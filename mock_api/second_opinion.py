"""双模型交叉复核（Second Opinion）—— ADR-014 · P8。

背景（详见 docs/improving-review-rigor.md §漏洞 C）：
  本地 Qwen3.5-9B 单模型意见不可当"终审"。多 AI 对比实验
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
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .llm import ChatMessage, get_factory
from .settings import get_settings

logger = logging.getLogger(__name__)

# ── 常量 ───────────────────────────────────────────────────────────────
_MAX_INPUT_CHARS = 6000  # 第二评审员只读文本头（摘要+引言足够独立判断）

_PAPER_PROMPT = """你是一位独立的期刊审稿人，正在对一篇论文做匿名评审。
以下是论文的摘要与引言（节选）。请【独立】给出你的判断，不要参考任何外部评分。

论文：
{text}

【要求】
- 综合质量分：综合考察创新性、严谨性、影响力、可复现性后给出 0~1 的一个小数。
- verdict 只能是 accept / minor_revision / major_revision / reject 之一。
- 请严格按以下格式输出（每行一个字段，key: value）：
score: 0.72
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
score: 0.62
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


def _build_provider(cfg):
    """从 DB 配置行构建 provider 实例（公开入口，不改动全局当前模型）。"""
    factory = get_factory()
    return factory.build_provider(cfg)


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
            return None
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
) -> dict[str, Any]:
    """顶层入口：开关检查 → 独立评审 → 分歧判定（全程 fail-open）。

    db：可选。评审任务传入自己的 Session 供候选枚举复用，避免独立 session
    的 close() 副作用干扰调用方事务（见 find_second_provider 注释）。
    """
    if not is_enabled():
        return {"enabled": False, "skipped": "disabled"}
    second = get_second_opinion(text, kind=kind, db=db)
    return assess_disagreement(primary_score, primary_verdict, second, kind=kind)
