"""P0-12 全文相似度检测（重复发表 / 论文工厂线索）。

补齐图像取证覆盖不到的撤稿类型：图片复用（P0-9）抓的是「同一张图被
复用」，本检查抓的是「同一篇正文被逐字/近逐字复用」——重复提交、
自我剽窃、切香肠发表的典型手法。

边界（诚实声明）：词级 n-gram 只对**逐字重叠**敏感，抓不到「同一项
研究被换词重写 + 重画图表」的语义级重复（实测 Berberine/KJPP 词 5-gram
Jaccard 仅 0.02）；那类需要语义嵌入，属于后续路线。

策略（确定性，零外部依赖，纯标准库）：
1. 归一化全文（小写 + 去标点 + 折叠空白）→ 词级 5-gram shingling。
2. 与库内所有其他非空全文论文两两比对 Jaccard 相似度。
3. Jaccard >= 阈值 → TEXT_DUPLICATION_CANDIDATE（severity=high，
   一律 needs_human_review=True）。

红线：只产出「相似线索」，不判定重复发表/造假；每条 Finding 带
normal_explanation 交人工终审。
"""

from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from ..models import Paper as PaperORM
from .schemas import make_finding

logger = logging.getLogger(__name__)

# 词级 n-gram 粒度：5 词窗口在「正文近重复」与「巧合短语重叠」间平衡。
DEFAULT_SHINGLE_SIZE = 5
# 全文 5-gram Jaccard 阈值：>=0.55 视为强重复信号（正常同领域论文通常 <0.2）。
DEFAULT_JACCARD_THRESHOLD = 0.55
# 最多输出的 Finding 条数（按相似度降序），避免大库刷屏。
DEFAULT_TOP_K = 5
# 少于该词数的论文视为无实质全文，跳过比对。
_MIN_WORDS = 50

_WORD_RE = re.compile(r"\W+")


def _normalize(text: str) -> str:
    """归一化全文：小写、非单词字符折叠为单空格、去首尾空白。"""
    return _WORD_RE.sub(" ", (text or "").lower()).strip()


def _shingles(text: str, size: int = DEFAULT_SHINGLE_SIZE) -> set[str]:
    """词级 n-gram 集合；词数不足 n 时返回空集。"""
    words = text.split()
    if len(words) < size:
        return set()
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    """集合 Jaccard 相似度；任一方为空返回 0.0。"""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a) + len(b) - inter
    return inter / union if union else 0.0


def detect_text_duplication(
    db: Session,
    paper_id: str,
    *,
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
    jaccard_threshold: float = DEFAULT_JACCARD_THRESHOLD,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict]:
    """检测某论文全文与库内其他论文的相似度，返回重复发表候选线索。

    只比较「有非空 full_text」的其他论文（arXiv 导入通常无全文，天然排除）。
    结果按相似度降序、paper_id 升序稳定排序，最多返回 top_k 条。
    """
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if paper is None:
        return []

    src = _normalize(paper.full_text or "")
    if len(src.split()) < _MIN_WORDS:
        return []
    src_shingles = _shingles(src, shingle_size)
    if not src_shingles:
        return []

    others = (
        db.query(PaperORM)
        .filter(PaperORM.id != paper_id)
        .filter(PaperORM.full_text.isnot(None))  # type: ignore[arg-type]
        .filter(PaperORM.full_text != "")
        .all()
    )

    scored: list[tuple[float, str, PaperORM]] = []
    for other in others:
        other_norm = _normalize(other.full_text or "")
        if len(other_norm.split()) < _MIN_WORDS:
            continue
        other_shingles = _shingles(other_norm, shingle_size)
        if not other_shingles:
            continue
        sim = _jaccard(src_shingles, other_shingles)
        if sim >= jaccard_threshold:
            scored.append((sim, other.id, other))

    scored.sort(key=lambda x: (-x[0], x[1]))

    findings: list[dict] = []
    for sim, _other_id, other in scored[:top_k]:
        findings.append(
            make_finding(
                "TEXT_DUPLICATION_CANDIDATE",
                title=f"全文与库内论文「{other.title[:60]}」高度相似",
                claim=(
                    f"与 {other.id}（{other.title[:120]}）的 {shingle_size}-gram "
                    f"Jaccard 相似度 {sim:.3f}"
                ),
                computed=(
                    f"jaccard={sim:.3f}, shingle_size={shingle_size}, "
                    f"threshold={jaccard_threshold}, other_paper_id={other.id}"
                ),
                method=(
                    f"全文 {shingle_size}-gram shingling + Jaccard 相似度"
                    f"（库内两两比对，阈值 {jaccard_threshold}）"
                ),
                evidence_sources=[
                    {"type": "text", "snippet": f"{other.id}: {other.title[:300]}"},
                ],
                normal_explanation=(
                    "同领域论文共享方法学描述、常用句式或参考文献，可能天然高度相似；"
                    "是否构成重复发表/论文工厂需人工核对两文的核心方法、结果段落是否实质相同"
                ),
                needs_human_review=True,
            )
        )
    return findings


