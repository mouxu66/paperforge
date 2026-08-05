"""原论文自动解析 → arXiv 下载 → 入库；arXiv 无匹配时 fallback 到 Semantic Scholar。

供报告上传流程调用（main.py 的 /file、/files 端点），实现
「学生提交感悟报告 → 系统自动识别引用的原论文 → 自动下载导入库」。

入口：
    resolve_and_ingest(paper_title, author=None, db) -> dict
        - 已存在库中              -> {"status": "exists",    "paper_id": ..., "source": ...}
        - 缺失且成功导入          -> {"status": "imported",  "paper_id": ..., "source": "arxiv"|"semantic_scholar", "title": ...}
        - 被 arXiv 限流            -> {"status": "rate_limited"}
        - 搜不到匹配              -> {"status": "no_match"}
        - 下载/入库失败            -> {"status": "import_failed", "detail": ...}

幂等：库里已有同名论文直接返回 exists，不重复下载。
依赖：标准库 + mock_api（sqlalchemy / pypdf / fastembed / reflection_binding / semantic_scholar）。
"""

from __future__ import annotations

import logging
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from fastapi import HTTPException

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}


# ===========================================================================
# arXiv 检索
# ===========================================================================
@dataclass
class ArxivHit:
    title: str
    abs_id: str  # 如 0706.2974v1
    pdf_url: str | None
    authors: list[str]
    year: int | None


def _norm(t: str) -> str:
    return (t or "").lower().replace("-", " ").replace(":", " ").strip()


def _year_from_id(abs_id: str) -> int | None:
    m = abs_id.split("/")[-1].split("v")[0]  # 0706.2974
    parts = m.split(".")
    if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) == 2:
        yy = int(parts[0])
        return 2000 + yy if yy < 90 else 1900 + yy
    return None


def search_arxiv(title: str, author: str | None = None, max_results: int = 8):
    """按题目(+第一作者)搜 arXiv。

    Returns (hits, err):
        hits -> list[ArxivHit]（检索成功，可能为空=无匹配）
        err  -> None 成功；"rate_limited" 被限流无法确定结果；其余为具体错误
    """
    q = f'ti:"{title}"'
    if author:
        first = author.split(",")[0].split()[0] if author.split(",")[0].split() else ""
        if first:
            q += f' AND au:"{first}"'
    url = (
        "http://export.arxiv.org/api/query?search_query="
        + urllib.parse.quote(q)
        + f"&max_results={max_results}&start=0"
    )

    data = None
    rate_limited = False
    for attempt in range(4):  # 重试退避：arXiv 限流严格，指数退避 + jitter
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PaperForge-Ingest/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            break
        except urllib.error.HTTPError as e:
            if e.code == 429:
                rate_limited = True
                wait = min(2**attempt + random.uniform(0, 1), 30)
                logger.info("arXiv 429 限流，等待 %.1fs (%d/4)", wait, attempt + 1)
                time.sleep(wait)
            else:
                logger.warning("arXiv HTTP %d: %s", e.code, e)
                return [], f"http_{e.code}"
        except Exception as e:  # noqa: BLE001 - 外部 arXiv 下载 - 网络/解析失败应 status 降级
            logger.warning("arXiv 检索失败: %s", e)
            return [], f"error:{e}"
    if data is None:
        return [], ("rate_limited" if rate_limited else "no_data")

    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        return [], f"parse:{e}"

    hits: list[ArxivHit] = []
    for entry in root.findall("a:entry", ATOM_NS):
        title_elem = entry.find("a:title", ATOM_NS)
        t = (title_elem.text or "").strip() if title_elem is not None else ""
        id_url_elem = entry.find("a:id", ATOM_NS)
        id_url = (id_url_elem.text or "").strip() if id_url_elem is not None else ""
        abs_id = id_url.split("/abs/")[-1] if "/abs/" in id_url else id_url
        pdf = None
        for l in entry.findall("a:link", ATOM_NS):
            if l.get("title") == "pdf":
                pdf = l.get("href")
        if not pdf and abs_id:
            pdf = f"https://arxiv.org/pdf/{abs_id}"
        authors: list[str] = []
        for a in entry.findall("a:author", ATOM_NS):
            name_elem = a.find("a:name", ATOM_NS)
            if name_elem is not None and name_elem.text:
                authors.append(name_elem.text.strip())
        hits.append(
            ArxivHit(
                title=t, abs_id=abs_id, pdf_url=pdf, authors=authors, year=_year_from_id(abs_id)
            )
        )
    return hits, None


def pick_best(hits: list[ArxivHit], want_title: str) -> ArxivHit | None:
    """按标题相似度选最佳匹配；阈值 0.55 防误匹配。"""
    best, best_sim = None, 0.0
    wt = _norm(want_title)
    for h in hits:
        sim = SequenceMatcher(None, wt, _norm(h.title)).ratio()
        if sim > best_sim:
            best, best_sim = h, sim
    if best and best_sim >= 0.55:
        return best
    return None


