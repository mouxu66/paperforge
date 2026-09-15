"""双模型交叉复核（Second Opinion）—— ADR-014 · P8。

背景（详见 docs/improving-review-rigor.md §漏洞 C）：
  本地 Ornstein-V2 单模型意见不可当"终审"。多 AI 对比实验
  （deliverables/qwen_vs_others_compare_matched_2026-08-07.md）显示：
  - 与 ChatGPT 存在真实分歧 ~0.10（尤其 ua/es 维度，r≈0.59）
  - 与混元排序不相关（r=0.08）——各打各的
  - 与 DeepSeek 排序强一致（r≈0.81）
  → 任何单模型打分都需要第二个独立模型的交叉印证。

设计：
  - 环境门控：PAPERFORGE_SECOND_OPINION=1 开启（默认关闭，向后兼容）。
  - 第二评审员：从 llm_configs 表里挑一个「与当前主 provider 不同」的启用配置
    （即云端模型如 GLM/DeepSeek/ChatGPT，或另一台本地端点）。找不到则跳过。
  - 轻量 prompt：只对文本头尾做一次独立评分，输出 score + verdict + reason。
  - 分歧判定：|Δscore| ≥ PAPERFORGE_SECOND_OPINION_THRESHOLD（默认 0.15）
    或 verdict 语义不一致 → flag=disagreement，建议 needs_human_review。
- fail-open：任何异常（未启用 / 无第二 provider / 超时 / 解析失败）
  一律返回 enabled=False 的空结果，绝不改变主评审分数与 verdict。
- 本地失败兜底（rescue）：当主评审未产出有效分数（primary_score 为 None，
  通常是本地模型 JSON 解析失败 / 空返回）且云端第二评审有效时，`run_second_opinion`
  以云端结果直接兜底（flag="rescued"），让该篇评审以 completed 收尾而非无结果。
  调用方（如 DEPTH reflection 路径）可在本地解析失败时优先尝试 rescue 再决定是否判失败。
"""

from __future__ import annotations

import logging
import random
import re
from typing import Any

from .llm import ChatMessage, get_factory
from .settings import get_settings

logger = logging.getLogger(__name__)

# ── 常量 ───────────────────────────────────────────────────────────────
_MAX_INPUT_CHARS = 6000  # 第二评审员只读文本头（摘要+引言足够独立判断）# ── L1 模式：条件触发 + Ridge 融合（ADR-017 融合实验验证）──
# 500 篇 PeerRead 分层抽样 + 双模型并行打分实验。
# Ridge alpha=5 拟合于训练集（345 篇），测试集 AUC=0.786（本地 0.676 → +0.110）。
# 仅在本地分数落入 [0.60, 0.75] 区间时调用云端，其余直接采信本地。
# 软过渡带 [0.58, 0.77]：边界处线性混合，避免悬崖效应。
# Min-Max 重映射：Ridge 原始分 [0.07, 0.88] → [0, 1]，与本地分量纲对齐。
_RIDGE_W_LOCAL = 0.5226
_RIDGE_W_CLOUD = 0.3821
_RIDGE_INTERCEPT = 0.0
_RIDGE_MIN = 0.0747  # 训练集 ridge 原始分最小值
_RIDGE_MAX = 0.8787  # 训练集 ridge 原始分最大值
_TRIGGER_LOW = 0.60
_TRIGGER_HIGH = 0.75
_TRIGGER_MARGIN = 0.02

# ── P2 影子哨兵（shadow sentry，独立于 override / rescue 的第三态）──
# 设计：本地主评审高度肯定（>=SENTRY_LOCAL_HIGH）但云端第二评审强烈反对
# （<SENTRY_CLOUD_LOW）时，标记 needs_human_review 仅作「建议」——
# 不覆盖本地分、不自动转人工、不阻断流程（影子模式，默认开）。
# 阈值 0.5/0.7 为初始值，后续按全库分布调整（见 dry-run 统计）。
SENTRY_CLOUD_LOW = 0.5  # 云端强烈反对的下限
SENTRY_LOCAL_HIGH = 0.7  # 本地高度肯定的下限

_PAPER_PROMPT = """你是一位独立的期刊审稿人，正在对一篇论文做匿名评审。
以下是论文的摘要与引言（节选）。请【独立】给出你的判断，不要参考任何外部评分。

论文：
{text}

【要求】
- 综合质量分：综合考察创新性、严谨性、影响力、可复现性后给出 0~1 的一个小数。
- verdict 只能是 accept / minor_revision / major_revision / reject 之一。
- 请严格按以下格式输出（每行一个字段，key: value）：
score: <0~1 的小数，需根据论文质量在全程分布，不要集中在某一值>
verdict: major_revision
reason: 一句话理由，不超过60字"""