# 全库扫描用的候选阈值（比单篇检测低：清单要召回，结论交人工）。
DEFAULT_CORPUS_JACCARD_THRESHOLD = 0.30
DEFAULT_CORPUS_MAX_PAIRS = 200

# ── 语义级重复（换词重写）阈值 ────────────────────────────
# 余弦 ≥ 0.85（同义改写通常 0.85-0.95，无关论文 <0.6）且表层 5-gram Jaccard
# ≤ 0.35（避免与表层检测重复报警）→ 措辞不同但语义相同 = 换词重写线索。
DEFAULT_COSINE_THRESHOLD = 0.85
DEFAULT_SURFACE_JACCARD_MAX = 0.35
# 嵌入片段上限（bge-small 上下文 ~512 token ≈ 3K 字符；截取开头保方法与结果措辞）
_MAX_EMBED_CHARS = 3000


def _corpus_entries(
    db: Session,
    *,
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
    min_words: int = _MIN_WORDS,
    limit_papers: int = 0,
) -> list[tuple[str, str, set[str]]]:
    """库内所有有非空全文的论文 → [(paper_id, title, shingles)]（低于 min_words 的跳过）。"""
    query = (
        db.query(PaperORM)
        .filter(PaperORM.full_text.isnot(None))  # type: ignore[arg-type]
        .filter(PaperORM.full_text != "")
        .order_by(PaperORM.id)
    )
    if limit_papers:
        query = query.limit(limit_papers)
    entries: list[tuple[str, str, set[str]]] = []
    for p in query.all():
        norm = _normalize(p.full_text or "")
        if len(norm.split()) < min_words:
            continue
        sh = _shingles(norm, shingle_size)
        if sh:
            entries.append((p.id, p.title or p.id, sh))
    return entries


def scan_corpus_duplication(
    db: Session,
    *,
    shingle_size: int = DEFAULT_SHINGLE_SIZE,
    jaccard_threshold: float = DEFAULT_CORPUS_JACCARD_THRESHOLD,
    min_words: int = _MIN_WORDS,
    max_pairs: int = DEFAULT_CORPUS_MAX_PAIRS,
    limit_papers: int = 0,
) -> list[dict]:
    """全库 full_text 两两相似度扫描 → 重复发表/论文工厂候选对清单。

    倒排索引（shingle → 论文列表）只对「至少共享 1 个 shingle」的论文对做
    精确 Jaccard 计算，避免全库 O(N²) 集合求交；纯标准库、零外部依赖。
    输出按 jaccard 降序：``{paper_a, paper_b, jaccard, title_a, title_b}``，
    仅供人工复核（红线：不出判定结论）。
    """
    entries = _corpus_entries(
        db, shingle_size=shingle_size, min_words=min_words, limit_papers=limit_papers
    )
    if len(entries) < 2:
        return []

    # shingle → 论文下标列表（倒排索引）
    inverted: dict[str, list[int]] = {}
    for i, (_, _, sh) in enumerate(entries):
        for s in sh:
            inverted.setdefault(s, []).append(i)

    # 对每篇论文，只对共享 ≥1 个 shingle 的其他论文精确算 Jaccard
    scored: list[tuple[float, int, int]] = []
    n = len(entries)
    for i in range(n):
        sh_i = entries[i][2]
        counter: dict[int, int] = {}
        for s in sh_i:
            for j in inverted[s]:
                if j > i:
                    counter[j] = counter.get(j, 0) + 1
        for j, shared in counter.items():
            sh_j = entries[j][2]
            union = len(sh_i) + len(sh_j) - shared
            if union <= 0:
                continue
            sim = shared / union
            if sim >= jaccard_threshold:
                scored.append((sim, i, j))

    scored.sort(key=lambda x: (-x[0], x[1], x[2]))
    out: list[dict] = []
    for sim, i, j in scored[:max_pairs]:
        pid_a, title_a, _ = entries[i]
        pid_b, title_b, _ = entries[j]
        out.append(
            {
                "paper_a": pid_a,
                "paper_b": pid_b,
                "jaccard": round(sim, 4),
                "title_a": title_a,
                "title_b": title_b,
            }
        )
    return out


# ── 语义级重复（换词重写）检测 ────────────────────────────


def semantic_available() -> bool:
    """语义级检测的硬依赖：fastembed 向量模型可用性（缺则显式 skipped）。"""
    try:
        from ..semantic_search import is_available

        return is_available()
    except Exception:  # noqa: BLE001 - 探测失败视同不可用
        return False


