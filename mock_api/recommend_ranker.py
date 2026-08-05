"""PaperForge 论文推荐重排层（推荐视角）· 实施骨架

独立于 DEPTH 审稿流水线，与召回层（FTS5 + 向量 + RRF）解耦。
真实业务目的：给学生推荐可引用文献、辅助写作（教师版场景）。

设计规格见 deliverables/recommend_ranking_spec.html。
本文件为 P0/P1 主体逻辑的实施骨架，**不修改 depth_eval_v4.py / depth_prompts_v4.py**。

核心思想：
- 召回层（已有，不动）负责"找得到"：FTS5 + 向量 + RRF → 候选集 Top-K
- 本重排层负责"值不值得推"：对候选计算加权重排 + MMR 多样性 + 可解释输出
- 共存决策（用户 2026-07-07 拍板）：**推荐为主 + 审稿辅助**。DEPTH 审稿视角(calibrated_score)
  作为 ≤0.10 权重的辅助维度并入推荐分，不主导排序；审稿视角与推荐视角在系统内同时实现、各司其职。

维度与权重（经验初值，须经离线评测校准；含审稿辅助后总权重自动归一）：
    match 0.35 · citable 0.25 · authority 0.20 · recency 0.10 · readability 0.10 · review 0.10(辅助)
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 复用项目已有模块（不修改它们）
# ---------------------------------------------------------------------------
embed_text: Callable[[str], list[float] | None] | None
cosine_similarity: Callable[[list[float], list[float]], float] | None

try:
    from .semantic_search import cosine_similarity as _cosine_similarity
    from .semantic_search import embed_text as _embed_text

    embed_text = _embed_text
    cosine_similarity = _cosine_similarity
except Exception as exc:  # pragma: no cover - 极端降级  # noqa: BLE001 - 极端降级 import fallback
    logger.debug("semantic_search 可选导入失败，降级为 None: %s", exc)
    embed_text = None
    cosine_similarity = None

vector_available: Callable[[], bool] = lambda: False

try:
    from .semantic_search import is_available as vector_available
except Exception as exc:  # pragma: no cover - 极端降级  # noqa: BLE001 - 极端降级 import fallback
    logger.debug("semantic_search.is_available 导入失败: %s", exc)

get_journal_tier: Callable[[str], float] | None

try:
    from .semantic_scholar import get_journal_tier as _get_journal_tier

    get_journal_tier = _get_journal_tier
except Exception as exc:  # pragma: no cover  # noqa: BLE001 - 极端降级 import fallback
    logger.debug("semantic_scholar.get_journal_tier 导入失败: %s", exc)
    get_journal_tier = None


# ===========================================================================
# 配置（对应规格「配置总表 12 项」+ 原 4 个待确认项）
# ===========================================================================
@dataclass
class RecommendConfig:
    """推荐重排层配置。所有项可热调到 Settings。

    原规格「待确认项」在此全部给出默认初值，作为可配置字段暴露：
      - match_source        : 匹配粒度（原待确认项①，默认 section）
      - citable_model_id    : 底层 LLM（原待确认项②，默认 None=启发式兜底）
      - oim_external_enrich : 是否恢复 Semantic Scholar 富化（原待确认项③，默认 False）
      - depth_coexist       : DEPTH 共存策略（原待确认项④，默认 True=解耦共存）
    """

    # —— 权重（规格表 1）——
    w_match: float = 0.35
    w_citable: float = 0.25
    w_authority: float = 0.20
    w_recency: float = 0.10
    w_readability: float = 0.10
    w_review: float = 0.10  # 审稿辅助维度（用户决策：推荐为主+审稿辅助，≤0.10，不主导）

    # —— 召回 / 匹配（规格表 2,10）——
    match_source: str = "section"  # section(章节) / paper(全文)
    retrieve_top_k: int = 40

    # —— OIM 客观层（规格表 3,4,5；第五节）——
    oim_enabled: bool = True
    oim_citations_field: str = "citations"  # papers.citations
    oim_recency_tau: int = 5  # 时效衰减时间常数（年）

    # —— 原 DEPTH 维度并入 citable 的可选上限（规格表 6；第四节）——
    legacy_weight_in_citable: float = 0.10

    # —— 多样性（规格表 7；第九批3）——
    diversity_mmr_lambda: float = 0.7  # 1=纯多样, 0=纯相关

    # —— 死维度（规格表 8；第七节）——
    hotspot_keep_display: bool = True

    # —— 输出形态（规格表 9；第六节）——
    verdict_mode: str = "strength"  # strength(推荐强度) / legacy(保留DEPTH verdict)
    readability_enabled: bool = False  # 难度适配（可选，P2）
    why_max_items: int = 3

    # —— 原 4 个待确认项（现定稿为配置默认值）——
    citable_model_id: str | None = None  # None → 启发式兜底（见 compute_citable）
    oim_external_enrich: bool = False  # True 需 Semantic Scholar API + 缓存
    depth_coexist: bool = True  # True → 本层与 DEPTH 完全解耦共存

    # —— 审稿辅助来源（用户决策：推荐为主+审稿辅助）——
    review_source: str = "cached"  # cached(读DEPTH缓存) / live(触发实时评测,重) / off(关闭)

    def total_weight(self) -> float:
        """返回当前权重之和（未归一，调用方需自行除以该值归一）。"""
        return (
            self.w_match
            + self.w_citable
            + self.w_authority
            + self.w_recency
            + self.w_readability
            + self.w_review
        )


# ===========================================================================
# 输出结构
# ===========================================================================
@dataclass
class RecommendResult:
    paper_id: str
    title: str
    recommend_score: float  # 0-1 加权推荐强度（替代 DEPTH calibrated_score）
    tier: str  # strong / good / optional（仅分档展示，不作过滤）
    dims: dict = field(default_factory=dict)  # {match,citable,authority,recency,readability}
    why: list = field(default_factory=list)  # 可解释理由
    cite_anchors: list = field(default_factory=list)  # 可点回原文的引用锚点
    hotspot_display: str | None = None  # 死维度仅展示


# ===========================================================================
# 数据获取（复用 crud / models，懒加载避免循环依赖）
# ===========================================================================
def _get_paper_meta(db, paper_id: str) -> dict | None:
    """取论文元数据：title/abstract/year/citations/journal。"""
    from .crud import get_paper

    p = get_paper(db, paper_id)
    if not p:
        return None
    return {
        "id": p.id,
        "title": p.title or "",
        "abstract": p.abstract or "",
        "year": int(getattr(p, "year", 0) or 0),
        "citations": int(getattr(p, "citations", 0) or 0),
        "journal": getattr(p, "journal", "") or "",
    }


def _get_embedding_by_ids(db, paper_ids: list[str]) -> dict[str, list[float]]:
    """批量取候选论文向量（paper_embeddings 表 → 反序列化）。"""
    from .models import PaperEmbedding as PaperEmbeddingORM
    from .semantic_search import deserialize_vector

    rows = (
        db.query(PaperEmbeddingORM.paper_id, PaperEmbeddingORM.embedding)
        .filter(PaperEmbeddingORM.paper_id.in_(paper_ids))
        .all()
    )
    out: dict[str, list[float]] = {}
    for pid, emb_str in rows:
        vec = deserialize_vector(emb_str)
        if vec:
            out[pid] = vec
    return out


# ===========================================================================
# 单维度计算
# ===========================================================================
def compute_match(
    context_vec: list[float] | None,
    paper_vec: list[float] | None,
    context_text: str = "",
    paper_abstract: str = "",
) -> float:
    """主题匹配度（权重最高）。向量余弦，无向量时降级为关键词重叠。

    余弦相似度原始范围为 [-1, 1]，作为"匹配度"使用前先映射到 [0, 1]，
    避免负相关论文被错误扣分并导致前端展示负数。
    """
    if context_vec and paper_vec and cosine_similarity is not None:
        sim = float(cosine_similarity(context_vec, paper_vec))
        return max(0.0, (sim + 1.0) / 2.0)
    # 降级：关键词（去停用词）Jaccard 重叠
    if context_text and paper_abstract:

        def toks(s: str) -> set[str]:
            return {w for w in re.findall(r"[a-zA-Z]{4,}", s.lower())}

        a, b = toks(context_text), toks(paper_abstract)
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)
    return 0.0


def compute_authority(
    citations: int | None, journal: str, citations_max: float, config: RecommendConfig
) -> tuple[float, str]:
    """引用权威度（OIM 客观层）。被引量对数归一 + 期刊分级。

    兜底：citations 缺失时该维度权重按比例分摊给 match/citable（规格第五节）。
    """
    if not config.oim_enabled:
        return 0.0, "客观层已关闭"
    # 被引量：log(1+c) 后相对候选集 max 归一
    if citations is None or citations_max <= 0:
        c_norm = 0.0
        note = "被引量缺失，已兜底"
    else:
        c_norm = math.log1p(citations) / math.log1p(citations_max)
        note = f"被引 {citations}"
    # 期刊分级（0-1，语义来自 semantic_scholar.get_journal_tier）
    jt = 0.0
    if journal and get_journal_tier is not None:
        try:
            jt = float(get_journal_tier(journal) or 0.0)
        except Exception as exc:  # noqa: BLE001 - ranker 启发式 - 单维度失败退化到默认值，不阻断推荐
            logger.debug("get_journal_tier(%s) 失败: %s", journal, exc)
            jt = 0.0
    score = 0.5 * jt + 0.5 * c_norm
    return float(max(0.0, min(1.0, score))), note


def compute_recency(
    year: int | None, config: RecommendConfig, now_year: int | None = None
) -> float:
    """时效性（OIM 客观层）。exp(-(now-year)/tau)，经典方法论保留衰减。"""
    if not config.oim_enabled or not year:
        return 0.0
    now_year = now_year or datetime.now().year
    if year > now_year:
        year = now_year
    return float(math.exp(-(now_year - year) / max(1, config.oim_recency_tau)))


def compute_citable(meta: dict | None, model_id: str | None) -> tuple[float, str]:
    """可引用性（学生场景特有）。优先 LLM 轻量评估，否则确定性启发式兜底。

    当 meta 为 None 时返回低置信度兜底分。
    集成点：当 model_id 非 None 时，应调用 PaperForge 的 OpenAI 兼容 LLM
    网关（项目已有，运行时多 Provider 切换）做轻量评估：
        "给定论文摘要与当前写作段落，评估其结论清晰度/结构自解释性/
         是否有可复用方法、数据、图表（0-1 分）"。
    下方 _llm_citable_assess 为桩函数，未接网关时自动走 _heuristic_citable。
    """
    if meta is None:
        return 0.3, "无元数据，可引用性低"
    if model_id:
        try:
            score = _llm_citable_assess(meta, model_id)
            if score is not None:
                return float(score), "LLM 评估"
        except Exception as e:  # pragma: no cover  # noqa: BLE001 - 极端降级 启发式 fallback
            logger.warning("citable LLM 评估失败，降级启发式：%s", e)
    return _heuristic_citable(meta)


def _llm_citable_assess(meta: dict, model_id: str) -> float | None:
    """桩：接项目 LLM 网关。当前未实现，返回 None 触发启发式兜底。

    TODO(集成): 调用 app 的 completion 接口，prompt 见 compute_citable 文档。
    """
    # from .llm_gateway import complete  # 项目已有网关，按此注入
    # ... 解析 0-1 分 ...
    return None


def _heuristic_citable(meta: dict | None) -> tuple[float, str]:
    """确定性启发式（无 LLM 也可用）：从摘要结构推断可引用性。

    当 meta 为 None 时返回低置信度兜底分。
    信号: 1) 含方法/结果类关键词 2) 长度适中 3) 含数字/数据声明。
    这是初版兜底，质量上限受限于无语义理解——P2 离线评测后应由 LLM 替代。
    """
    if meta is None:
        return 0.3, "无元数据，可引用性低"

    abstract = (meta.get("abstract") or "").lower()
    if not abstract:
        return 0.3, "无摘要，可引用性低"
    score = 0.4
    if re.search(r"(method|approach|propose|framework|model|algorithm)", abstract):
        score += 0.2
    if re.search(r"(result|experiment|achieve|improve|outperform|dataset|benchmark)", abstract):
        score += 0.2
    if re.search(r"\d+(\.\d+)?\s*(%|percent|times|×|fold|accuracy|f1|bleu)", abstract):
        score += 0.1
    if 80 <= len(abstract.split()) <= 400:
        score += 0.1
    return float(min(1.0, score)), "启发式评估（无 LLM）"


def compute_readability(meta: dict | None, config: RecommendConfig) -> float:
    """难度适配（可选，P2）。默认关闭时返回中性 0.5，不影响排序。

    TODO(集成): 词汇/公式密度估计，面向学生水平。
    """
    if not config.readability_enabled:
        return 0.5
    if meta is None:
        return 0.5
    # 占位：长摘要/高公式密度 → 难度高 → 分数低
    abstract = meta.get("abstract") or ""
    formula_density = abstract.count("$") / max(1, len(abstract.split()))
    return float(max(0.0, min(1.0, 1.0 - formula_density * 10)))


def compute_review_score(db, paper_id: str, config: RecommendConfig) -> tuple[float, str]:
    """审稿辅助维度（用户决策：推荐为主 + 审稿辅助，权重 ≤0.10）。

    取 DEPTH v4.1 的 calibrated_score(0-1, 已 clamp) 作为论文"审稿质量"信号，
    以 w_review(默认 0.10) 并入推荐分——仅作轻微辅助，不主导推荐排序。
      - review_source="cached"（默认）：读 DepthScore 缓存；未命中则中性兜底 0.5
      - review_source="live"：触发 DEPTH 实时评测（重，需 full_text+LLM，见 _live_review_eval 桩）
      - review_source="off"：关闭，返回 0
    """
    if config.review_source == "off":
        return 0.0, "审稿辅助已关闭"
    if config.review_source == "live":
        try:
            score = _live_review_eval(db, paper_id)
            if score is not None:
                return float(score), "DEPTH 实时评测"
        except Exception as e:  # pragma: no cover  # noqa: BLE001 - ranker fallback 读 DEPTH 缓存
            logger.warning("DEPTH 实时评测失败，降级缓存：%s", e)
    try:
        from .depth_eval_v4 import get_cached_score  # DEPTH v3 已合并到 depth_eval_v4

        cached = get_cached_score(db, paper_id)
        if cached and "calibrated_score" in cached:
            return float(cached["calibrated_score"]), "DEPTH 缓存分"
    except Exception as e:  # pragma: no cover  # noqa: BLE001 - ranker fallback 读 DEPTH 缓存
        logger.warning("读 DEPTH 缓存失败：%s", e)
    return 0.5, "未命中 DEPTH 缓存，中性兜底"


def _live_review_eval(db, paper_id: str) -> float | None:
    """桩：触发 DEPTH 实时评测。当前未实现，返回 None 走缓存兜底。

    TODO(集成): 调用 depth_eval 主入口（需论文 full_text + LLM 网关），
    返回 calibrated_score。注意耗时/成本，生产环境建议异步预计算并写缓存。
    """
    return None


# ===========================================================================
# 可解释输出：why + cite_anchors
# ===========================================================================
def _split_sentences(text: str) -> list[tuple[int, int, str]]:
    """返回 [(start, end, sentence), ...] 字符级偏移，供 cite_anchors 点回原文。"""
    spans: list[tuple[int, int, str]] = []
    for m in re.finditer(r"[^.!?]+[.!?]?", text):
        s = m.group().strip()
        if len(s) >= 8:
            spans.append((m.start(), m.end(), s))
    return spans


def build_explanation(
    meta: dict, context_vec: list[float] | None, dims: dict, config: RecommendConfig
) -> tuple[list[str], list[dict]]:
    """生成 why（可解释理由）+ cite_anchors（可点回原文的引用锚点）。"""
    why: list[str] = []
    # ① 匹配点
    why.append(f"主题匹配度 {dims.get('match', 0):.2f}：与当前写作段落语义相近")
    # ② 权威/时效
    if config.oim_enabled:
        yr = meta.get("year")
        cit = meta.get("citations")
        bits = []
        if yr:
            bits.append(f"{yr} 年")
        if cit is not None:
            bits.append(f"被引 {cit}")
        if bits:
            why.append("权威/时效：" + "、".join(bits))
    # ③ 可引用性
    why.append(f"可引用性 {dims.get('citable', 0):.2f}：结论/结构适合直接引用")
    # ④ 审稿辅助（若启用：推荐为主 + 审稿辅助）
    if config.review_source != "off" and "review" in dims:
        why.append(f"审稿质量 {dims.get('review', 0):.2f}（辅助信号，仅轻微影响排序）")

    # cite_anchors：从摘要取与上下文最相关的句子（句级锚点）
    anchors: list[dict] = []
    abstract = meta.get("abstract") or ""
    _embed_text = embed_text
    _cosine_similarity = cosine_similarity
    if abstract and context_vec and _embed_text is not None and _cosine_similarity is not None:
        spans = _split_sentences(abstract)
        scored = []
        for start, end, sent in spans:
            vec = _embed_text(sent)
            if vec:
                scored.append((_cosine_similarity(context_vec, vec), start, end, sent))
        scored.sort(key=lambda x: x[0], reverse=True)
        for _, start, end, sent in scored[: config.why_max_items]:
            anchors.append({"text": sent, "start": start, "end": end})
    return why[: config.why_max_items], anchors


# ===========================================================================
# MMR 多样性后处理（规格第九批3；第七节的 diversity）
# ===========================================================================
def mmr_rerank(items: list[tuple[str, float, list[float] | None]], lambda_: float) -> list[str]:
    """MMR：在相关性与多样性间权衡重排。items=(paper_id, score, vector)。

    lambda_ 语义：
      - 1.0 = 纯相关（按 score 排序）
      - 0.0 = 纯多样（优先选与已选集合差异最大的）
      - 0.7 = 默认平衡
    """
    if lambda_ >= 1.0:
        return [pid for pid, score, _ in sorted(items, key=lambda x: x[1], reverse=True)]
    if lambda_ <= 0.0:
        lambda_ = 0.0
    selected: list[tuple[str, float, list[float] | None]] = []
    remaining = list(items)
    while remaining:
        best: tuple[str, float, list[float] | None] = remaining[0]
        best_val = -1e9
        for pid, score, vec in remaining:
            if selected and vec and cosine_similarity is not None:
                max_sim = max(
                    (cosine_similarity(vec, sv) for _, _, sv in selected if sv),
                    default=0.0,
                )
                val = lambda_ * score - (1 - lambda_) * max_sim
            else:
                val = lambda_ * score
            if val > best_val:
                best_val = val
                best = (pid, score, vec)
        selected.append(best)
        remaining.remove(best)
    return [pid for pid, _, _ in selected]


# ===========================================================================
# 主入口
# ===========================================================================
def recommend(
    db, context_text: str, candidate_ids: list[str], config: RecommendConfig | None = None
) -> list[RecommendResult]:
    """对召回层给出的候选集做推荐重排。

    参数：
      db             : SQLAlchemy Session
      context_text   : 当前写作上下文（章节/草稿文本）
      candidate_ids  : 召回层候选论文 id 列表（来自 hybrid_search_papers）
      config         : 推荐配置（默认 RecommendConfig()）

    返回：按推荐强度（经 MMR 多样性重排后）排序的 RecommendResult 列表。
    """
    config = config or RecommendConfig()
    if not candidate_ids:
        return []

    context_vec = (
        embed_text(context_text)
        if (context_text and vector_available() and embed_text is not None)
        else None
    )

    # 批量取元数据 + 向量
    metas = {pid: _get_paper_meta(db, pid) for pid in candidate_ids}
    typed_metas: dict[str, dict] = {k: v for k, v in metas.items() if v is not None}
    if not typed_metas:
        return []
    vectors = _get_embedding_by_ids(db, list(typed_metas.keys()))

    # authority 归一所需候选集 max 被引
    citations_vals = [m.get("citations") or 0 for m in typed_metas.values()]
    citations_max = float(max(citations_vals)) if config.oim_enabled else 0.0
    now_year = datetime.now().year

    scored: list[dict] = []
    for pid, m in typed_metas.items():
        pvec = vectors.get(pid)
        # 匹配粒度：section=整段；paper=全文（当前统一用 context_vec 计算）
        match = compute_match(context_vec, pvec, context_text, m.get("abstract", ""))

        cit_score, cit_note = compute_authority(
            m.get("citations"), m.get("journal", ""), citations_max, config
        )
        rec = compute_recency(m.get("year"), config, now_year)
        citable, citable_note = compute_citable(m, config.citable_model_id)
        readability = compute_readability(m, config)
        # 审稿辅助维度（用户决策：推荐为主 + 审稿辅助，≤0.10 权重，不主导）
        review_score, review_note = compute_review_score(db, pid, config)

        total = (
            config.w_match * match
            + config.w_citable * citable
            + config.w_authority * cit_score
            + config.w_recency * rec
            + config.w_readability * readability
            + config.w_review * review_score
        )
        # 权重和未必为 1（readability 关闭 / 含审稿辅助），做归一保证 0-1
        tw = config.total_weight()
        total = total / tw if tw > 0 else 0.0

        scored.append(
            {
                "pid": pid,
                "meta": m,
                "vec": pvec,
                "score": total,
                "dims": {
                    "match": match,
                    "citable": citable,
                    "authority": cit_score,
                    "recency": rec,
                    "readability": readability,
                    "review": review_score,
                },
                "_cit_note": cit_note,
                "_citable_note": citable_note,
                "_review_note": review_note,
            }
        )

    # 初排（按加权分）
    scored.sort(key=lambda x: x["score"], reverse=True)

    # MMR 多样性重排（保留 score 用于展示）
    mmr_order = mmr_rerank(
        [(s["pid"], s["score"], s["vec"]) for s in scored],
        config.diversity_mmr_lambda,
    )
    order_map = {pid: i for i, pid in enumerate(mmr_order)}
    scored.sort(key=lambda x: order_map.get(x["pid"], 999))

    results: list[RecommendResult] = []
    for s in scored:
        m = s["meta"]
        why, anchors = build_explanation(m, context_vec, s["dims"], config)
        tier = "strong" if s["score"] >= 0.70 else "good" if s["score"] >= 0.50 else "optional"
        hotspot = None
        if config.hotspot_keep_display:
            hotspot = _hotspot_display(m.get("abstract", ""), config)
        results.append(
            RecommendResult(
                paper_id=s["pid"],
                title=m.get("title", ""),
                recommend_score=round(s["score"], 4),
                tier=tier,
                dims={k: round(v, 4) for k, v in s["dims"].items()},
                why=why,
                cite_anchors=anchors,
                hotspot_display=hotspot,
            )
        )
    return results


def _hotspot_display(abstract: str, config: RecommendConfig) -> str | None:
    """死维度（hotspot_alignment_score）仅作展示，不出分。规格第七节。"""
    if not config.hotspot_keep_display or not abstract:
        return None
    # 复用 DEPTH 的热点词表仅作提示性展示（不计入分数）
    try:
        from .depth_prompts_v4 import DEFAULT_HOTSPOTS

        hits = [h for h in DEFAULT_HOTSPOTS if h.lower() in abstract.lower()]
        return "、".join(hits) if hits else None
    except Exception as exc:  # noqa: BLE001 - ranker 启发式 - 单维度失败退化到默认值，不阻断推荐
        logger.debug("_hotspot_display 失败: %s", exc)
        return None


# ===========================================================================
# 便捷封装：直接对接召回层（可选集成点）
# ===========================================================================
def recommend_from_search(
    db, context_text: str, top_k: int | None = None, config: RecommendConfig | None = None
) -> list[RecommendResult]:
    """端到端：召回层 hybrid_search → 推荐重排。

    TODO(集成): 替换为项目实际的 hybrid_search_papers 调用。
    当前为示意：从 papers 表取全部 id 作为候选（小规模库可如此，大规模须走召回层）。
    """
    config = config or RecommendConfig()
    top_k = top_k or config.retrieve_top_k
    from .crud import get_papers

    rows, _ = get_papers(db, keyword=None, category=None, sort=None, page=1, page_size=top_k)
    # 上面仅示意；正式集成应调用 hybrid_search_papers(db, context_text, top_k)
    # 下面用最小可用实现：
    candidates = [p.id for p in rows]
    if not candidates:
        # 回退：直接查 papers 表前 top_k（演示用）
        from .models import Paper as PaperORM

        candidates = [r.id for r in db.query(PaperORM.id).limit(top_k).all()]
    return recommend(db, context_text, candidates, config)