_REPORT_PROMPT = """你是一位独立的阅读笔记评审专家，正在评审一篇"读后感/感悟/复现报告"。
以下是报告正文（节选）。请【独立】给出你的判断，不要参考任何外部评分。

报告：
{text}

【要求】
- 综合质量分：综合理解准确性、分析深度、创新见解、证据支撑后给出 0~1 的一个小数。
- verdict 只能是 well_done / needs_evidence / needs_depth / rewrite_required 之一。
- 请严格按以下格式输出（每行一个字段，key: value）：
score: <0~1 的小数，需根据报告质量在全程分布，不要集中在某一值>
verdict: needs_depth
reason: 一句话理由，不超过60字"""

# ── 语义分歧判定：verdict 是否"等价" ───────────────────────────────────
# 论文侧：accept≈minor_revision（都可接收），major_revision≈reject（都要大改/拒）。
# 报告侧：well_done 独立；needs_evidence/needs_depth 都是"需要补"；rewrite_required 独立。
_PAPER_EQUIV = {
    "accept": {"accept", "minor_revision"},
    "minor_revision": {"accept", "minor_revision"},
    "major_revision": {"major_revision", "reject"},
    "reject": {"major_revision", "reject"},
}
_REPORT_EQUIV = {
    "well_done": {"well_done"},
    "needs_evidence": {"needs_evidence", "needs_depth"},
    "needs_depth": {"needs_evidence", "needs_depth"},
    "rewrite_required": {"rewrite_required"},
}


def is_enabled() -> bool:
    """是否开启第二评审（PAPERFORGE_SECOND_OPINION=1）。"""
    try:
        return bool(get_settings().second_opinion_enabled)
    except Exception:  # noqa: BLE001 - settings 异常隔离
        return False


def disagreement_threshold() -> float:
    """分歧分数阈值（PAPERFORGE_SECOND_OPINION_THRESHOLD，默认 0.15）。"""
    try:
        v = float(get_settings().second_opinion_threshold)
        return v if v > 0 else 0.15
    except Exception:  # noqa: BLE001 - settings 异常隔离
        return 0.15


def override_enabled() -> bool:
    """云端纠正本地开关（PAPERFORGE_SECOND_OPINION_OVERRIDE，默认 True）。

    开启且双模型分歧超阈值时，以云端（更强）模型的 score/verdict 覆盖本地结果。
    """
    try:
        return bool(get_settings().second_opinion_override)
    except Exception:  # noqa: BLE001 - settings 异常隔离
        return False


def _build_provider(cfg):
    """从 DB 配置行构建 provider 实例（公开入口，不改动全局当前模型）。"""
    factory = get_factory()
    return factory.build_provider(cfg)


def _glm_vision_provider(factory) -> Any | None:
    """回退第二评审员：用 glm_vision_* 云端配置（同一把 GLM key 即可，文本复核也能用）。

    仅当 glm_vision_enabled 且配置了 api_key 时返回 provider；否则 None（fail-open）。
    这样「默认开」对已经配好 GLM key 的用户开箱即用，无需再到 llm_configs 加云端模型。
    """
    try:
        st = get_settings()
        if not getattr(st, "glm_vision_enabled", False):
            return None
        key = getattr(st, "glm_vision_api_key", "") or ""
        if not key:
            return None
        base = (
            getattr(st, "glm_vision_base_url", "") or "https://open.bigmodel.cn/api/paas/v4"
        ).rstrip("/")
        # ⚠️ 文本第二评审必须用文本模型：glm_vision_model 是视觉模型(glm-4v-flash)，
        # 纯文本长提示下常不按 score:/verdict: 格式输出导致解析失败、静默跳过。
        # 故回退路径改用独立的 second_opinion_model（默认 glm-4-flash，文本通用）。
        model = getattr(st, "second_opinion_model", "") or "glm-4-flash"

        class _Cfg:
            api_url = base
            api_key = key
            model_id = model

        return factory.build_provider(_Cfg())
    except Exception as e:  # noqa: BLE001 - 回退构建失败仅 fail-open
        logger.warning("[second_opinion] glm_vision 回退构建失败（跳过）: %s", e)
        return None