def _cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度（两向量等长；embed 输出已 L2 归一化，退化为点积）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _embed_excerpt(norm_text: str) -> str:
    """截取归一化全文片段用于嵌入（bge 上下文有限，截开头保方法/结果措辞）。"""
    return (norm_text or "")[:_MAX_EMBED_CHARS]


def detect_semantic_duplication(
    db: Session,
    paper_id: str,
    *,
    cosine_threshold: float = DEFAULT_COSINE_THRESHOLD,
    surface_jaccard_max: float = DEFAULT_SURFACE_JACCARD_MAX,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict]:
    """语义级重复检测：目标论文 vs 库内候选论文（至少共享 1 个 shingle）的改写线索。

    双信号判定：embedding 余弦 ≥ 阈值（语义相近）且 词级 Jaccard ≤ 上限
    （措辞不同）→ SEMANTIC_DUPLICATION_CANDIDATE（换词重写/切香肠线索）。

    候选集 = 与目标共享 ≥1 个 5-gram 的论文（倒排索引，秒级）；完全零重叠的
    深度改写不在单篇检测覆盖内，走 scripts/scan_text_duplication.py --semantic
    的全库全对语义扫描。fastembed 不可用时返回空（调用方标 skipped）。
    """
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if paper is None:
        return []
    src = _normalize(paper.full_text or "")
    if len(src.split()) < _MIN_WORDS:
        return []
    src_shingles = _shingles(src, DEFAULT_SHINGLE_SIZE)
    if not src_shingles:
        return []

    # 候选集：与目标至少共享 1 个 shingle 的其他论文（倒排索引）
    entries = [e for e in _corpus_entries(db) if e[0] != paper_id]
    inverted: dict[str, list[int]] = {}
    for i, (_, _, sh) in enumerate(entries):
        for s in sh:
            inverted.setdefault(s, []).append(i)
    cand_idx: set[int] = set()
    for s in src_shingles:
        cand_idx.update(inverted.get(s, ()))
    if not cand_idx:
        return []

    try:
        from ..semantic_search import embed_batch

        src_vec = embed_batch([_embed_excerpt(src)])
        if not src_vec:
            return []
        src_vec = src_vec[0]
        cand_texts = [
            _embed_excerpt(_normalize(_fulltext_of(db, entries[i][0]))) for i in sorted(cand_idx)
        ]
        cand_vecs = embed_batch(cand_texts)
        if not cand_vecs or len(cand_vecs) != len(cand_idx):
            return []
    except Exception:  # noqa: BLE001 - 嵌入不可用属降级，返回空
        logger.warning("[audit] 语义级重复检测嵌入失败，返回空")
        return []

    scored: list[tuple[float, float, str]] = []
    for i, vec in zip(sorted(cand_idx), cand_vecs):
        cosine = _cosine(src_vec, vec)
        if cosine < cosine_threshold:
            continue
        jaccard = _jaccard(src_shingles, entries[i][2])
        if jaccard > surface_jaccard_max:
            continue
        scored.append((cosine, jaccard, entries[i][0]))
    scored.sort(key=lambda x: (-x[0], x[2]))

    findings: list[dict] = []
    for cosine, jaccard, other_id in scored[:top_k]:
        other = db.query(PaperORM).filter(PaperORM.id == other_id).first()
        other_title = other.title if other else other_id
        findings.append(
            make_finding(
                "SEMANTIC_DUPLICATION_CANDIDATE",
                title=f"全文与库内论文「{other_title[:60]}」语义高度相似但措辞不同",
                claim=(
                    f"与 {other_id}（{other_title[:120]}）嵌入余弦相似度 {cosine:.3f}"
                    f"但词级 Jaccard 仅 {jaccard:.3f}，疑似换词重写/切香肠发表"
                ),
                computed=(
                    f"cosine={cosine:.3f}, surface_jaccard={jaccard:.3f}, "
                    f"thresholds=(cosine≥{cosine_threshold}, jaccard≤{surface_jaccard_max}), "
                    f"other_paper_id={other_id}"
                ),
                method=(
                    "embedding 余弦相似度（语义）+ 词级 5-gram Jaccard（表层）双信号；"
                    "表层低 + 语义高 → 换词重写线索"
                ),
                evidence_sources=[
                    {"type": "text", "snippet": f"{other_id}: {other_title[:300]}"},
                ],
                normal_explanation=(
                    "同领域论文可能就相似结论用不同措辞独立表述；是否构成重复发表/"
                    "切香肠需人工核对两文的实验设计与数据是否实质相同"
                ),
                needs_human_review=True,
            )
        )
    return findings


def _fulltext_of(db: Session, paper_id: str) -> str:
    """查某篇论文的原始 full_text（语义嵌入用原文而非 shingle 集）。"""
    row = db.query(PaperORM.full_text).filter(PaperORM.id == paper_id).first()
    return row[0] if row else ""
