"""Semantic Scholar API 客户端 —— 拉取论文的引用数、期刊、发表时间、研究领域。

B2：集成 Semantic Scholar 丰富论文元数据。
- GET https://api.semanticscholar.org/v1/paper/{paper_id}
- 返回 citations（引用数）、influentialCitationCount（有影响力引用）、
  fieldsOfStudy（研究领域）、venue（期刊/会议名）、year（发表年份）、
  publicationDate（发表日期）
- 超时 10 秒，失败时静默降级（返回 None），不阻塞主流程

paper_id 支持：
- arXiv ID（如 "1706.03762"）需加前缀 "ARXIV:" → "ARXIV:1706.03762"
- DOI（如 "10.1145/..."）需加前缀 "DOI:"
- Semantic Scholar 内部 ID（40 字符）直接使用
"""

from __future__ import annotations

import threading
from difflib import SequenceMatcher

import requests

API_BASE = "https://api.semanticscholar.org/v1/paper"
SEARCH_API_BASE = "https://api.semanticscholar.org/graph/v1/paper/search"
TIMEOUT = 10  # 秒

# 标题相似度阈值：低于此值的 S2 命中视为无关论文
SEARCH_TITLE_SIM_THRESHOLD = 0.50


def _normalize_paper_id(paper_id: str) -> str:
    """将 PaperForge 内部的 paper_id 规范化为 Semantic Scholar 接受的格式。

    - arXiv ID（如 "1706.03762"）→ "ARXIV:1706.03762"
    - 上传的 PDF ID（"upload_xxx"）→ 无法识别，返回原值（大概率查询失败）
    - 已带前缀（ARXIV:/DOI:）→ 原样返回
    """
    pid = (paper_id or "").strip()
    if not pid:
        return pid
    if pid.startswith(("ARXIV:", "DOI:", "PMID:", "CORPUS:")):
        return pid
    # arXiv ID 格式：纯数字 + 点（如 1706.03762）或带版本号（1706.03762v1）
    if "." in pid and all(c.isdigit() or c in ".v" for c in pid):
        return f"ARXIV:{pid}"
    return pid


def fetch_paper_metadata(paper_id: str) -> dict | None:
    """从 Semantic Scholar 拉取论文元数据。

    Args:
        paper_id: PaperForge 内部 paper_id（arXiv ID 或 DOI）。

    Returns:
        {
            "citations": int,
            "influential_citations": int,
            "fields_of_study": list[str],
            "venue": str,           # 期刊/会议名
            "year": int,            # 发表年份
            "publication_date": str # ISO 日期字符串（可能为 null）
        }
        失败时返回 None（不抛异常，调用方静默降级）。
    """
    s2_id = _normalize_paper_id(paper_id)
    if not s2_id:
        return None

    url = f"{API_BASE}/{s2_id}"
    params: dict[str, str] = {
        "fields": "citationCount,influentialCitationCount,fieldsOfStudy,venue,year,publicationDate"
    }

    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    return {
        "citations": int(data.get("citationCount") or 0),
        "influential_citations": int(data.get("influentialCitationCount") or 0),
        "fields_of_study": list(data.get("fieldsOfStudy") or []),
        "venue": (data.get("venue") or "").strip(),
        "year": int(data.get("year") or 0),
        "publication_date": data.get("publicationDate") or None,
    }


def search_paper_by_title(
    title: str, author: str | None = None, limit: int = 5
) -> tuple[list[dict], str | None]:
    """按标题搜索 Semantic Scholar，返回候选列表与错误标记。

    Args:
        title: 论文标题（报告头部解析出的原论文题目）。
        author: 第一作者（可选），用于结果后过滤。
        limit: 返回结果数量上限。

    Returns:
        (hits, err):
        - hits: 符合标题相似度阈值的候选列表；无命中时为空列表。
        - err: None 表示成功；"rate_limited"/"unavailable"/"error:..." 表示失败。

    命中字段（与 resolver 约定）：
        paperId, title, authors, year, openAccessPdf, externalIds
    """
    query = (title or "").strip()
    if not query:
        return [], None

    url = SEARCH_API_BASE
    params: dict[str, str] = {
        "query": query,
        "fields": "paperId,title,authors,year,openAccessPdf,externalIds",
        "limit": str(limit),
    }

    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        return [], f"error:{e}"

    if resp.status_code == 429:
        return [], "rate_limited"
    if resp.status_code != 200:
        return [], f"unavailable:{resp.status_code}"

    try:
        data = resp.json()
    except ValueError as e:
        return [], f"error:{e}"

    raw_hits = data.get("data") or []
    want_author = (author or "").strip().lower().split(",")[0].split()[0] if author else ""

    hits: list[dict] = []
    for h in raw_hits:
        hit_title = (h.get("title") or "").strip()
        if not hit_title:
            continue
        sim = SequenceMatcher(None, query.lower(), hit_title.lower()).ratio()
        if sim < SEARCH_TITLE_SIM_THRESHOLD:
            continue

        authors = [a.get("name", "").strip() for a in (h.get("authors") or []) if a.get("name")]
        # 若提供了第一作者，优先保留作者名匹配的命中
        if want_author and not any(
            want_author in (a.lower().split()[0] if a.split() else "") for a in authors
        ):
            continue

        hits.append(
            {
                "paper_id": h.get("paperId"),
                "title": hit_title,
                "authors": authors,
                "year": int(h.get("year") or 0) or None,
                "open_access_pdf": (h.get("openAccessPdf") or {}).get("url"),
                "external_ids": h.get("externalIds") or {},
            }
        )

    return hits, None


