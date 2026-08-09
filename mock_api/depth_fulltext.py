"""DEPTH 全文覆盖层（分块摘要 + 采样增强）—— 漏洞 C 软件解法。

背景（docs/improving-review-rigor.md §漏洞 C）：
  本地 9B 模型实际 ctx≈8K token，而 segment_paper_text 只给模型
  摘要+引言+结论 + 正文开头（max_chars_full，rtx5060 预设 16000 字），
  论文中段（方法/实验）几乎看不见 → QE 证据池残缺、评分节点噪声。

解法（env 门控，默认关，全程 fail-open）：
  1. **map**：全文 → 分块（~chunk_size 字）→ 每块本地模型摘要（并行，单块失败跳过）
  2. **reduce**：块摘要 → 1-2 轮合并 → 全局摘要（~1500 字）——模型"知道"全文讲了什么
  3. **采样增强**：按「数值/百分比/表图关键词 + 位置分散度」选 top_k 块原文注入 prompt，
     让 QE 能引用真实中段段落（证据锚定，不编造）
  4. **缓存**：按 (paper_id, text_hash) 存 depth_fulltext_cache 表，重评不重复付 LLM 成本

接入：depth_eval_v4.py 在文本分段后调 build_fulltext_context，产物经
  PaperContext.fulltext_supplement 注入 QE/Q234 的 {paper} 视图（默认空串 = 零开销）。

设计约束：
  - 默认关闭：PAPERFORGE_DEPTH_FULLTEXT_ENABLED 未设 → is_enabled()=False，
    所有入口直接返回 None，与历史行为完全一致。
  - fail-open：任何一步（分块/摘要/合并/采样/DB）异常都只降级到无补充文本，
    绝不改变主评审流程与分数。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger(__name__)

# ── 默认参数（可经 env 覆盖）────────────────────────────────────────────
_DEFAULT_CHUNK_SIZE = int(os.getenv("PAPERFORGE_DEPTH_FULLTEXT_CHUNK_SIZE", "1500"))
_DEFAULT_CHUNK_OVERLAP = int(os.getenv("PAPERFORGE_DEPTH_FULLTEXT_CHUNK_OVERLAP", "120"))
_DEFAULT_TOP_K = int(os.getenv("PAPERFORGE_DEPTH_FULLTEXT_TOP_K", "8"))
_DEFAULT_MAX_WORKERS = int(os.getenv("PAPERFORGE_DEPTH_FULLTEXT_MAX_WORKERS", "3"))
_SUMMARY_CHARS = 100  # 单块摘要目标长度（字）
_GLOBAL_SUMMARY_CHARS = 1500  # 全局摘要目标长度（字）
_VERBATIM_CHUNK_CHARS = 500  # 注入的采样块单块截断
_MAX_INJECT_CHARS = 4000  # 注入总量上限（防止把 ctx 又撑爆）

# 证据信号：命中越多 → 越可能是"方法/实验结果"段落
_EVIDENCE_SIGNAL_PATTERNS = [
    re.compile(r"\d+\.\d+|\d+\s*%|%\s*\d+|\b\d{2,}\b", re.I),  # 数字/百分比
    re.compile(r"\btable\s+\d|\bfig(?:ure)?\.?\s*\d|表\s*\d|图\s*\d", re.I),  # 表图引用
    re.compile(r"accuracy|precision|recall|f1|bleu|perplexity|error|loss|result|performance", re.I),
    re.compile(r"实验|结果|精度|性能|准确率|召回率|F1|指标", re.I),
]

# 摘要 prompt（单块 map）
_SUMMARIZE_PROMPT = """以下是同一篇论文的一个片段。用不超过{max_chars}字概括这段讲了什么（方法/实验/结论要点，保留关键数字）。只输出概括，不要其他内容。

片段：
{chunk}"""

# 合并 prompt（全局 reduce）
_MERGE_PROMPT = """以下是同一篇论文各分段的摘要。合并成一篇不超过{max_chars}字的论文全局摘要，按"方法→实验→主要结果与贡献"组织，保留关键数字与结论。只输出合并后的摘要，不要其他内容。