def find_second_provider(db=None):
    """从 llm_configs 找一个与主 provider 不同的启用配置。

    ⚠️ 不在本函数内部 open/close 独立 session：调用方（评审任务）把
    自己的 db 传进来复用，避免对共享 session 的 close() 副作用污染调用方
    事务（2026-08-09 实测：独立 SessionLocal 在测试 patch 场景下会 close
    掉共享 session，导致主评审最终 commit 丢失）。db 为空时才自建并在
    finally 中关闭（生产兜底）。

    第二 provider 判定：api_url 与主 provider 不同（即云端模型或另一端点）。
    主 provider 未初始化时，跳过本机 llama-server（127.0.0.1/localhost），
    其余启用配置都可作为独立意见来源。找不到返回 None（fail-open）。
    """
    try:
        # 🛡️ 测试环境守卫：pytest 下不回退云端，避免测试套件发起真实 API 调用（fail-open）。
        import sys

        if "pytest" in sys.modules:
            return None
        factory = get_factory()
        # 只读内存态当前 provider（不触发 DB 加载，不碰任何 session）
        primary_url = ""
        try:
            current = getattr(factory, "_current", None)
            if current is not None:
                primary_url = (getattr(current, "base_url", "") or "").strip().lower()
        except Exception:  # noqa: BLE001 - 主 provider 信息缺失仅影响候选过滤
            primary_url = ""

        from .models import LLMConfig

        if db is None:
            from .database import SessionLocal

            _own_db = SessionLocal()
        else:
            _own_db = None
        qdb = db if _own_db is None else _own_db
        try:
            for cfg in (
                qdb.query(LLMConfig).filter(LLMConfig.enabled == True).order_by(LLMConfig.id).all()  # noqa: E712
            ):
                url = (cfg.api_url or "").strip().lower()
                if not url:
                    continue
                if not primary_url and ("127.0.0.1" in url or "localhost" in url):
                    continue  # 主 provider 未知时，本机 llama-server 视为「主」，跳过
                if url != primary_url:
                    return _build_provider(cfg)
            # llm_configs 无云端配置 → 回退 glm_vision_* 作为第二评审员（开箱即用）
            return _glm_vision_provider(factory)
        finally:
            if _own_db is not None:
                _own_db.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("[second_opinion] 枚举第二 provider 失败: %s", e)
        return None


def _truncate_head(text: str, max_chars: int = _MAX_INPUT_CHARS) -> str:
    """截断到头部 max_chars 字符（第二评审员读开头即可，控制 token 成本）。"""
    t = (text or "").strip()
    if not t:
        return ""
    return t[:max_chars]


def _parse_output(raw: str) -> dict[str, Any]:
    """从 LLM 输出解析 score/verdict/reason（宽松正则，fail-open）。"""
    if not raw:
        return {}
    out: dict[str, Any] = {}
    m = re.search(r"(?m)^score\s*[:：]\s*([0-9]*\.?[0-9]+)", raw)
    if m:
        try:
            out["score"] = max(0.0, min(1.0, float(m.group(1))))
        except ValueError:
            pass
    m = re.search(r"(?m)^verdict\s*[:：]\s*([A-Za-z_]+)", raw)
    if m:
        out["verdict"] = m.group(1).strip().lower()
    m = re.search(r"(?m)^reason\s*[:：]\s*(.+)$", raw)
    if m:
        out["reason"] = m.group(1).strip()[:200]
    return out


def _verdicts_agree(a: str, b: str, kind: str) -> bool:
    """verdict 语义等价判定（论文/报告两套口径）。"""
    if not a or not b:
        return True  # 缺 verdict 不判分歧（fail-open）
    table = _PAPER_EQUIV if kind == "paper" else _REPORT_EQUIV
    va = table.get(a, {a})
    return b in va