# ===========================================================================
# 下载 + 入库
# ===========================================================================
def _ssrf_safe_download(pdf_url: str, timeout: int = 90) -> bytes:
    """SSRF 安全下载：禁止自动跟随重定向，逐跳复核 host。

    默认 urllib 会跟随 302 到任意主机，外部可控 pdf_url 可借此跳到内网，
    绕过 ``validate_url_ssrf`` 的私网/回环 IP 校验。这里关闭自动重定向，
    对每跳 Location 重新跑 ``validate_url_ssrf``（拒绝则抛 HTTPException），
    重定向上限 5 跳。
    """
    from .services.pdf_proxy_service import validate_url_ssrf

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        # 不自动跟随重定向：原样返回 3xx 响应，由下方逐跳复核 host
        def http_response(self, req, response):
            return response

        https_response = http_response

    opener = urllib.request.build_opener(_NoRedirect)
    current = pdf_url
    for _ in range(6):  # 初始请求 + 最多 5 跳
        req = urllib.request.Request(current, headers={"User-Agent": "PaperForge-Ingest/1.0"})
        with opener.open(req, timeout=timeout) as r:
            code = r.getcode()
            if code in (301, 302, 303, 307, 308):
                location = r.headers.get("Location")
                if not location:
                    raise urllib.error.URLError("重定向缺少 Location 头")
                if not location.startswith(("http://", "https://")):
                    location = urllib.parse.urljoin(current, location)
                validate_url_ssrf(location)  # 逐跳 SSRF 校验（拒绝则抛 HTTPException）
                current = location
                continue
            if code >= 400:
                raise urllib.error.HTTPError(current, code, r.msg, r.headers, r)
            return r.read()
    raise urllib.error.URLError("重定向次数超限（疑似重定向环）")


def download_pdf(pdf_url: str, dest: Path) -> bool:
    from .services.pdf_proxy_service import validate_url_ssrf

    try:
        validate_url_ssrf(pdf_url)
    except HTTPException as e:
        logger.warning("PDF URL SSRF 校验失败: %s — %s", pdf_url, e.detail)
        return False

    try:
        data = _ssrf_safe_download(pdf_url, timeout=90)
    except HTTPException as e:
        logger.warning("PDF URL SSRF 校验失败（重定向）: %s — %s", pdf_url, e.detail)
        return False
    except Exception as e:  # noqa: BLE001 - 外部 arXiv 下载 - 网络/解析失败应 status 降级
        logger.warning("PDF 下载失败 %s: %s", pdf_url, e)
        return False

    dest.write_bytes(data)
    return len(data) > 1000


def _import_paper_from_bytes(
    content: bytes,
    filename: str,
    title: str,
    authors: list[str],
    year: int | None,
    source: str,
    db,
) -> str | None:
    """把 PDF 字节入库，用规范元数据覆盖，并尝试建向量。返回 paper_id。"""
    from mock_api.crud import upsert_paper_embedding
    from mock_api.models import Paper as PaperORM
    from mock_api.pdf_parser import process_one_pdf
    from mock_api.semantic_search import embed_text, is_available

    res = process_one_pdf(content, filename, db)
    if not res.success:
        logger.warning("入库失败: %s", res.error)
        return None

    pid = res.id
    p = db.query(PaperORM).filter_by(id=pid).first()
    if p is not None:
        p.title = title.strip()
        # 2026-08-03 修复：原代码把逗号串写回 JSON 列，产生「格式 3」脏数据
        # （ORM 读出字符串 → list() 逐字符拆分乱码）。必须存真正的 list。
        p.authors = list(authors) if authors else []
        if year:
            p.year = year
        p.source = source
        db.commit()

    if is_available():
        vec = embed_text(f"{title}. ")
        if vec:
            upsert_paper_embedding(db, pid, vec)
            db.commit()
    return pid


def import_paper_from_pdf(pdf_path: Path, canonical: ArxivHit, db) -> str | None:
    """入原论文库 + 用 arXiv 规范元数据覆盖脏标题 + 建向量。返回 paper_id。"""
    with open(pdf_path, "rb") as f:
        content = f.read()
    return _import_paper_from_bytes(
        content,
        pdf_path.name,
        canonical.title,
        canonical.authors,
        canonical.year,
        source="arxiv",
        db=db,
    )


def _import_paper_from_pdf_url(pdf_url: str, canonical: dict, source: str, db) -> str | None:
    """从任意 URL 下载 PDF 并入库。canonical 为 S2 命中或 arXiv 命中的通用字典。"""
    from .services.pdf_proxy_service import validate_url_ssrf

    try:
        validate_url_ssrf(pdf_url)
    except HTTPException as e:
        logger.warning("PDF URL SSRF 校验失败: %s — %s", pdf_url, e.detail)
        return None

    try:
        content = _ssrf_safe_download(pdf_url, timeout=90)
    except HTTPException as e:
        logger.warning("PDF URL SSRF 校验失败（重定向）: %s — %s", pdf_url, e.detail)
        return None
    except Exception as e:  # noqa: BLE001 - 外部 arXiv 下载 - 网络/解析失败应 status 降级
        logger.warning("PDF 下载失败 %s: %s", pdf_url, e)
        return None

    if len(content) <= 1000:
        logger.warning("PDF 太小，疑似失败页: %s", pdf_url)
        return None

    safe = "".join(c if c.isalnum() else "_" for c in canonical.get("title", "source"))[:60]
    filename = f"auto_{source}_{safe}.pdf"
    return _import_paper_from_bytes(
        content,
        filename,
        canonical.get("title", ""),
        canonical.get("authors", []),
        canonical.get("year"),
        source=source,
        db=db,
    )


