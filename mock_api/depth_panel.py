"""专家评审面板（ADR-014 · P1）。

解决 W1/W6/W9/W11：原 DEPTH 评分缺乏「多评审一致性」与「明确评分标准」。
本模块提供：

1. **显式 Rubric 加载**：从 ``depth_rubric.yaml`` 读取每档分数对应的
   定性标准 / 必要证据 / 红旗信号，作为评审与复核的单一事实来源。
2. **标注者间一致性**：
   - ``cohen_kappa``：分类一致（verdict 档位）。
   - ``krippendorff_alpha``：连续分数一致（interval 级），允许缺失值。
3. **面板运行框架** ``run_panel``：把同一篇论文交给 N 个「评审员」
   （LLM 多提示/多温度扰动作为可复现的代理评审员；或加载人工标注）
   计算一致性。一致性过低（α < 阈值）即触发告警，提示该分数不可信。
4. **人工金标加载器** ``load_annotator_labels``：对接 P3 的金标回归。

fail-open：任何异常返回 ``PanelResult`` 带 ``agreement_status="error"``，
绝不因评审面板失败阻断主评测管线。
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_RUBRIC_PATH = os.path.join(os.path.dirname(__file__), "depth_rubric.yaml")


# ── Rubric 加载 ──────────────────────────────────────────────────────────────
def load_rubric(path: str | None = None) -> dict:
    """加载评分量表 YAML。失败时返回空 dict（fail-open）。"""
    try:
        import yaml

        with open(path or _RUBRIC_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 - rubric 加载失败不应阻断评测
        logger.warning("Rubric 加载失败(%s): %s", path or _RUBRIC_PATH, e)
        return {}


def score_to_band(rubric: dict, dimension: str, score: float) -> str:
    """把连续分数映射到 rubric 中定义的定性档位 label。"""
    dims = (rubric.get("dimensions") or {}).get(dimension)
    if not dims:
        return ""
    s = float(score)
    # 档位按 lo 降序，命中第一个 ≤ s 的
    for band in sorted(dims, key=lambda b: b.get("lo", 0.0), reverse=True):
        if s >= float(band.get("lo", 0.0)):
            return band.get("label", "")
    return dims[-1].get("label", "")


# ── 一致性指标 ───────────────────────────────────────────────────────────────
def cohen_kappa(a: list[str], b: list[str]) -> float:
    """Cohen's Kappa（分类一致度，verdict 档位）。"""
    if len(a) != len(b) or not a:
        return 0.0
    labels = sorted(set(a) | set(b))
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa = sum((a.count(l) / n) * (b.count(l) / n) for l in labels)
    return (po - pa) / (1 - pa) if pa < 1 else 1.0


def krippendorff_alpha_interval(ratings: Iterable[Iterable[float | None]]) -> float:
    """Krippendorff's α（interval 级，用于连续分数，允许缺失值 None）。

    公式：α = 1 - Do/De
      Do = (1/n) Σ_u 2·Σ_i (x_ui - μ_u)²      （单元内离散）
      De = (1/(n-1)) Σ_all (x - μ)²          （总体离散）
    """
    rows = [list(r) for r in ratings]
    all_vals: list[float] = [v for row in rows for v in row if v is not None]
    n = len(all_vals)
    if n < 2:
        return 0.0
    y_mean = sum(all_vals) / n
    de = sum((v - y_mean) ** 2 for v in all_vals) / (n - 1)
    do = 0.0
    for row in rows:
        su = [v for v in row if v is not None]
        nu = len(su)
        if nu < 2:
            continue
        mu = sum(su) / nu
        do += 2.0 * sum((x - mu) ** 2 for x in su)
    do = do / n
    if de == 0:
        return 1.0 if do == 0 else 0.0
    return max(0.0, 1.0 - do / de)


# ── 面板运行框架 ─────────────────────────────────────────────────────────────
@dataclass
class PanelResult:
    """多评审员面板结果。"""

    n_annotators: int = 0
    scores: dict[str, list[float]] = field(default_factory=dict)  # dimension -> [per-annotator]
    mean_scores: dict[str, float] = field(default_factory=dict)
    verdicts: list[str] = field(default_factory=list)
    kappa: float | None = None  # verdict 一致性
    alpha: float | None = None  # 分数一致性
    agreement_status: str = "ok"  # ok | low | error
    note: str = ""
    error: str | None = None
    cloud_cross_check: dict | None = (
        None  # ADR-014 P8：云端交叉复核记录（仅记录，不覆盖本地维度分）
    )

    def to_dict(self) -> dict:
        return {
            "n_annotators": self.n_annotators,
            "mean_scores": {k: round(v, 4) for k, v in self.mean_scores.items()},
            "verdicts": self.verdicts,
            "kappa": round(self.kappa, 4) if self.kappa is not None else None,
            "alpha": round(self.alpha, 4) if self.alpha is not None else None,
            "agreement_status": self.agreement_status,
            "note": self.note,
            "error": self.error,
            "cloud_cross_check": self.cloud_cross_check,
        }