def should_trigger_cloud(primary_score: float | None) -> tuple[bool, float]:
    """判断是否应对当前论文调用云端第二评审。

    Returns:
        (should_trigger, trigger_weight)
        - should_trigger: True 时调用云端；False 时跳过（节省 API）。
        - trigger_weight: 云端融合权重（过渡带内线性衰减，核心区=1.0，边界=0.0）。

    设计（ADR-018 融合实验，500 篇 PeerRead 验证）：
      - 核心区 [0.70, 0.80]：总是触发（trigger_weight=1.0）
      - 过渡带 [0.68, 0.70) 和 (0.80, 0.82]：概率触发（线性衰减）
      - 区间外：不触发（直接采信本地）

    物理解释：云端 GLM-4.7-Flash 分数高度量化（64% 集中在 0.65），
    在高分区（>0.80）会把好论文拖低；在低分区（<0.65）本地已足够准确。
    只在边界区间 [0.70, 0.80] 调用云端，精准打击本地"高分放水"。
    """
    if primary_score is None:
        return False, 0.0
    s = float(primary_score)
    # 核心区：总是触发
    if _TRIGGER_LOW <= s <= _TRIGGER_HIGH:
        return True, 1.0
    # 过渡带下界 [0.68, 0.70)
    if _TRIGGER_LOW - _TRIGGER_MARGIN <= s < _TRIGGER_LOW:
        weight = (s - (_TRIGGER_LOW - _TRIGGER_MARGIN)) / _TRIGGER_MARGIN
        return random.random() < weight, weight
    # 过渡带上界 (0.80, 0.82]
    if _TRIGGER_HIGH < s <= _TRIGGER_HIGH + _TRIGGER_MARGIN:
        weight = ((_TRIGGER_HIGH + _TRIGGER_MARGIN) - s) / _TRIGGER_MARGIN
        return random.random() < weight, weight
    # 区间外：不触发
    return False, 0.0


def fuse_scores(local_score: float, cloud_score: float, trigger_weight: float = 1.0) -> float:
    """Ridge 融合 + Min-Max 重映射。

    公式：raw = w_local × local + w_cloud × cloud + intercept
    重映射：calibrated = (raw - RIDGE_MIN) / (RIDGE_MAX - RIDGE_MIN)
    trigger_weight: 过渡带衰减系数（0.0~1.0），核心区内=1.0，边界处线性混合。
    """
    raw = _RIDGE_W_LOCAL * local_score + _RIDGE_W_CLOUD * cloud_score + _RIDGE_INTERCEPT
    calibrated = (raw - _RIDGE_MIN) / (_RIDGE_MAX - _RIDGE_MIN)
    calibrated = max(0.0, min(1.0, calibrated))  # clamp to [0, 1]
    # 过渡带内线性混合融合分与本地分
    return round(trigger_weight * calibrated + (1 - trigger_weight) * local_score, 4)


def get_second_opinion(text: str, *, kind: str = "paper", db=None) -> dict[str, Any]:
    """用第二 provider 对文本做一次独立评审。

    Returns:
        {enabled, provider, model, score, verdict, reason}；失败时 enabled=False。
    """
    provider = find_second_provider(db=db)
    if provider is None:
        return {"enabled": False, "skipped": "no_second_provider"}
    t = _truncate_head(text)
    if not t:
        return {"enabled": False, "skipped": "empty_text"}
    prompt = (_PAPER_PROMPT if kind == "paper" else _REPORT_PROMPT).format(text=t)
    try:
        from .llm.reproducibility import with_eval_seed

        result = provider.chat(
            [ChatMessage(role="user", content=prompt)],
            temperature=0.2,
            max_tokens=256,
            **with_eval_seed({}),
        )
        raw = (result.content or "").strip()
        parsed = _parse_output(raw)
        if not parsed.get("score") and not parsed.get("verdict"):
            return {"enabled": False, "skipped": "parse_failed"}
        return {
            "enabled": True,
            "provider": getattr(provider, "provider_name", None),
            "model": getattr(provider, "model", None),
            "score": parsed.get("score"),
            "verdict": parsed.get("verdict"),
            "reason": parsed.get("reason", ""),
        }
    except Exception as e:  # noqa: BLE001 - fail-open
        logger.warning("[second_opinion] 第二评审调用失败（跳过）: %s", e)
        return {"enabled": False, "skipped": "error", "error": str(e)[:200]}


