"""DEPTH v4.2 专家校准集回归框架。

设计约束：
- 默认空跑：CALIBRATION_SET_PATH 为空时返回空集，所有重标定函数原值返回。
- 向后兼容：无校准数据时行为与当前 v4.2 完全一致。
- 纯本地：不依赖外部模型，仅做统计回归。
- SCORE_OFFSET 采用 functools.lru_cache 装饰器模式，env 变化重启后即时生效。
"""

from __future__ import annotations

import csv
import functools
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from statistics import mean
from typing import Any

from .utils.math_utils import clamp_float

logger = logging.getLogger(__name__)

# ── 分数偏移校正层（DEPTH 校准偏移） ──
# 校准实测（blind_review_comparison_2026-07-24）：DEPTH 相对人工盲评系统性偏高
# +0.09~+0.12。通过此偏移层对最终分做全局平移，使 verdict 与人工盲评对齐。
# 优先级：显式 PAPERFORGE_DEPTH_SCORE_OFFSET > 金标驱动(PAPERFORGE_DEPTH_GOLD_OFFSET_PATH)
#         > 校准集自动估计(calib_offset.json) > 0.0(关闭)
_AUTO_OFFSET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "calib_papers", "runs", "calib_offset.json"
)
_AUTO_OFFSET_ENABLED = os.getenv("DEPTH_AUTO_OFFSET", "0").lower() in ("1", "true", "yes", "on")
# ADR-014 P5：金标驱动偏移。指向由真实盲评金标数据集（calib_set_20.json + calib_pool_415.json
# 经 scripts/calibration/recompute_gold_offset.py 离线计算）生成的 gold_offset.json，
# 替代此前仅记录偏移值、来源不可审计的 -0.09（治 W1 病根）。
# gold_offset.json 含 recommended_offset（20 样本数据最优，过拟合、不采用）与
# adopted_offset（用户采用的稳健值 -0.09，三方印证），_resolved_offset 优先 adopted_offset。
# 注意：必须在每次调用时动态读取环境变量（而非模块加载时捕获），否则运行时 setenv 不生效。
_GOLD_OFFSET_ENV = "PAPERFORGE_DEPTH_GOLD_OFFSET_PATH"


def _resolved_offset() -> float:
    """解析生效的偏移量（默认 0.0 = 关闭，向后兼容）。"""
    env = os.getenv("PAPERFORGE_DEPTH_SCORE_OFFSET")
    if env not in (None, ""):
        try:
            return float(env)
        except ValueError:  # noqa: BLE001 - calibration config - 非法 env 兜底
            logger.warning("PAPERFORGE_DEPTH_SCORE_OFFSET 解析失败: %s，回退 0.0", env)
            return 0.0
    # P5：优先使用金标驱动偏移（来源诚实、可审计）。优先 adopted_offset（稳健值），
    # 其次 recommended_offset（数据最优，可能过拟合），其次 offset 兼容旧字段。
    gold_path = os.getenv(_GOLD_OFFSET_ENV)
    if gold_path and os.path.exists(gold_path):
        try:
            with open(gold_path, encoding="utf-8") as f:
                _gold_cfg = json.load(f)
            v = float(
                _gold_cfg.get(
                    "adopted_offset",
                    _gold_cfg.get("recommended_offset", _gold_cfg.get("offset", 0.0)),
                )
            )
            logger.info("分数偏移层启用(金标驱动): offset=%.3f", v)
            return v
        except Exception as e:  # noqa: BLE001 - calibration config - 文件读取兜底
            logger.warning("金标偏移文件读取失败: %s，回退校准自动估计", e)
    if _AUTO_OFFSET_ENABLED and os.path.exists(_AUTO_OFFSET_PATH):
        try:
            with open(_AUTO_OFFSET_PATH, encoding="utf-8") as f:
                v = float(json.load(f).get("offset", 0.0))
            logger.info("分数偏移层启用(校准自动): offset=%.3f", v)
            return v
        except Exception as e:  # noqa: BLE001 - calibration config - 文件读取兜底
            logger.warning("calib_offset.json 读取失败: %s，回退 0.0", e)
    return 0.0