def is_available() -> bool:
    """检查 Semantic Scholar API 是否可访问（轻量 HEAD 请求）。

    仅在调试/诊断时使用，正常流程直接调用 fetch_paper_metadata 并按 None 降级。
    """
    try:
        resp = requests.get(f"{API_BASE}/ARXIV:1706.03762", timeout=TIMEOUT)
        return resp.status_code == 200
    except requests.RequestException:
        return False


# ==================== 期刊等级评分 ====================

# 期刊等级映射表（基于学术声誉 + 影响因子近似分级）
JOURNAL_TIERS: dict[str, float] = {
    # Tier 1: 顶级综合期刊
    "Nature": 1.0,
    "Science": 1.0,
    "Cell": 1.0,
    "Proceedings of the National Academy of Sciences": 0.95,
    "PNAS": 0.95,
    # Tier 2: 顶级 AI/ML 会议
    "NeurIPS": 0.90,
    "NIPS": 0.90,
    "ICML": 0.90,
    "ICLR": 0.90,
    "CVPR": 0.90,
    "ICCV": 0.85,
    "ECCV": 0.85,
    "ACL": 0.85,
    "EMNLP": 0.80,
    "NAACL": 0.80,
    # Tier 3: 优秀期刊/会议
    "AAAI": 0.80,
    "IJCAI": 0.75,
    "JMLR": 0.85,
    "TPAMI": 0.85,
    "SIGIR": 0.75,
    "WWW": 0.75,
    "KDD": 0.80,
    "SIGMOD": 0.80,
    "VLDB": 0.80,
    "CHI": 0.75,
    "UIST": 0.70,
    "CSCW": 0.70,
    "COLT": 0.75,
    "UAI": 0.70,
    "AISTATS": 0.70,
    "TACL": 0.75,
    "CL": 0.75,
    "COLING": 0.65,
    "EACL": 0.65,
    "CoNLL": 0.60,
    # Tier 4: 其他正式出版物
    "arXiv": 0.30,
}


def get_journal_tier(venue: str) -> float:
    """根据期刊/会议名返回等级分数 (0.0~1.0)。

    匹配策略：
    1. 精确匹配 → 返回对应 tier
    2. 子串匹配 → 返回最高匹配 tier
    3. 若 venue 为空 → 默认 0.25（视作预印本/未发表）
    4. 无匹配 → 默认 0.40（未知期刊给予基础分）
    """
    venue = (venue or "").strip()
    if not venue:
        return 0.25

    # 精确匹配
    if venue in JOURNAL_TIERS:
        return JOURNAL_TIERS[venue]

    # 子串匹配（取最高分）
    best = 0.0
    for name, tier in JOURNAL_TIERS.items():
        if name.lower() in venue.lower():
            if tier > best:
                best = tier

    if best > 0:
        return best

    # 包含 "journal" / "review" / "transaction" / "proceedings" → 可能有同行评审
    venue_lower = venue.lower()
    if any(
        kw in venue_lower
        for kw in ("journal", "review", "transaction", "proceedings", "conference", "symposium")
    ):
        return 0.45

    return 0.40


def enrich_paper_async(paper_id: str, db_url: str) -> None:
    """在后台线程中异步富化论文（用于导入后不阻塞响应）。

    Args:
        paper_id: 论文 ID
        db_url: SQLAlchemy 数据库 URL（用于创建独立 session）
    """
    from .database import SessionLocal

    def _run():
        db = SessionLocal()
        try:
            from .crud import enrich_paper_from_semantic

            enrich_paper_from_semantic(db, paper_id, force=False)
        except Exception:  # noqa: BLE001 - enrichment fallback - enrichment 失败返回默认元数据
            pass  # 静默降级
        finally:
            db.close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