def assess_disagreement(
    primary_score: float | None,
    primary_verdict: str | None,
    second: dict[str, Any],
    *,
    kind: str = "paper",
) -> dict[str, Any]:
    """合并主评审与第二评审，给出分歧判定。

    Returns:
        {enabled, second_score, second_verdict, second_reason, second_provider,
         second_model, score_delta, verdict_agree, flag, note}
        flag ∈ "agree" | "disagreement" | "no_data"
    """
    if not second.get("enabled"):
        return {**second, "flag": "no_data"}
    s2 = second.get("score")
    v2 = second.get("verdict")
    if s2 is None and not v2:
        return {**second, "flag": "no_data"}
    out: dict[str, Any] = {
        "enabled": True,
        "second_provider": second.get("provider"),
        "second_model": second.get("model"),
        "second_score": s2,
        "second_verdict": v2,
        "second_reason": second.get("reason", ""),
    }
    # 分数分歧
    delta: float | None = None
    if s2 is not None and primary_score is not None:
        delta = round(abs(float(s2) - float(primary_score)), 4)
    out["score_delta"] = delta
    # verdict 分歧
    agree = _verdicts_agree(str(primary_verdict or ""), str(v2 or ""), kind)
    out["verdict_agree"] = agree
    thr = disagreement_threshold()
    flag = "disagreement" if (delta is not None and delta >= thr) or not agree else "agree"
    out["flag"] = flag
    if flag == "disagreement":
        reasons = []
        if delta is not None and delta >= thr:
            reasons.append(f"分数分歧 |Δ|={delta:.2f} ≥ {thr:.2f}")
        if not agree:
            reasons.append(f"verdict 不一致（主={primary_verdict}，次={v2}）")
        out["note"] = "；".join(reasons) + " → 建议人工复核（needs_human_review）"
    else:
        out["note"] = "双模型意见一致" if delta is not None else "双模型 verdict 一致"
    return out