@functools.lru_cache(maxsize=1)
def get_score_offset() -> float:
    """获取当前生效的全局分数偏移量（lru_cache 缓存，进程内仅计算一次）。

    相比模块级全局变量 SCORE_OFFSET = _resolved_offset()，lru_cache 模式：
    - 首次调用时执行 env 解析（与旧行为一致）
    - reset_score_offset() 清除缓存后重新从 env 读取（支持运行时热更新）
    - 避免模块导入时 env 未就绪导致的 0.0 静默回退。
    """
    return _resolved_offset()


def reset_score_offset() -> None:
    """清除全局偏移缓存，下次调用 get_score_offset() 重新从 env 解析。"""
    get_score_offset.cache_clear()


# 向后兼容别名：模块级 SCORE_OFFSET = get_score_offset 惰性调用点
# 新代码请直接使用 get_score_offset()
SCORE_OFFSET = get_score_offset()


# ── 分档偏移表（按 import 源 / 年代自适应） ──
# PeerRead 校准实证：DEPTH 绝对分标定**不跨语料鲁棒**
#   - 库内 2020–2026 现代 arXiv 论文：DEPTH 相对人工盲评系统性偏高 ~+0.09 → 需 -0.09（default）。
#   - PeerRead 2007–2017（4 会场）N=198 真实人类金标重扫（2026-08-09，
#     deliverables/gold/peerread_offset_scan_t06.json）：**生产阈值 accept≥0.6** 下
#     最优偏移 -0.02（κ=0.414 / 一致率 70.7%），采纳稳健圆整值 0.0（κ=0.364 / 68.2%）。
# 单一全局偏移被证伪；改为按 (source, year) 分档查表。
#
# 【生产规则 P0】DEFAULT_OFFSET_TABLE 已内置为生产生效值（无需 env 即可应用）：
#   "default"  : -0.09   # 库内现代论文校准（保持）
#   "peerread" :  0.00   # 2026-08-09 重扫：旧值 +0.18 是 0.80 阈值时代（历史扫描
#                        # 建议在 0.8 阈值下 κ 最优）的产物；阈值降到 0.6 后未再验证，
#                        # 实测 +0.18 在 0.6 阈值下过度接受（56.6%），故移除。
# 任何新来源（SNOR / ARR-DC / 其他）必须单独跑基线 + 偏移扫描，禁止复用上述任一值。
#
# key 解析优先级（高→低）：
#   "source:YEAR"  >  "source:ERA"(legacy/modern)  >  "source"  >  "ERA"  >  "default"
# env: PAPERFORGE_DEPTH_OFFSET_TABLE = JSON，会【叠加】到 DEFAULT_OFFSET_TABLE（env 同键覆盖），
#      便于一次性实验仅覆盖某个 source 而不丢失 default 规则。
_LEGACY_YEAR_CUTOFF = int(os.getenv("PAPERFORGE_DEPTH_LEGACY_YEAR_CUTOFF", "2018"))

# 生产生效的默认分档偏移表（P0 规则：无需 env 即应用）
DEFAULT_OFFSET_TABLE: dict[str, float] = {
    "default": -0.09,
    "peerread": 0.0,
}


def _parse_offset_table() -> dict:
    """解析分档偏移表：env【叠加】到 DEFAULT_OFFSET_TABLE（env 同键覆盖默认）。

    返回始终非 None 的合并表 —— 因此 DEFAULT_OFFSET_TABLE 即生产默认生效规则，
    无需任何 env 即可应用 default / peerread 偏移。仅当 env 解析失败时回退到默认表。
    """
    base = dict(DEFAULT_OFFSET_TABLE)
    raw = os.getenv("PAPERFORGE_DEPTH_OFFSET_TABLE")
    if not raw:
        return base
    try:
        env_tbl = json.loads(raw)
        if isinstance(env_tbl, dict):
            base.update({k: float(v) for k, v in env_tbl.items()})
            return base
    except Exception:  # noqa: BLE001 - calibration config
        logger.warning("PAPERFORGE_DEPTH_OFFSET_TABLE 解析失败，回退默认表")
    return base