# ===========================================================================
# 主入口：查库 → 搜 → 下载 → 导入
# ===========================================================================
def _import_from_arxiv_hit(best: ArxivHit, db) -> dict:
    """下载并导入一个 arXiv 命中。返回 resolver 结果字典。"""
    uploads = ROOT / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in best.abs_id)
    dest = uploads / f"auto_{safe}.pdf"
    if not best.pdf_url or not download_pdf(best.pdf_url, dest):
        return {"status": "download_failed", "arxiv": best.abs_id}
    pid = import_paper_from_pdf(dest, best, db)
    if not pid:
        return {"status": "import_failed", "arxiv": best.abs_id}
    return {
        "status": "imported",
        "paper_id": pid,
        "source": "arxiv",
        "arxiv": best.abs_id,
        "title": best.title,
    }


def _try_semantic_scholar(title: str, author: str | None, db) -> dict | None:
    """arXiv 无匹配时尝试 Semantic Scholar。成功返回 resolver 结果字典，失败返回 None。"""
    from mock_api.semantic_scholar import search_paper_by_title

    s2_hits, s2_err = search_paper_by_title(title, author, limit=5)
    if s2_err or not s2_hits:
        return None

    hit = s2_hits[0]
    # 优先用 S2 命中的 arXiv ID 回 arXiv 下载（PDF 稳定、元数据规范）
    arxiv_id = (hit.get("external_ids") or {}).get("ArXiv")
    if arxiv_id:
        abs_id = arxiv_id if "/" in arxiv_id or "v" in arxiv_id else f"{arxiv_id}v1"
        best = ArxivHit(
            title=hit["title"],
            abs_id=abs_id,
            pdf_url=f"https://arxiv.org/pdf/{abs_id}",
            authors=hit.get("authors", []),
            year=hit.get("year"),
        )
        return _import_from_arxiv_hit(best, db)

    # 其次用 openAccessPdf 直接下载
    pdf_url = hit.get("open_access_pdf")
    if pdf_url:
        pid = _import_paper_from_pdf_url(pdf_url, hit, source="semantic_scholar", db=db)
        if pid:
            return {
                "status": "imported",
                "paper_id": pid,
                "source": "semantic_scholar",
                "title": hit["title"],
            }

    return None


def resolve_and_ingest(paper_title: str, author: str | None = None, db=None) -> dict:
    """识别并导入一篇原论文。

    paper_title: 从报告头部解析出的原论文题目
    author:       原论文第一作者（可选，提升检索精度）
    db:           数据库会话（调用方提供，便于复用同一事务）

    返回结果新增 source 字段：
        - exists/imported 时 source 为 "arxiv" 或 "semantic_scholar"
        - 其余状态无 source 字段
    """
    if not paper_title or not paper_title.strip():
        return {"status": "no_title"}

    # 延迟导入，避免循环 + 仅在需要时加载
    from mock_api.database import SessionLocal
    from mock_api.reflection_binding import match_paper_by_title

    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        # 1. 查库：已存在则跳过（幂等）
        existing = match_paper_by_title(paper_title.strip(), db)
        if existing:
            # 尝试推断来源：paper_id 以数字+点开头视为 arXiv
            src = (
                "arxiv"
                if "." in existing and existing.split(".")[0].isdigit()
                else "semantic_scholar"
            )
            return {"status": "exists", "paper_id": existing, "source": src}

        # 2. 检索 arXiv（礼貌限速）
        # N3: 原 time.sleep(3.0) 硬编码不可配置/不可跳过，交互式上传延迟伤害明显。
        # 改为环境变量控制：PAPERFORGE_ARXIV_RATE_LIMIT_SEC（默认 3.0，设为 0 跳过）。
        _rate_limit = float(os.environ.get("PAPERFORGE_ARXIV_RATE_LIMIT_SEC", "3.0"))
        if _rate_limit > 0:
            time.sleep(_rate_limit)
        hits, err = search_arxiv(paper_title.strip(), author)
        if err == "rate_limited":
            return {"status": "rate_limited"}
        best = pick_best(hits, paper_title.strip()) if not err else None
        if best:
            return _import_from_arxiv_hit(best, db)

        # 3. arXiv 无匹配 → fallback 到 Semantic Scholar
        s2_result = _try_semantic_scholar(paper_title.strip(), author, db)
        if s2_result:
            return s2_result

        return {"status": "no_match", "detail": f"arXiv 检索 {len(hits)} 条均不达标"}
    finally:
        if own_db:
            db.close()
