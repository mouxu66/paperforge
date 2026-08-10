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
  - 默认自动开启：论文超过阈值时自动启用全文覆盖；设 PAPERFORGE_DEPTH_FULLTEXT_ENABLED=0
    可强制关闭。短文（≤ 1.2×max_chars_full）自动跳过以省 LLM 成本。
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
_DEFAULT_CHUNK_SIZE = int(os.getenv("PAPERFORGE_DEPTH_FULLTEXT_CHUNK_SIZE", "3000"))
_DEFAULT_CHUNK_OVERLAP = int(os.getenv("PAPERFORGE_DEPTH_FULLTEXT_CHUNK_OVERLAP", "200"))
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
    """是否开启全文覆盖层。默认自动开启；设 PAPERFORGE_DEPTH_FULLTEXT_ENABLED=0 可强制关闭。"""
    try:
        from .settings import get_settings

        return bool(get_settings().depth_fulltext_enabled)
    except Exception:  # noqa: BLE001 - settings 异常隔离，fail-open
        return False


def _chunk_config() -> tuple[int, int]:
    """返回 (chunk_size, chunk_overlap)，允许运行期 env 覆盖。"""
    try:
        s = 3000
        o = 200
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
    # 类型守卫：确保 summaries 元素均为 dict（避免上游 mock/异常产出脏数据）
    _dicts = [s for s in summaries if isinstance(s, dict)]
    valid = [
        s.get("summary", "")
        for s in _dicts
        if isinstance(s.get("summary"), str) and s["summary"].strip()
    ]
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
    *,
    fast: bool = False,
) -> dict[str, Any] | None:
    """构建全文覆盖上下文（摘要 + 采样块）。强制关闭/失败/文本过短 → None。

    默认自动开启；文本过短（≤ max_chars_full 的 1.2 倍）时现有头截断已足够，跳过省成本。
    设 PAPERFORGE_DEPTH_FULLTEXT_ENABLED=0 可全局强制关闭。

    fast=True 时跳过 LLM 摘要（summarize_chunks + merge_summaries），仅返回 verbatim_chunks
    和空 global_summary。适用于零 LLM 成本的关键句驱动路径（extract_key_sentences 替代摘要）。
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
            logger.debug("[depth_fulltext] 文本长度 %d ≤ 1.2×max_full=%d，跳过", len(t), max_full)
            return None

        h = _text_hash(t)
        _own_db = None
        if db is None:
            try:
                from .database import SessionLocal

                _own_db = SessionLocal()
                db = _own_db
            except Exception:  # noqa: BLE001
                db = None
        try:
            if not fast:
                cached = _cache_get(db, paper_id, h)
                if cached is not None:
                    logger.info("[depth_fulltext] 命中缓存: paper=%s hash=%s", paper_id, h)
                    return cached

            chunk_size, overlap = _chunk_config()
            chunks = split_chunks(t, chunk_size, overlap)
            if len(chunks) < 2:
                return None

            if fast:
                # 零 LLM 路径：跳过 summarize_chunks + merge_summaries
                verbatim = pick_verbatim_chunks(chunks)
                ctx: dict[str, Any] = {
                    "global_summary": "",
                    "chunk_summaries": [],
                    "verbatim_chunks": verbatim,
                    "n_chunks": len(chunks),
                    "cached": False,
                    "fast": True,
                }
                logger.info(
                    "[depth_fulltext] 全文覆盖构建完成（fast）: paper=%s n_chunks=%d verbatim=%d",
                    paper_id,
                    len(chunks),
                    len(verbatim),
                )
            else:
                summaries = summarize_chunks(chunks, llm_func)
                global_summary = merge_summaries(summaries, llm_func)
                verbatim = pick_verbatim_chunks(chunks, summaries)
                ctx = {
                    "global_summary": global_summary,
                    "chunk_summaries": [
                        s.get("summary", "") if isinstance(s, dict) else "" for s in summaries
                    ],
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


# ===========================================================================
# 零 LLM 成本的关键句提取层（方案 2：纯正则，无额外 LLM 调用）
# ===========================================================================

# 关键句提取模式：按 DAG 节点需求分类
_KEY_SENTENCE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # ── 含数字/百分比/指标 ──
    (
        "metric",
        re.compile(
            r"[^。！？.!?\n]{0,80}(?:\d+\.?\d*\s*%|\d+\.\d+|accuracy|precision|recall|f1|bleu|"
            r"perplexity|error|loss|performance|准确率|精度|召回率|F1|指标|提高|降低|提升|下降|"
            r"outperforms|beats|state-of-the-art|sota)[^。！？.!?\n]{0,80}",
            re.I,
        ),
    ),
    # ── 含贡献/声明 ──
    (
        "claim",
        re.compile(
            r"[^。！？.!?\n]{0,80}(?:we\s+(?:propose|present|introduce|develop|design)|our\s+(?:method|approach|model|framework|system|architecture|novel|new|contribution)|"
            r"本文提出|我们提出|我们设计|我们开发|我们的方法|贡献|创新|首次|第一个)[^。！？.!?\n]{0,80}",
            re.I,
        ),
    ),
    # ── 含消融/可比性/严谨性 ──
    (
        "rigor",
        re.compile(
            r"[^。！？.!?\n]{0,80}(?:ablation|baseline|compare|compared|versus|vs\.?\s|p.?value|"
            r"significant|significance|benchmark|baseline|消融|基线|对比|显著|p\s*值|"
            r"without\s+the|w/o\s+the|removing|remove|去掉|移除)[^。！？.!?\n]{0,80}",
            re.I,
        ),
    ),
    # ── 含局限/未来工作/不足 ──
    (
        "limitation",
        re.compile(
            r"[^。！？.!?\n]{0,80}(?:limitation|future\s+work|however|we\s+do\s+not|our\s+method\s+(?:does|cannot|may|might|is\s+limited)|"
            r"不足|局限|未来|后续|仍需|有待|尚未|未考虑|未涉及)[^。！？.!?\n]{0,80}",
            re.I,
        ),
    ),
    # ── 含代码/数据/开源 ──
    (
        "release",
        re.compile(
            r"[^。！？.!?\n]{0,80}(?:github\.com|huggingface\.co|gitlab\.com|open.?source|code.*(?:available|release|public)|"
            r"开源|代码|公开|发布|可获取|download|checkpoint|model.?zoo|weight)[^。！？.!?\n]{0,80}",
            re.I,
        ),
    ),
    # ── 含表/图引用 ──
    (
        "figure",
        re.compile(
            r"[^。！？.!?\n]{0,80}(?:table\s+\d|fig(?:ure)?\.?\s*\d|表\s*\d|图\s*\d|as\s+shown|illustrated|depicted)[^。！？.!?\n]{0,80}",
            re.I,
        ),
    ),
]


def extract_key_sentences(full_text: str, max_chars: int = 3000) -> str:
    """从论文全文中提取高信息密度关键句（零 LLM 成本，纯正则）。

    按类别（指标/声明/严谨性/局限/开源/图表）匹配句子，去重后按类别分组输出。
    结果可直接注入各 DAG 节点的 prompt 作为「全文关键事实清单」。

    设计约束：
    - 零 LLM 调用，纯正则匹配。
    - fail-open：任何异常返回空串，不影响主评审流程。
    - 去重：同一句子（前 60 字）只出现一次，即使匹配多个模式。
    """
    try:
        t = (full_text or "").strip()
        if not t:
            return ""
        categorized: dict[str, list[str]] = {}
        seen: set[str] = set()
        for cat, pat in _KEY_SENTENCE_PATTERNS:
            hits: list[str] = []
            for m in pat.finditer(t):
                sent = m.group(0).strip()
                key = sent[:60]
                if key in seen:
                    continue
                seen.add(key)
                hits.append(sent[:200])
            if hits:
                categorized[cat] = hits
        if not categorized:
            return ""
        # 组装文本块
        cat_labels = {
            "metric": "指标/数据",
            "claim": "贡献/声明",
            "rigor": "严谨性/对比",
            "limitation": "局限/不足",
            "release": "开源/复现",
            "figure": "表图引用",
        }
        parts: list[str] = []
        total = 0
        for cat in ("claim", "metric", "rigor", "release", "limitation", "figure"):
            hits = categorized.get(cat, [])
            if not hits:
                continue
            label = cat_labels.get(cat, cat)
            # 每类最多取 5 句，控制总长度
            selected = hits[:5]
            parts.append(f"【{label}】" + " | ".join(selected))
            total += sum(len(s) for s in selected) + 10
            if total > max_chars:
                break
        block = "【全文关键事实（零LLM提取，非摘要转述）】\n" + "\n".join(parts)
        return block[:max_chars]
    except Exception:  # noqa: BLE001 - fail-open
        return ""


# ===========================================================================
# 按节点定向检索（方案 1：每个 DAG 节点拿到不同的补充文本）
# ===========================================================================

# 节点 → 检索关键词
_NODE_KEYWORDS: dict[str, list[str]] = {
    "QE": [
        "propose",
        "method",
        "experiment",
        "result",
        "conclusion",
        "achieve",
        "提出",
        "方法",
        "实验",
        "结果",
        "结论",
        "实现",
        "达到",
    ],
    "Q234": [
        "propose",
        "novel",
        "contribution",
        "ablation",
        "baseline",
        "benchmark",
        "outperform",
        "state-of-the-art",
        "limitation",
        "提出",
        "创新",
        "贡献",
        "消融",
        "基线",
        "开源",
        "局限",
    ],
    "Q2": [
        "propose",
        "novel",
        "first",
        "contribution",
        "new",
        "introduce",
        "提出",
        "首次",
        "创新",
        "新",
        "贡献",
    ],
    "Q3": [
        "ablation",
        "baseline",
        "compare",
        "significant",
        "benchmark",
        "p-value",
        "experiment",
        "evaluation",
        "dataset",
        "消融",
        "基线",
        "对比",
        "显著",
        "实验",
        "评估",
        "数据集",
    ],
    "Q4": [
        "result",
        "performance",
        "outperform",
        "state-of-the-art",
        "github",
        "open-source",
        "release",
        "benchmark",
        "结果",
        "性能",
        "开源",
        "发布",
        "代码",
    ],
    "Q5a": [
        "limitation",
        "future work",
        "however",
        "we do not",
        "caveat",
        "assumption",
        "不足",
        "局限",
        "未来",
        "仍需",
        "假设",
        "有待",
    ],
    "Q5b": [
        "propose",
        "method",
        "result",
        "achieve",
        "experiment",
        "ablation",
        "提出",
        "方法",
        "结果",
        "实现",
        "实验",
    ],
    "Q5c": [
        "propose",
        "result",
        "achieve",
        "limitation",
        "conclusion",
        "contribution",
        "提出",
        "结果",
        "实现",
        "局限",
        "结论",
        "贡献",
    ],
}


def _keyword_signal(chunk: str, keywords: list[str]) -> int:
    """计算 chunk 对给定关键词列表的命中数（不区分大小写）。"""
    low = chunk.lower()
    return sum(1 for kw in keywords if kw.lower() in low)


def retrieve_for_node(
    fulltext_ctx: dict[str, Any] | None,
    node_name: str,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """从已有的全文分块中按节点关键词检索最相关的原文块。

    复用 build_fulltext_context 产出的分块结果（不额外调用 LLM）。
    返回的块按关键词命中数降序排列。

    Args:
        fulltext_ctx: build_fulltext_context 的返回值（含 verbatim_chunks + chunks 信息）。
        node_name: DAG 节点名（QE, Q234, Q2, Q3, Q4, Q5a, Q5b, Q5c 等）。
        top_k: 返回的最相关块数量。

    Returns:
        按命中数降序的块列表 [{index, text, signal}]；fulltext_ctx 为空时返回 []。
    """
    try:
        if not fulltext_ctx:
            return []
        verbatim = fulltext_ctx.get("verbatim_chunks") or []
        if not verbatim:
            return []
        keywords = _NODE_KEYWORDS.get(node_name, _NODE_KEYWORDS.get("QE", []))
        scored = [
            {
                "index": c.get("index", i),
                "text": (c.get("text") or "")[:_VERBATIM_CHUNK_CHARS],
                "signal": _keyword_signal(c.get("text", ""), keywords),
            }
            for i, c in enumerate(verbatim)
        ]
        scored.sort(key=lambda x: x["signal"], reverse=True)
        return [c for c in scored[:top_k] if c["signal"] > 0]
    except Exception:  # noqa: BLE001 - fail-open
        return []


# ===========================================================================
# 统一组装：全局摘要 + 关键句 + 节点定向块
# ===========================================================================
def format_node_supplement(
    fulltext_ctx: dict[str, Any] | None,
    node_name: str,
    key_sentences: str,
) -> str:
    """组装节点专属的 prompt 补充文本块。

    组成：全局摘要 + 关键句清单 + 节点定向原文块。
    全局摘要和关键句对所有节点一致（提供全局上下文），
    节点定向块按检索关键词定制。

    Args:
        fulltext_ctx: build_fulltext_context 的返回值。
        node_name: DAG 节点名。
        key_sentences: extract_key_sentences 的返回值。

    Returns:
        组装后的文本块（≤ _MAX_INJECT_CHARS 字）；fulltext_ctx 为空时返回 ''。
    """
    if not fulltext_ctx:
        return ""
    parts: list[str] = []
    # 1. 全局摘要（所有节点共享）
    gs = (fulltext_ctx.get("global_summary") or "").strip()
    if gs:
        parts.append(f"【全文全局摘要】{gs}")
    # 2. 零 LLM 关键句清单（所有节点共享）
    ks = key_sentences.strip()
    if ks:
        parts.append(ks)
    # 3. 节点定向原文块
    node_chunks = retrieve_for_node(fulltext_ctx, node_name)
    if node_chunks:
        lines = [f"[{c['index']}] {c['text']}" for c in node_chunks]
        parts.append("【节点定向补充段落】\n" + "\n\n".join(lines))
    block = "\n\n".join(parts)
    return block[:_MAX_INJECT_CHARS]