_OFFSET_TABLE = _parse_offset_table()


def resolve_context_offset(
    source: str | None = None,
    year: int | None = None,
    table: dict | None = None,
) -> float | None:
    """按 (source, year) 解析分档偏移。

    默认表（_OFFSET_TABLE = DEFAULT_OFFSET_TABLE 叠加 env）始终非 None 且含 "default" 键，
    因此常规调用总会命中 "default"（= -0.09）或某个具体 source 键，不会返回 None。
    仅当传入自定义 table 且不含任何匹配键时返回 None（调用方回退全局 SCORE_OFFSET）。
    """
    table = table if table is not None else _OFFSET_TABLE
    if not table:
        return None
    src = (source or "").lower().strip()
    era = None
    if year is not None:
        try:
            era = "legacy" if int(year) < _LEGACY_YEAR_CUTOFF else "modern"
        except (TypeError, ValueError):
            era = None
    cands: list[str] = []
    if src and year is not None:
        cands.append(f"{src}:{year}")
    if src and era:
        cands.append(f"{src}:{era}")
    if src:
        cands.append(src)
    if era:
        cands.append(era)
    cands.append("default")
    for k in cands:
        if k in table:
            try:
                return float(table[k])
            except (TypeError, ValueError):
                continue
    return None


@dataclass
class CalibrationSample:
    """单条专家校准样本。"""

    paper_id: str
    text_hash: str
    expert_scores: dict[str, float] = field(default_factory=dict)
    expert_verdict: str = "major_revision"
    weight: float = 1.0

    def __post_init__(self):
        self.expert_verdict = str(self.expert_verdict).lower().replace(" ", "_")
        valid = {"accept", "minor_revision", "major_revision", "reject"}
        if self.expert_verdict not in valid:
            self.expert_verdict = "major_revision"


@dataclass
class CalibrationResult:
    """校准回归结果。"""

    weights: dict[str, float] | None = None
    accept_threshold: float | None = None
    reject_threshold: float | None = None
    spearman_rho: float | None = None
    cohen_kappa: float | None = None


def _safe_json_load(path: str) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get("samples", [])
    return list(data) if isinstance(data, list) else []