各分段摘要：
{summaries}"""


# ===========================================================================
# 开关
# ===========================================================================
def is_enabled() -> bool:
    """是否开启全文覆盖层（PAPERFORGE_DEPTH_FULLTEXT_ENABLED=1）。默认关闭。"""
    try:
        from .settings import get_settings

        return bool(get_settings().depth_fulltext_enabled)
    except Exception:  # noqa: BLE001 - settings 异常隔离，fail-open
        return False


def _chunk_config() -> tuple[int, int]:
    """返回 (chunk_size, chunk_overlap)，允许运行期 env 覆盖。"""
    try:
        s = 1500
        o = 120
        from .settings import get_settings

        cfg = get_settings()
        s = int(cfg.depth_fulltext_chunk_size or _DEFAULT_CHUNK_SIZE)
        o = int(cfg.depth_fulltext_chunk_overlap or _DEFAULT_CHUNK_OVERLAP)
        return max(300, s), max(0, min(o, s // 2))
    except Exception:  # noqa: BLE001
        return _DEFAULT_CHUNK_SIZE, _DEFAULT_CHUNK_OVERLAP


def top_k_setting() -> int:
    try:
        from .settings import get_settings

        return int(get_settings().depth_fulltext_top_k or _DEFAULT_TOP_K)
    except Exception:  # noqa: BLE001
        return _DEFAULT_TOP_K


# ===========================================================================
# 分块
# ===========================================================================
def split_chunks(text: str, chunk_size: int | None = None, overlap: int | None = None) -> list[str]:
    """把全文切成有重叠的句子级分块（避免硬切句子）。"""
    t = (text or "").strip()
    if not t:
        return []
    if chunk_size is None or overlap is None:
        chunk_size, overlap = _chunk_config()
    if len(t) <= chunk_size:
        return [t]

    # 先按句子边界粗分，再按 chunk_size 组装
    sentences = re.split(r"(?<=[。！？.!?])\s*", t)
    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        if not sent:
            continue
        if len(buf) + len(sent) <= chunk_size:
            buf += sent
            continue
        if buf:
            chunks.append(buf)
        if len(sent) > chunk_size:
            # 超长句硬切（带 overlap）
            for i in range(0, len(sent), chunk_size - overlap):
                chunks.append(sent[i : i + chunk_size])
        else:
            buf = sent
    if buf:
        chunks.append(buf)
    return [c.strip() for c in chunks if c.strip()]


# ===========================================================================
# 单块摘要（map）—— 并行，单块失败跳过
# ===========================================================================
def summarize_chunks(
    chunks: list[str],
    llm_func: Any,
    max_workers: int | None = None,
    max_chars: int = _SUMMARY_CHARS,
) -> list[dict[str, str]]:
    """并行生成每块的摘要。返回 [{index, chunk, summary}]；失败的块 summary=''。"""
    if not chunks:
        return []
    workers = max(1, max_workers or _DEFAULT_MAX_WORKERS)

    def _one(i: int, chunk: str) -> dict[str, str]:
        prompt = _SUMMARIZE_PROMPT.format(max_chars=max_chars, chunk=chunk[:2000])
        try:
            raw = llm_func(prompt)
            summary = (raw or "").strip()
            return {"index": i, "chunk": chunk, "summary": summary[: max_chars * 4]}
        except Exception as e:  # noqa: BLE001 - fail-open
            logger.warning("[depth_fulltext] 分块摘要失败 #%d: %s", i, e)
            return {"index": i, "chunk": chunk, "summary": ""}

    out: list[dict[str, str]] = []
    if workers <= 1 or len(chunks) <= 2:
        out = [_one(i, c) for i, c in enumerate(chunks)]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = [ex.submit(_one, i, c) for i, c in enumerate(chunks)]
            for fut in as_completed(futures):
                try:
                    out.append(fut.result())
                except Exception:  # noqa: BLE001 - fail-open
                    pass
        out.sort(key=lambda x: x["index"])
    logger.info(
        "[depth_fulltext] 分块摘要完成: %d/%d 块成功",
        sum(1 for x in out if x["summary"]),
        len(chunks),
    )
    return out


# ===========================================================================
# 合并（reduce）—— 1-2 轮把块摘要压成全局摘要
# ===========================================================================
def merge_summaries(
    summaries: list[dict[str, str]],
    llm_func: Any,
    target_chars: int = _GLOBAL_SUMMARY_CHARS,
) -> str:
    """把块摘要合并为全局摘要（多轮 map-reduce）。失败返回空串。"""
    valid = [s["summary"] for s in summaries if s.get("summary")]
    if not valid:
        return ""
    if len(valid) == 1:
        return valid[0][:target_chars]
    batch: list[str] = list(valid)
    per_batch_target = max(300, target_chars // 2)
    while len(batch) > 1:
        merged: list[str] = []
        step = 6  # 每轮最多合并 6 条
        for i in range(0, len(batch), step):
            group = batch[i : i + step]
            if len(group) == 1:
                merged.append(group[0])
                continue
            prompt = _MERGE_PROMPT.format(
                max_chars=per_batch_target,
                summaries="\n".join(f"- {g}" for g in group),
            )
            try:
                raw = llm_func(prompt)
                text = (raw or "").strip()
                merged.append(text[: per_batch_target * 4] if text else group[0])
            except Exception as e:  # noqa: BLE001 - fail-open
                logger.warning("[depth_fulltext] 摘要合并失败: %s", e)
                merged.append(" ".join(group)[: per_batch_target * 4])
        batch = merged
        per_batch_target = max(300, per_batch_target // 2)
    return batch[0][:target_chars]


# ===========================================================================
# 采样增强：按"证据信号 + 位置分散度"选 top_k 块原文
# ===========================================================================
def _evidence_signal(chunk: str) -> int:
    return sum(1 for pat in _EVIDENCE_SIGNAL_PATTERNS if pat.search(chunk))


def pick_verbatim_chunks(
    chunks: list[str],
    summaries: list[dict[str, str]] | None = None,
    top_k: int | None = None,
) -> list[dict[str, Any]]:
    """选 top_k 个「位置分散 + 证据信号强」的块原文注入 prompt。

    贪心：已选位置的最小距离作为多样性惩罚；综合分 = 证据信号 + 多样性。
    """
    n = len(chunks)
    if n == 0:
        return []
    k = max(1, min(top_k or top_k_setting(), n))
    if n <= k:
        return [
            {"index": i, "text": c[:_VERBATIM_CHUNK_CHARS], "signal": _evidence_signal(c)}
            for i, c in enumerate(chunks)
        ]
    positions = [i / n for i in range(n)]
    selected: list[int] = []
    picked: list[dict[str, Any]] = []
    for _ in range(k):
        best_i, best_score = -1, -1.0
        for i in range(n):
            if i in selected:
                continue
            diversity = min(abs(positions[i] - positions[j]) for j in selected) if selected else 1.0
            score = _evidence_signal(chunks[i]) * 2.0 + diversity
            if score > best_score:
                best_i, best_score = i, score
        selected.append(best_i)
        picked.append(
            {
                "index": best_i,
                "text": chunks[best_i][:_VERBATIM_CHUNK_CHARS],
                "signal": _evidence_signal(chunks[best_i]),
            }
        )
    picked.sort(key=lambda x: x["index"])
    return picked


# ===========================================================================
# 组装可注入文本块
# ===========================================================================
def format_supplement(fulltext_ctx: dict[str, Any] | None) -> str:
    """把 fulltext_ctx 组装为 prompt 尾部可注入的文本块（空/None → ''）。"""
    if not fulltext_ctx:
        return ""
    parts: list[str] = []
    gs = (fulltext_ctx.get("global_summary") or "").strip()
    if gs:
        parts.append(f"【全文全局摘要】{gs}")
    vc = fulltext_ctx.get("verbatim_chunks") or []
    if vc:
        lines = [f"[{i}] {c['text']}" for i, c in enumerate(vc, 1)]
        parts.append("【正文补充段落（可直接引用为证据）】\n" + "\n\n".join(lines))
    block = "\n\n".join(parts)
    return block[:_MAX_INJECT_CHARS]


# ===========================================================================
# DB 缓存（best-effort：任何失败都跳过缓存，不影响主流程）
# ===========================================================================
def _text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _cache_get(db: Any, paper_id: str, text_hash: str) -> dict[str, Any] | None:
    if db is None:
        return None
    try:
        from .models import DepthFulltextCache

        row = (
            db.query(DepthFulltextCache)
            .filter(
                DepthFulltextCache.paper_id == paper_id,
                DepthFulltextCache.text_hash == text_hash,
            )
            .first()
        )
        if row is None:
            return None
        return {
            "global_summary": row.global_summary or "",
            "chunk_summaries": json.loads(row.chunk_summaries or "[]"),
            "verbatim_chunks": json.loads(row.verbatim_chunks or "[]"),
            "n_chunks": row.n_chunks or 0,
            "cached": True,
        }
    except Exception as e:  # noqa: BLE001 - 表不存在/DB 异常 → 不缓存
        logger.warning("[depth_fulltext] 缓存读取失败（跳过缓存）: %s", e)
        return None


def _cache_set(db: Any, paper_id: str, text_hash: str, ctx: dict[str, Any]) -> None:
    if db is None:
        return
    try:
        from .models import DepthFulltextCache

        row = (
            db.query(DepthFulltextCache)
            .filter(
                DepthFulltextCache.paper_id == paper_id,
                DepthFulltextCache.text_hash == text_hash,
            )
            .first()
        )
        if row is None:
            row = DepthFulltextCache(paper_id=paper_id, text_hash=text_hash)
            db.add(row)
        row.global_summary = ctx.get("global_summary") or ""
        row.chunk_summaries = json.dumps(ctx.get("chunk_summaries", []), ensure_ascii=False)
        row.verbatim_chunks = json.dumps(ctx.get("verbatim_chunks", []), ensure_ascii=False)
        row.n_chunks = int(ctx.get("n_chunks", 0))
        db.commit()
    except Exception as e:  # noqa: BLE001 - 缓存写入失败不影响评审
        logger.warning("[depth_fulltext] 缓存写入失败（跳过缓存）: %s", e)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass


# ===========================================================================
# 顶层入口
# ===========================================================================
def build_fulltext_context(
    paper_id: str,
    full_text: str,
    llm_func: Any,
    db: Any = None,
) -> dict[str, Any] | None:
    """构建全文覆盖上下文（摘要 + 采样块）。未开启/失败/文本过短 → None。

    文本过短（≤ max_chars_full 的 1.2 倍）时现有头截断已足够，跳过（省 LLM 成本）。
    """
    if not is_enabled():
        return None
    try:
        t = (full_text or "").strip()
        if not t:
            return None
        from .config import get_compute_mode_config

        max_full = int(get_compute_mode_config().get("max_chars_full", 32000))
        if len(t) <= max_full * 1.2:
            # 现有头截断基本覆盖全文，无需分块摘要
            logger.debug("[depth_fulltext] 文本长度 %d ≤ 1.2×max_full=%d，跳过", len(t), max_full)
            return None

        h = _text_hash(t)
        # 调用方未传 session 时自建并自关（只关自己的，绝不碰调用方 session）
        _own_db = None
        if db is None:
            try:
                from .database import SessionLocal

                _own_db = SessionLocal()
                db = _own_db
            except Exception:  # noqa: BLE001 - 无缓存可用即退化
                db = None
        try:
            cached = _cache_get(db, paper_id, h)
            if cached is not None:
                logger.info("[depth_fulltext] 命中缓存: paper=%s hash=%s", paper_id, h)
                return cached

            chunk_size, overlap = _chunk_config()
            chunks = split_chunks(t, chunk_size, overlap)
            if len(chunks) < 2:
                return None
            summaries = summarize_chunks(chunks, llm_func)
            global_summary = merge_summaries(summaries, llm_func)
            verbatim = pick_verbatim_chunks(chunks, summaries)
            ctx: dict[str, Any] = {
                "global_summary": global_summary,
                "chunk_summaries": [s.get("summary", "") for s in summaries],
                "verbatim_chunks": verbatim,
                "n_chunks": len(chunks),
                "cached": False,
            }
            _cache_set(db, paper_id, h, ctx)
            logger.info(
                "[depth_fulltext] 全文覆盖构建完成: paper=%s n_chunks=%d summary=%dc verbatim=%d",
                paper_id,
                len(chunks),
                len(global_summary),
                len(verbatim),
            )
            return ctx
        finally:
            if _own_db is not None:
                try:
                    _own_db.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001 - 全程 fail-open
        logger.warning("[depth_fulltext] 构建失败（降级为无补充）: %s", e)
        return None
