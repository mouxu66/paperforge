#!/usr/bin/env python3
"""回填脚本：对已入库但缺少全文（full_text）的论文重新解析。

从数据库中查询所有 full_text 为空或过短（< 200 字符）的论文，尝试：
1. 查找本地 uploads/ 目录中保存的 PDF 文件
2. 重新提取全文（pypdf + OCR 降级）
3. 分块切割并更新 chunk_count / index_size / full_text
4. 同步 FTS5 全文索引

用法：
    cd paperforge/
    python -m scripts.backfill_chunks                   # 回填所有缺少全文的论文
    python -m scripts.backfill_chunks --dry-run          # 仅预览，不写入
    python -m scripts.backfill_chunks --paper-id 1706.03762  # 指定单篇论文
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 确保 mock_api 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, func, or_
from sqlalchemy.orm import Session, sessionmaker

from mock_api import crud
from mock_api.author_utils import parse_authors
from mock_api.models import Paper as PaperORM

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill_chunks")

# chunk 切割参数（与 pdf_parser.py 一致）
CHUNK_SIZE = 512
CHUNK_OVERLAP = 64

# 回填批次大小
BATCH_SIZE = 50


def get_db_session() -> Session:
    """获取数据库会话（从默认 SQLite 路径）。"""
    db_path = Path(__file__).resolve().parent.parent / "mock_api" / "paperforge_mock.db"
    if not db_path.exists():
        logger.error("数据库文件不存在: %s", db_path)
        sys.exit(1)
    engine = create_engine(f"sqlite:///{db_path}")
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def split_into_chunks(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """将文本切割成多个 chunk（与 pdf_parser.py 保持一致）。"""
    text = text.strip()
    if not text:
        return [""]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start += chunk_size - overlap
    return chunks if chunks else [""]


def fetch_semantic_scholar_citations(paper_id: str) -> tuple[int | None, str | None]:
    """从 Semantic Scholar API 获取论文引用数和年份。

    Args:
        paper_id: arXiv ID（如 "1706.03762"）。

    Returns:
        (citations, year_str) 或 (None, None) 表示失败。
    """
    ss_id = f"ArXiv:{paper_id}"
    api_url = f"https://api.semanticscholar.org/graph/v1/paper/{ss_id}?fields=citationCount,year"
    try:
        import requests
        resp = requests.get(api_url, timeout=10)
        if resp.status_code != 200:
            logger.warning("  Semantic Scholar API 返回 HTTP %d", resp.status_code)
            return None, None
        data = resp.json()
        citations = data.get("citationCount")
        year = data.get("year")
        return citations, year
    except Exception as e:
        logger.warning("  Semantic Scholar API 请求失败: %s", e)
        return None, None


def update_paper_metadata(db: Session, paper_id: str, citations: int | None, year: int | None) -> bool:
    """更新论文引用数和年份（仅在当前值为 0 时覆盖）。"""
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        return False
    changed = False
    if citations is not None and (paper.citations == 0 or paper.citations is None):
        paper.citations = citations
        changed = True
    if year is not None and (paper.year == 0 or paper.year is None):
        paper.year = year
        changed = True
    if changed:
        db.commit()
    return changed


def reparse_paper_from_pdf(pdf_path: Path) -> tuple[str, str, int, int]:
    """从本地 PDF 文件重新解析：提取全文 + 分块。

    Returns:
        (full_text, abstract, chunk_count, index_size)
    """
    content = pdf_path.read_bytes()

    from mock_api.pdf_parser import extract_full_text

    # 1. pypdf 提取
    full_text = extract_full_text(content)

    # 2. 扫描 PDF 不再调用已退役 OCR；保留空全文并由上层标记不可提取。

    # 3. 分块切割
    chunks = split_into_chunks(full_text) if full_text else [""]
    chunk_count = len(chunks)
    index_size = sum(len(c.encode("utf-8")) for c in chunks)

    # 4. 摘要（取前 800 字符）
    abstract = full_text[:800] if full_text else ""

    return full_text, abstract, chunk_count, index_size


def update_paper_chunks(db: Session, paper_id: str, full_text: str, abstract: str, chunk_count: int, index_size: int) -> bool:
    """更新数据库中的 paper chunk 信息。

    Returns:
        True 表示更新成功，False 表示跳过。
    """
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        logger.warning("  论文 %s 在数据库中不存在，跳过", paper_id)
        return False

    # 已回填的跳过：full_text 已有足够内容（≥200 字符）则不再处理
    if paper.full_text and len(paper.full_text) >= 200:
        return False

    paper.full_text = full_text or None
    paper.abstract = abstract or paper.abstract
    paper.chunk_count = chunk_count
    paper.index_size = index_size

    # 同步 FTS5 索引
    try:
        from sqlalchemy import text
        authors_str = " ".join(parse_authors(paper.authors))
        db.execute(
            text(
                "INSERT OR REPLACE INTO paper_fts(paper_id, title, abstract, authors, full_text) "
                "VALUES (:pid, :title, :abstract, :authors, :full_text)"
            ),
            {
                "pid": paper_id,
                "title": paper.title,
                "abstract": paper.abstract or "",
                "authors": authors_str,
                "full_text": full_text or "",
            },
        )
    except Exception as e:
        logger.warning("  FTS5 索引同步失败: %s", e)

    db.commit()
    return True


def backfill_all(dry_run: bool = False, target_id: str | None = None) -> None:
    """回填所有缺少全文（full_text 为空或 < 200 字符）的论文。

    查找顺序：
    1. 本地 uploads/ 目录下保存的 PDF（id 匹配）
    2. arXiv 论文走 pdf_url 下载（通过 index_pdf_fulltext.py）
    """
    db = get_db_session()
    uploads_dir = Path(__file__).resolve().parent.parent / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    if target_id:
        papers = db.query(PaperORM).filter(PaperORM.id == target_id).all()
    else:
        papers = db.query(PaperORM).filter(
            or_(
                PaperORM.full_text == None,
                PaperORM.full_text == '',
                func.length(PaperORM.full_text) < 200,
            )
        ).order_by(PaperORM.year.desc()).limit(BATCH_SIZE).all()

    if not papers:
        logger.info("没有需要回填的论文")
        db.close()
        return

    logger.info("找到 %d 篇待回填论文（缺少全文或全文不足 200 字符）", len(papers))
    updated = 0
    skipped = 0

    for paper in papers:
        pid = paper.id
        logger.info("处理: %s — %s", pid, paper.title[:60])

        pdf_path = uploads_dir / f"{pid}.pdf"
        if not pdf_path.exists():
            logger.warning("  未找到本地 PDF 文件: %s", pdf_path)
            if pid.startswith("upload_"):
                logger.warning("  本地上传论文无 PDF 文件，跳过")
                skipped += 1
                continue
            else:
                # arXiv 论文，尝试从 pdf_url 下载
                if not paper.pdf_url:
                    logger.warning("  无 pdf_url，跳过")
                    skipped += 1
                    continue
                logger.info("  从 %s 下载 PDF...", paper.pdf_url)
                try:
                    import requests
                    # 禁用 SSL 验证以兼容旧版 Python TLS
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    resp = requests.get(paper.pdf_url, timeout=30, verify=False)
                    if resp.status_code != 200:
                        logger.warning("  下载失败: HTTP %d", resp.status_code)
                        skipped += 1
                        continue
                    content = resp.content
                    # 保存到 uploads/ 以便后续复用
                    pdf_path.parent.mkdir(parents=True, exist_ok=True)
                    pdf_path.write_bytes(content)
                except Exception as e:
                    logger.warning("  下载失败: %s", e)
                    skipped += 1
                    continue
        else:
            content = pdf_path.read_bytes()

        # 解析 PDF
        if pid.startswith("upload_") or pdf_path.exists():
            full_text, abstract, chunk_count, index_size = reparse_paper_from_pdf(pdf_path)
        else:
            # 没有本地文件，跳过
            skipped += 1
            continue

        if not full_text and chunk_count <= 1:
            logger.warning("  解析后仍然无有效文本: chunks=%d", chunk_count)
            # 还是更新一下（至少让 chunk_count=1）
            if not dry_run:
                update_paper_chunks(db, pid, "", "", 1, 0)
                updated += 1
            continue

        logger.info("  结果: chunks=%d, size=%d, text_len=%d", chunk_count, index_size, len(full_text))

        if dry_run:
            logger.info("  [DRY RUN] 跳过写入")
            updated += 1
            continue

        success = update_paper_chunks(db, pid, full_text, abstract, chunk_count, index_size)
        if success:
            updated += 1
            # 对于 arXiv 论文，通过 Semantic Scholar 补齐引用数和年份
            if not pid.startswith("upload_"):
                logger.info("  查询 Semantic Scholar 引用数...")
                cites, ss_year_raw = fetch_semantic_scholar_citations(pid)
                if cites is not None or ss_year_raw is not None:
                    ss_year = int(ss_year_raw) if ss_year_raw is not None else None
                    meta_changed = update_paper_metadata(db, pid, cites, ss_year)
                    if meta_changed:
                        logger.info("  ✅ 引用数=%s, 年份=%s", cites, ss_year)
            logger.info("  ✅ 更新成功")
        else:
            skipped += 1

    db.close()

    logger.info("=" * 50)
    logger.info("回填完成: 成功 %d, 跳过 %d", updated, skipped)
    if dry_run:
        logger.info("（DRY RUN 模式，未实际写入数据库）")


def main():
    parser = argparse.ArgumentParser(description="回填论文 chunk 数据")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入数据库")
    parser.add_argument("--paper-id", type=str, help="指定单篇论文 ID")
    args = parser.parse_args()

    backfill_all(dry_run=args.dry_run, target_id=args.paper_id)


if __name__ == "__main__":
    main()
