"""回填本地上传 PDF 的解析结果。

使用场景：
- 之前上传的 PDF 因 bug 导致 chunk_count=0 或 pdf_url 为空
- uploads/ 目录下存在文件但数据库未正确记录
- 需要批量重新解析 uploads/ 下的 PDF 并更新数据库

逻辑：
1. 遍历 uploads/ 目录下所有 .pdf 文件
2. 从文件名提取 paper_id（uploads/{paper_id}.pdf）
3. 查询数据库中对应论文
4. 若 chunk_count == 0 或 pdf_url 为空，则重新解析并更新
5. 输出统计信息

用法：
    python scripts/backfill_upload_pdfs.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

# 将项目根目录加入 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from mock_api.database import SessionLocal
from mock_api.models import Paper
from mock_api.pdf_parser import extract_full_text, split_into_chunks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _get_uploads_dir() -> Path:
    return project_root / "uploads"


def main() -> None:
    uploads_dir = _get_uploads_dir()
    if not uploads_dir.exists():
        logger.error("uploads/ 目录不存在: %s", uploads_dir)
        sys.exit(1)

    pdf_files = sorted(uploads_dir.glob("*.pdf"))
    if not pdf_files:
        logger.info("uploads/ 目录下没有 PDF 文件")
        return

    db = SessionLocal()
    total = len(pdf_files)
    skipped = 0
    re_parsed = 0
    failed = 0
    missing_db = 0

    try:
        for pdf_path in pdf_files:
            paper_id = pdf_path.stem
            paper = db.query(Paper).filter(Paper.id == paper_id).first()

            if not paper:
                logger.warning("[跳过] uploads/ 存在但数据库无记录: %s", paper_id)
                missing_db += 1
                continue

            need_reparse = (
                paper.chunk_count == 0
                or paper.index_size == 0
                or not paper.pdf_url
                or not paper.full_text
            )

            if not need_reparse:
                logger.info("[跳过] 已正确解析: %s (chunks=%d, pdf_url=%s)",
                            paper_id, paper.chunk_count, bool(paper.pdf_url))
                skipped += 1
                continue

            logger.info("[重新解析] %s (当前 chunks=%d, pdf_url=%s)",
                        paper_id, paper.chunk_count, bool(paper.pdf_url))

            try:
                content = pdf_path.read_bytes()
                if not content:
                    logger.error("[失败] PDF 文件为空: %s", pdf_path.name)
                    failed += 1
                    continue

                full_text = extract_full_text(content)
                if not full_text:
                    logger.error("[失败] 无法提取任何文本: %s", pdf_path.name)
                    failed += 1
                    continue

                chunks = split_into_chunks(full_text)
                chunk_count = len(chunks)
                index_size = sum(len(c.encode("utf-8")) for c in chunks)

                paper.full_text = full_text
                paper.chunk_count = chunk_count
                paper.index_size = index_size
                paper.pdf_url = str(pdf_path.as_posix())
                db.commit()

                logger.info("[成功] %s -> chunks=%d, index_size=%d, text_len=%d",
                            paper_id, chunk_count, index_size, len(full_text))
                re_parsed += 1

            except Exception as e:
                logger.exception("[失败] %s: %s", paper_id, e)
                failed += 1
                db.rollback()

    finally:
        db.close()

    logger.info("=" * 60)
    logger.info("回填完成：总计=%d, 跳过=%d, 重新解析=%d, 失败=%d, 无数据库记录=%d",
                total, skipped, re_parsed, failed, missing_db)


if __name__ == "__main__":
    main()