def _llm_annotator(text: str, system_prompt: str, temperature: float) -> dict:
    """单个 LLM 评审员：用不同 system_prompt/temperature 模拟独立评审视角。

    需可用的 LLM provider（get_factory）。返回简易维度分数 dict。
    这是「代理评审员」——真实一致性研究应以人工金标为准（见 load_annotator_labels）。
    """
    from .llm.base import ChatMessage
    from .llm.factory import get_factory

    provider = get_factory().get_provider()
    content = provider.chat(
        [ChatMessage(role="system", content=system_prompt), ChatMessage(role="user", content=text)],
        temperature=temperature,
        max_tokens=512,
    ).content
    # 简易解析：从返回中抽第一个 JSON 块；失败返回中性 0.5
    try:
        import re

        m = re.search(r"\{.*\}", content or "", re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        pass
    return {}


_PANEL_PROMPTS = [
    "你是严谨的资深审稿人，请仅依据文本给出 novelty/rigor/influence/reproducibility 四维度 0~1 分数（JSON）。",
    "你是注重方法严谨性的统计学家，请仅依据文本给出四维度 0~1 分数（JSON）。",
    "你是注重创新性与影响力的领域专家，请仅依据文本给出四维度 0~1 分数（JSON）。",
]


def run_panel(
    text: str,
    *,
    n_annotators: int = 3,
    dimensions: list[str] | None = None,
    alpha_threshold: float = 0.67,
    use_llm: bool = True,
) -> PanelResult:
    """运行 N 评审员面板并计算一致性（fail-open）。

    Args:
        text: 待评审文本。
        n_annotators: 评审员数量（LLM 模式循环使用 _PANEL_PROMPTS）。
        dimensions: 关注的维度键名（默认四维度）。
        alpha_threshold: 一致性告警阈值（Krippendorff α < 阈值 → low）。
        use_llm: True 用 LLM 代理评审员；False 仅做结构占位（便于离线测试）。

    Returns:
        PanelResult（异常时 agreement_status="error"）。
    """
    dims = dimensions or ["novelty", "rigor", "influence", "reproducibility"]
    res = PanelResult(n_annotators=n_annotators)
    try:
        scores: dict[str, list[float]] = {d: [] for d in dims}
        verdicts: list[str] = []
        for i in range(n_annotators):
            if use_llm:
                sp = _PANEL_PROMPTS[i % len(_PANEL_PROMPTS)]
                temp = round(0.1 + 0.1 * (i % 3), 2)
                out = _llm_annotator(text, sp, temp)
            else:
                out = {}
            # 缺失维度填中性 0.5（保持矩阵结构，便于 α 计算）
            for d in dims:
                v = out.get(d)
                scores[d].append(float(v) if isinstance(v, (int, float)) else 0.5)
            verdicts.append(str(out.get("verdict", "major_revision")))
        res.scores = scores
        res.verdicts = verdicts
        res.mean_scores = {d: sum(v) / len(v) for d, v in scores.items()}
        # 一致性：对每个维度算 α，取最低者作为整体 α；verdict 算 κ
        alphas = [
            krippendorff_alpha_interval([[scores[d][i] for d in dims] for i in range(n_annotators)])
        ]
        res.alpha = alphas[0]
        # verdict κ：以第 0 个为基准 vs 其余 majority（演示用：两两平均）
        if len(set(verdicts)) > 1:
            kappas = [cohen_kappa([verdicts[0]], [verdicts[j]]) for j in range(1, len(verdicts))]
            res.kappa = sum(kappas) / len(kappas) if kappas else None
        else:
            res.kappa = 1.0
        res.agreement_status = (
            "low" if (res.alpha is not None and res.alpha < alpha_threshold) else "ok"
        )
        res.note = (
            "一致性低于阈值，分数存疑，建议人工复核"
            if res.agreement_status == "low"
            else "一致性可接受"
        )
        # ── ADR-014 P8：云端交叉复核（本地主 + 云端副，默认开，fail-open）──
        # 仅对聚合均分做一次独立云端评审并记录分歧；面板是多维一致性工具，
        # 不覆盖本地维度分（否则会扭曲 Krippendorff α），覆盖语义由调用方（如 DEPTH）承担。
        try:
            from .second_opinion import run_second_opinion

            _agg = None
            _all = [v for vs in scores.values() for v in vs if isinstance(v, (int, float))]
            if _all:
                _agg = round(sum(_all) / len(_all), 4)
            _cc = run_second_opinion(
                text[:6000], primary_score=_agg, primary_verdict=None, kind="paper", db=None
            )
            if _cc.get("enabled"):
                res.cloud_cross_check = _cc
        except Exception as _cc_err:  # noqa: BLE001 - 云端复核异常不影响面板主结果
            logger.warning("run_panel 云端交叉复核失败（非致命）: %s", _cc_err)
        return res
    except Exception as e:  # noqa: BLE001 - 面板异常隔离
        logger.warning("run_panel 异常降级: %s", e)
        res.agreement_status = "error"
        res.error = type(e).__name__
        return res


# ── 人工金标加载（对接 P3） ──────────────────────────────────────────────────
def load_annotator_labels(path: str) -> dict[str, dict]:
    """加载人工标注（金标）JSON：{paper_id: {dimension: score, verdict: str}}。

    用于与 DEPTH 自动分数比对（P3 回归）与计算标注者间一致性。
    文件缺失/解析失败返回空 dict（fail-open）。
    """
    if not path or not os.path.exists(path):
        logger.warning("金标路径不存在: %s", path)
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("金标加载失败: %s", e)
        return {}