def _safe_csv_load(path: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            samples.append(dict(row))
    return samples


def load_calibration_set(path: str | None = None) -> list[CalibrationSample]:
    """加载专家校准集。路径为空或文件不存在时返回空列表。"""
    from .settings import get_settings

    path = path or get_settings().calibration_set_path or ""
    if not path:
        return []
    if not os.path.exists(path):
        logger.warning("校准集路径不存在: %s，跳过回归", path)
        return []

    try:
        if path.lower().endswith(".csv"):
            raw = _safe_csv_load(path)
        else:
            raw = _safe_json_load(path)
    except Exception as e:  # noqa: BLE001 - calibration I/O - 文件缺失/解析失败需兜底
        logger.warning("校准集加载失败: %s，跳过回归。错误: %s", path, e)
        return []

    samples: list[CalibrationSample] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        paper_id = str(item.get("paper_id", item.get("id", "")))
        if not paper_id:
            continue
        scores = item.get("expert_scores") or {}
        if isinstance(scores, str):
            try:
                scores = json.loads(scores)
            except Exception:  # noqa: BLE001 - calibration I/O - 文件缺失/解析失败需兜底
                scores = {}
        samples.append(
            CalibrationSample(
                paper_id=paper_id,
                text_hash=str(item.get("text_hash", "")),
                expert_scores={
                    k: float(v) for k, v in scores.items() if isinstance(v, (int, float, str))
                },
                expert_verdict=str(item.get("expert_verdict", "major_revision")),
                weight=float(item.get("weight", 1.0)),
            )
        )

    logger.info("加载校准集: %s，共 %d 条样本", path, len(samples))
    return samples


def _spearman_rank_correlation(x: list[float], y: list[float]) -> float:
    """计算 Spearman 秩相关系数。"""
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    try:
        n = len(x)
        rx = sorted(range(n), key=lambda i: x[i])
        ry = sorted(range(n), key=lambda i: y[i])
        rank_x = [0.0] * n
        rank_y = [0.0] * n
        for r, i in enumerate(rx):
            rank_x[i] = float(r + 1)
        for r, i in enumerate(ry):
            rank_y[i] = float(r + 1)
        mean_x = mean(rank_x)
        mean_y = mean(rank_y)
        num = sum((rank_x[i] - mean_x) * (rank_y[i] - mean_y) for i in range(n))
        den = (
            sum((v - mean_x) ** 2 for v in rank_x) * sum((v - mean_y) ** 2 for v in rank_y)
        ) ** 0.5
        return num / den if den else 0.0
    except Exception:  # noqa: BLE001 - calibration I/O - 文件缺失/解析失败需兜底
        return 0.0


def spearman_corr(pred: list[float], truth: list[float]) -> float:
    """公开接口：Spearman 相关性。"""
    return _spearman_rank_correlation(pred, truth)


def _cohen_kappa(a: list[str], b: list[str]) -> float:
    """计算 Cohen's Kappa。"""
    if len(a) != len(b) or len(a) == 0:
        return 0.0
    labels = sorted(set(a) | set(b))
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa = sum((a.count(l) / n) * (b.count(l) / n) for l in labels)
    return (po - pa) / (1 - pa) if pa < 1 else 1.0


def reweight_by_calibration(
    base_weights: dict[str, float],
    samples: list[CalibrationSample],
    score_fn: Callable[..., Any],
) -> dict[str, float]:
    """基于校准集重标定权重。空样本时原样返回。

    Args:
        base_weights: 当前权重字典，如 {"novelty": 0.25, ...}。
        samples: 校准样本。
        score_fn: 给定权重 -> 预测分列表，与 samples 顺序一致。
    """
    if not samples:
        return dict(base_weights)

    truth = [s.expert_scores.get("final", 0.5) for s in samples]
    best = dict(base_weights)
    best_rho = _spearman_rank_correlation(score_fn(base_weights), truth)

    # 轻量网格搜索：每个维度 ±0.05，步长 0.05，保持和=1
    keys = list(base_weights.keys())
    if len(keys) < 2:
        return best

    candidates: list[dict[str, float]] = [best]
    for key in keys:
        for delta in (-0.05, 0.05):
            w = dict(best)
            w[key] = clamp_float(w[key] + delta, 0.05, 0.9)
            # 归一化保持和=1
            total = sum(w.values())
            if total > 0:
                w = {k: v / total for k, v in w.items()}
                candidates.append(w)

    for w in candidates:
        try:
            pred = score_fn(w)
            rho = _spearman_rank_correlation(pred, truth)
            if rho > best_rho:
                best_rho = rho
                best = w
        except Exception:  # noqa: BLE001 - calibration I/O - 文件缺失/解析失败需兜底
            continue

    logger.info("校准重标定完成: rho=%.3f, weights=%s", best_rho, best)
    return best


# 裁决梯子三档下界（不随校准浮动）：reject < minor < accept。
# 三者必须严格递增，否则中间档会被相邻档吞掉。
#
# 历史 bug：校准/配置曾把 accept 压到 0.6（< minor 0.7），导致
# `elif score >= minor` 这一档被 accept 完全吞掉 —— minor_revision 档架空，
# 0.6~0.7 的论文被直接判 accept（红队造假论文 NSGT 0.618 即因此误判 accept）。
VERDICT_MINOR_FLOOR = 0.65  # Ornith-1.5-9B: 从 0.7 降到 0.65，匹配新模型分数分布
# accept 档下界：0.75（全量 669 篇校准，85th percentile = 0.765，accept ≥ 0.77 → ~15%）
VERDICT_ACCEPT_FLOOR = 0.75


def calibrate_verdict_thresholds(
    samples: list[CalibrationSample],
    score_fn: Callable[..., Any],
) -> CalibrationResult:
    """基于校准集重标定 verdict 阈值。空样本时返回 None。"""
    if not samples:
        return CalibrationResult()

    scores = score_fn()
    truth = [s.expert_verdict for s in samples]

    # accept 阈值搜索区间须 ≥ VERDICT_ACCEPT_FLOOR（严格高于 minor 档），
    # 否则 minor_revision 档被架空；reject 在 [0.1, 0.6] 搜索。
    best_kappa = -1.0
    best_acc, best_rej = 0.8, 0.5
    for acc in (round(x, 2) for x in [0.8, 0.85, 0.9, 0.95]):
        for rej in (
            round(x, 2) for x in [0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6]
        ):
            pred = []
            for s in scores:
                if s >= acc:
                    pred.append("accept")
                elif s >= VERDICT_MINOR_FLOOR:
                    pred.append("minor_revision")
                elif s >= rej:
                    pred.append("major_revision")
                else:
                    pred.append("reject")
            kappa = _cohen_kappa(pred, truth)
            if kappa > best_kappa:
                best_kappa = kappa
                best_acc, best_rej = acc, rej

    logger.info(
        "verdict 阈值校准完成: accept=%.2f reject=%.2f kappa=%.3f", best_acc, best_rej, best_kappa
    )
    return CalibrationResult(
        accept_threshold=best_acc,
        reject_threshold=best_rej,
        cohen_kappa=best_kappa,
    )


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def apply_score_offset(score: float, offset: float | None = None) -> float:
    """对 DEPTH 最终分施加全局偏移（默认用 SCORE_OFFSET），clamp 到 [0,1]。

    校准实证：DEPTH 相对人工盲评系统性偏高 +0.09~+0.12，
    应用 offset=-0.09 可把 verdict Cohen κ 从 0.189 抬升到 0.375。
    """
    off = offset if offset is not None else get_score_offset()
    if off == 0.0:
        return clamp_float(score, 0.0, 1.0)
    return clamp_float(score + off, 0.0, 1.0)


# 顶刊封顶参数（可经 env 覆盖，便于校准复现）
OFFSET_CAP_THRESHOLD = float(os.getenv("PAPERFORGE_DEPTH_CAP_THRESHOLD", "0.85"))
OFFSET_CAP_TAPER = float(os.getenv("PAPERFORGE_DEPTH_CAP_TAPER", "0.15"))


def apply_top_tier_cap(
    score: float,
    offset: float | None = None,
    threshold: float | None = None,
    taper: float | None = None,
) -> float:
    """顶刊封顶：对 >= threshold 的高分做偏移 taper，保护真顶刊不被偏移误杀。

    设计：DEPTH 系统性偏高主要在**中段**（0.5~0.85），而真顶刊（>=0.85）的
    高分多属合理。裸全局 -0.09 会把 53 篇 accept 压到 4 篇（含真奠基论文误降级）。
    本函数让偏移量随分数升高而**线性收敛到 0**：
      - score < threshold：施加完整 offset（中段照常校正）
      - score in [threshold, 1.0]：effective_offset = offset * (1-score)/(1-threshold)
        即 threshold 处用完整 offset，1.0 处归零（真满分不被压低）
    仅对负向(offset<0)生效；正向偏移或 offset=0 原样返回（关闭/上修场景）。
    """
    off = offset if offset is not None else get_score_offset()
    if off >= 0.0:
        return clamp_float(score + off, 0.0, 1.0)
    thr = OFFSET_CAP_THRESHOLD if threshold is None else threshold
    tap = OFFSET_CAP_TAPER if taper is None else taper
    if score < thr or tap <= 0:
        return clamp_float(score + off, 0.0, 1.0)
    t = clamp_float((1.0 - score) / (1.0 - thr), 0.0, 1.0)
    eff = off * t
    return clamp_float(score + eff, 0.0, 1.0)


def _extract_source_year(paper: Any) -> tuple[str | None, int | None]:
    """从 paper 上下文抽取 (source, year)，兼容 dict（键访问）与对象（属性访问）。"""
    if isinstance(paper, dict):
        src = paper.get("source") or paper.get("source_id")
        yr = paper.get("year")
    else:
        src = getattr(paper, "source", None) or getattr(paper, "source_id", None)
        yr = getattr(paper, "year", None)
    if yr is not None:
        try:
            yr = int(yr)
        except (TypeError, ValueError):
            yr = None
    return (src, yr)


def correct_final_score(
    score: float,
    offset: float | None = None,
    use_cap: bool = True,
    threshold: float | None = None,
    taper: float | None = None,
    paper: Any | None = None,
) -> float:
    """DEPTH 最终分校正入口（偏移 + 可选顶刊封顶）。

    这是实际审稿管线应调用的唯一校正函数：默认开启顶刊封顶，
    消除裸全局偏移对高分端的过度下压（高端误杀）。offset=0 时恒等返回。

    新增 paper 参数（分档偏移，向后兼容）：传入含 source/year 的对象/字典时，
    优先使用分档偏移表（PAPERFORGE_DEPTH_OFFSET_TABLE）解析的偏移；未命中则回退
    全局 SCORE_OFFSET。paper=None 时行为与旧版完全一致——因此运行中已加载本模块的
    进程不受此改动影响。
    """
    if paper is not None:
        src, yr = _extract_source_year(paper)
        so = resolve_context_offset(src, yr)
        if so is not None:
            offset = so
    if use_cap:
        return apply_top_tier_cap(score, offset, threshold, taper)
    return apply_score_offset(score, offset)


def offset_corrected_verdict(
    score: float,
    offset: float | None = None,
    accept: float = 0.78,  # Ornith: 从 0.8 降到 0.78
    reject: float = 0.48,  # Ornith: 从 0.5 降到 0.48（金字塔分布）
) -> str:
    """施加偏移后按原阈值推导 verdict（分数层口径，不含否决层）。"""
    s = apply_score_offset(score, offset)
    # 三档阈值：accept(0.78) > minor(0.65) > major(0.45) > reject
    accept = max(accept, VERDICT_ACCEPT_FLOOR)
    minor = VERDICT_MINOR_FLOOR
    reject = min(reject, minor - 0.05)
    if s >= accept:
        return "accept"
    if s >= minor:
        return "minor_revision"
    if s >= reject:
        return "major_revision"
    return "reject"


def auto_offset_from_calibration(
    samples: list[CalibrationSample],
    score_fn: Callable[..., list[float]],
    grid: list[float] | None = None,
) -> tuple[float, float]:
    """基于校准集搜索最优偏移 δ 使 verdict Cohen κ 最大。

    Returns:
        (best_offset, best_kappa)
    """
    if not samples:
        return 0.0, 0.0
    truth = [s.expert_verdict for s in samples]
    base = list(score_fn())
    if grid is None:
        grid = [round(x, 2) for x in (i * 0.01 for i in range(-30, 31))]  # -0.30 ~ +0.30
    best_k, best_o = -1.0, 0.0
    for off in grid:
        pred = [offset_corrected_verdict(s, off) for s in base]
        k = _cohen_kappa(pred, truth)
        if k > best_k:
            best_k, best_o = k, off
    logger.info("偏移自校准: best_offset=%.2f kappa=%.3f", best_o, best_k)
    return best_o, best_k


# ── 运行时自适应偏移（实验性，P2）──
# 设计动机：当语料来源不确定或分布随时间漂移时，固定分档偏移可能失准。轻量机制：
# 依据当前批次 calibrated_score 的某分位（默认中位数 q=0.5），与历史参考分布同分位对齐，
# 计算 delta = ref_q - batch_q，使整体分布落在历史参考分布上（百分位匹配）。
#
# ⚠️ 当前【未接入】correct_final_score 生产路径 —— 仅作为库函数提供；
#    启用前需谨慎验证（如离线回放各语料、对比固定偏移的 κ/acc 是否提升）。
#    可通过 env PAPERFORGE_DEPTH_ADAPTIVE_OFFSET=1 在调用方显式开启（本模块不主动读取）。
ADAPTIVE_OFFSET_ENABLED = os.getenv("PAPERFORGE_DEPTH_ADAPTIVE_OFFSET", "0").lower() in (
    "1",
    "true",
    "yes",
    "on",
)


def _percentile(sorted_vals: list[float], q: float) -> float:
    """在已排序列表上线性插值取 q 分位（q∈[0,1]）。"""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * max(0.0, min(1.0, q))
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def adaptive_offset_from_reference(
    batch_scores: list[float],
    reference_scores: list[float] | None = None,
    reference_median: float | None = None,
    q: float = 0.5,
) -> float:
    """计算使 batch 分布对齐参考分布的偏移量 delta（百分位匹配）。

    delta = 分位_q(reference) - 分位_q(batch)
    优先使用 reference_median（已算好的参考分位），否则由 reference_scores 现场计算。
    返回 delta（物理范围不限，调用方应 clamp / 审核阈值后再用于 correct_final_score）。
    """
    if not batch_scores:
        return 0.0
    batch_q = _percentile(sorted(batch_scores), q)
    if reference_median is None:
        if reference_scores:
            reference_median = _percentile(sorted(reference_scores), q)
        else:
            return 0.0
    return reference_median - batch_q


def reference_stats_from_scores(scores: list[float], q: float = 0.5) -> dict:
    """从一批历史 calibrated_score 抽取参考统计（n / 中位数 / 均值 / 指定分位）。"""
    if not scores:
        return {}
    s = sorted(scores)
    return {
        "n": len(s),
        "median": _percentile(s, 0.5),
        "mean": sum(s) / len(s),
        "q": q,
        "p": _percentile(s, q),
    }


# ── ADR-014 P5：金标驱动偏移（诚实化偏移来源） ────────────────────────────────
# 关键澄清（纠错 2026-08-06）：DEFAULT_OFFSET_TABLE 的 -0.09 并非「凭空猜的魔数」，
# 而是来自本仓真实盲评金标数据集 calib_papers/runs/calib_my_review.json（=calib_set_20.json
# 的 CalibrationSample 格式）：从 415 篇分层抽样 20 篇，4 个独立评审 agent 盲评（只读全文、
# 不看 DEPTH 分），再用 auto_offset_from_calibration 穷举偏移、最大化 DEPTH verdict 与「我的
# verdict」的 Cohen κ（0.189→0.375）得出。属数据驱动校准，地基不悬空。
# 唯一残余局限（见 blind_review_comparison_2026-07-24.md §9）：评审 agent 仍是 LLM 判断，
# 非人类审稿人；故偏移是「LLM-vs-LLM 交叉验证」得来，绝对分仍需人类标定。
# 本组函数把偏移推导显式建立在上述金标之上，使其来源可审计、可重算：
#   1. gold_samples_to_calibration(gold)  把金标 JSON 转 CalibrationSample（兼容真实/合成两种格式）
#   2. recommend_offset_from_gold(...)    在金标上搜索最大化 Cohen κ 的偏移 δ
#   3. 输出 gold_offset.json，由 PAPERFORGE_DEPTH_GOLD_OFFSET_PATH 指向即可生效（见 _resolved_offset）
# 复算脚本：scripts/calibration/recompute_gold_offset.py（直接吃真实数据集）。
# 失败/缺失时全部回退到原函数，向后兼容。

_VALID_VERDICTS = {"accept", "minor_revision", "major_revision", "reject"}


def gold_samples_to_calibration(gold: dict | list) -> list[CalibrationSample]:
    """把金标转为 CalibrationSample 列表（兼容两种格式）。

    格式 A —— 本仓真实盲评金标 calib_set_20.json（CalibrationSample 原生格式）：
        [{"paper_id", "text_hash", "expert_scores": {"final": ...}, "expert_verdict", "weight"}, ...]
    格式 B —— 简化 schema 模板 mock_api/depth_gold.json：
        {"samples": [{"paper_id", "scores": {"calibrated"/"final"}, "verdict"}, ...]}
    """
    if isinstance(gold, list):
        items = gold
    elif isinstance(gold, dict):
        items = gold.get("samples", [])
    else:
        return []
    samples: list[CalibrationSample] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("paper_id", item.get("id", "")))
        if not pid:
            continue
        if "expert_scores" in item and isinstance(item["expert_scores"], dict):
            # 格式 A：真实盲评金标
            sc = item["expert_scores"]
            target = float(sc.get("final", sc.get("calibrated", 0.5)))
            verdict = str(item.get("expert_verdict", "major_revision"))
            weight = float(item.get("weight", 1.0))
            text_hash = str(item.get("text_hash", ""))
        else:
            # 格式 B：合成 schema 模板
            sc = item.get("scores") or {}
            target = float(
                sc.get("calibrated") or sc.get("final") or item.get("calibrated_score") or 0.5
            )
            verdict = str(item.get("verdict", "major_revision"))
            weight = 1.0
            text_hash = str(item.get("text_hash", ""))
        verdict = verdict.lower().replace(" ", "_")
        if verdict not in _VALID_VERDICTS:
            verdict = "major_revision"
        samples.append(
            CalibrationSample(
                paper_id=pid,
                text_hash=text_hash,
                expert_scores={"final": target},
                expert_verdict=verdict,
                weight=weight,
            )
        )
    return samples