def run_second_opinion(
    text: str,
    *,
    primary_score: float | None = None,
    primary_verdict: str | None = None,
    kind: str = "paper",
    db=None,
    allow_rescue: bool = True,
) -> dict[str, Any]:
    """顶层入口：开关检查 → 独立评审 → 分歧判定 / 本地失败兜底（全程 fail-open）。

    db：可选。评审任务传入自己的 Session 供候选枚举复用，避免独立 session
    的 close() 副作用干扰调用方事务（见 find_second_provider 注释）。

    本地失败兜底（rescue）：当本地主评审未产出有效分数（primary_score 为 None）
    且云端第二评审有效时，直接以云端结果作为最终结果（flag="rescued"），
    而不是让该篇评审无结果。仅在 second_opinion 开启且云端可用时触发，
    全程 fail-open，云端不可用则交回调用方原失败逻辑（如 B 路径的 status=failed）。
    """
    if not is_enabled():
        return {"enabled": False, "skipped": "disabled"}

    # ── 条件触发：仅在边界区间调用云端（节省 60%+ API，规避 429 限流）──
    triggered, trigger_weight = should_trigger_cloud(primary_score)
    if not triggered and primary_score is not None:
        # 区间外：跳过云端调用，直接采信本地结果（附影子标记）
        return {
            "enabled": True,
            "skipped": "outside_trigger_zone",
            "triggered": False,
            "trigger_weight": trigger_weight,
            "resolved_score": primary_score,
            "resolved_verdict": primary_verdict,
            "original_score": primary_score,
            "original_verdict": primary_verdict,
            "cloud_shadow_score": None,
            "cloud_shadow_verdict": None,
            "sentry_flag": False,
            "needs_human_review": False,
            "override_status": "bypassed_by_trigger",
            "note": (
                f"本地分数 {primary_score:.4f} 在触发区间外 [0.60,0.75]，"
                f"跳过云端调用，直接采信本地结果。"
            ),
        }

    second = get_second_opinion(text, kind=kind, db=db)
    assess = assess_disagreement(primary_score, primary_verdict, second, kind=kind)

    override = override_enabled()
    corrected = False
    resolved_score = primary_score
    resolved_verdict = primary_verdict

    # ── 云端兜底：本地主评审缺失/解析失败 ──
    # 本地未产出有效分数（primary_score is None）且云端第二评审有效时，
    # 直接以云端结果兜底（标记 rescued）。与下方「分歧覆盖」互斥：
    # 分歧覆盖要求本地有分数可覆盖，兜底则是本地根本没分数。
    if allow_rescue and second.get("enabled") and primary_score is None:
        s2 = second.get("score")
        v2 = second.get("verdict")
        if s2 is not None or v2:
            out = dict(assess)
            out["enabled"] = True
            out["rescued"] = True
            out["local_failed"] = True
            out["override_enabled"] = override
            out["corrected"] = True
            out["resolved_score"] = s2
            out["resolved_verdict"] = v2
            out["original_score"] = primary_score
            out["original_verdict"] = primary_verdict
            out["flag"] = "rescued"
            out["corrected_by"] = second.get("provider") or second.get("model")
            out["note"] = "本地主评审未产出有效分数（解析失败/空返回），已由云端第二评审直接兜底。"
            return out

    # ── L1 融合 or 旧版 override（互斥）──
    # 当 triggered 且云端可用时，用 Ridge 融合替代 override。
    # trigger_weight 在过渡带内线性衰减（0~1），核心区=1.0。
    # 融合优先于 override：一旦融合产出 resolved_score，不再走 override。
    _cloud_score_for_fusion = assess.get("second_score")
    if (
        triggered
        and assess.get("flag") == "disagreement"  # 仅分歧时融合，一致则保持本地
        and second.get("enabled")
        and _cloud_score_for_fusion is not None
        and primary_score is not None
    ):
        fused = fuse_scores(primary_score, _cloud_score_for_fusion, trigger_weight)
        resolved_score = fused
        # verdict 从融合分重新判定（复用 DEPTH 阈值）
        if fused >= 0.80:
            resolved_verdict = "accept"
        elif fused >= 0.70:
            resolved_verdict = "minor_revision"
        elif fused >= 0.60:
            resolved_verdict = "major_revision"
        else:
            resolved_verdict = "reject"
        corrected = True
        out_fused = {
            "fusion_applied": True,
            "fusion_mode": "L1_ridge",
            "ridge_w_local": _RIDGE_W_LOCAL,
            "ridge_w_cloud": _RIDGE_W_CLOUD,
            "ridge_intercept": _RIDGE_INTERCEPT,
            "trigger_weight": round(trigger_weight, 4),
        }
    elif override and assess.get("flag") == "disagreement":
        # ── 旧版 override（仅在融合未触发时生效）──
        s2 = assess.get("second_score")
        v2 = assess.get("second_verdict")
        if s2 is not None or v2:
            corrected = True
            if s2 is not None:
                resolved_score = s2
            if v2:
                resolved_verdict = v2
        out_fused = {}
    else:
        out_fused = {}

    out = dict(assess)
    out["override_enabled"] = override
    out["corrected"] = corrected
    out["resolved_score"] = resolved_score
    out["resolved_verdict"] = resolved_verdict
    out["original_score"] = primary_score
    out["original_verdict"] = primary_verdict

    # ── P2 影子哨兵（shadow sentry）：本地高、云端强烈反对 ──
    # 与 rescue（本地失败兜底）互斥：rescue 仅在 primary_score 为 None 时触发，
    #   哨兵要求本地有有效高分数（>=SENTRY_LOCAL_HIGH），二者天然不相容。
    # 与 override（云端纠正）互斥：仅在 override 未实际覆盖（corrected=False）时判定，
    #   避免与「分歧覆盖」重复标记。
    # 行为（影子模式，默认开）：仅产出 needs_human_review 建议标记 + 审计字段，
    #   绝不改变 resolved_score、不自动转人工、不阻断流程。阈值后续按全库分布调整。
    sentry_flag = False
    _cloud_score = assess.get("second_score")
    if (
        not corrected  # override 未实际覆盖（与覆盖互斥）
        and primary_score is not None  # 本地确有有效分（与 rescue 互斥）
        and _cloud_score is not None
        and float(primary_score) >= SENTRY_LOCAL_HIGH
        and float(_cloud_score) < SENTRY_CLOUD_LOW
    ):
        sentry_flag = True
    out["sentry_flag"] = sentry_flag
    out["needs_human_review"] = sentry_flag  # 影子模式：仅建议标记，不触发任何工作流动作
    out["cloud_shadow_score"] = _cloud_score  # 始终记录云端影子分，便于哨兵/审计回放
    out["local_score"] = primary_score  # 本地主评审分（= 未覆盖时的 resolved）

    # P0 安全标记：override 关闭时云端仅作影子分，resolved 保持本地；便于日后回放/审计
    out["triggered"] = triggered
    out["trigger_weight"] = round(trigger_weight, 4)
    out.update(out_fused)
    out["override_status"] = "disabled_by_p0" if not override else "enabled"
    if not override and assess.get("flag") == "disagreement" and second.get("enabled"):
        out["cloud_shadow_score"] = assess.get("second_score")
        out["cloud_shadow_verdict"] = assess.get("second_verdict")
        out["note"] = (out.get("note") or "") + (
            "（云端仅作影子分，override=disabled_by_p0，本地分未被覆盖）"
        )
    if corrected and assess.get("flag") == "disagreement":
        out["corrected_by"] = assess.get("second_provider") or assess.get("second_model")
        out["note"] = (out.get("note") or "") + (
            f" → 云端已覆盖本地（{primary_verdict}@{primary_score} → "
            f"{resolved_verdict}@{resolved_score}）"
        )
    return out