def recommend_offset_from_gold(
    gold_path: str,
    score_fn: Callable[..., list[float]],
    out_path: str | None = None,
    grid: list[float] | None = None,
) -> dict:
    """在真实金标上搜索使 verdict Cohen κ 最大的偏移 δ，并落盘 gold_offset.json。

    Args:
        gold_path: 金标 JSON 路径（mock_api/depth_gold.json 格式）。
        score_fn: 无参调用返回「基线 calibrated_score 列表」（与金标样本顺序一致）。
        out_path: 输出路径；默认写到 gold_path 同目录的 gold_offset.json。
        grid: 候选偏移网格，默认 -0.30 ~ +0.30 步长 0.01。

    Returns:
        {"recommended_offset": float, "kappa": float, "n_samples": int, "out_path": str}
    """
    if not os.path.exists(gold_path):
        logger.warning("金标文件不存在: %s", gold_path)
        return {"recommended_offset": 0.0, "kappa": 0.0, "n_samples": 0, "out_path": ""}
    with open(gold_path, encoding="utf-8") as f:
        gold = json.load(f)
    samples = gold_samples_to_calibration(gold)
    if not samples:
        return {"recommended_offset": 0.0, "kappa": 0.0, "n_samples": 0, "out_path": ""}
    offset, kappa = auto_offset_from_calibration(samples, score_fn, grid=grid)
    out_path = out_path or os.path.join(os.path.dirname(gold_path), "gold_offset.json")
    payload = {
        "recommended_offset": round(offset, 3),
        "kappa": round(kappa, 4),
        "n_samples": len(samples),
        "source": "gold_driven_calibration",
        "generated_by": "recommend_offset_from_gold",
    }
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info("金标驱动偏移已落盘: %s (offset=%.3f, kappa=%.3f)", out_path, offset, kappa)
    except Exception as e:  # noqa: BLE001 - calibration I/O
        logger.warning("gold_offset.json 写入失败: %s", e)
    return {
        "recommended_offset": offset,
        "kappa": kappa,
        "n_samples": len(samples),
        "out_path": out_path,
    }
