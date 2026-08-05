"""回填 report 类型论文的 chunk_count / index_size。

使用场景：
- 之前通过 /api/depth/reflection/text 上传的报告 hardcode 了 chunk_count=0 / index_size=0
- 需要批量修复已有 report 记录，使其卡片正确显示 chunks / 大小

逻辑：
1. 查询 papers 表中 category='report'、chunk_count=0、full_text 非空的记录
2. 使用 pdf_parser.split_into_chunks 重新分块
3. 更新 chunk_count 和 index_size

用法：
    python scripts/backfill_report_chunks.py
    python scripts/backfill_report_chunks.py --dry-run  # 仅预览，不写入数据库
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 将项目根目录加入 Python 路径
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from mock_api.database import SessionLocal
from mock_api.models import Paper
from mock_api.pdf_parser import split_into_chunks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="回填 report 论文的 chunk_count / index_size")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不写入数据库")
    args = parser.parse_args()

    db = SessionLocal()
    updated = 0
    skipped = 0
    failed = 0

    try:
        papers = (
            db.query(Paper)
            .filter(
                Paper.category == "report",
                Paper.source == "upload",
                Paper.chunk_count == 0,
            )
            .all()
        )
        logger.info("发现 %d 篇 chunk_count=0 的 report 论文", len(papers))

        for paper in papers:
            full_text = (paper.full_text or "").strip()
            if not full_text:
                logger.info("[跳过] full_text 为空: %s", paper.id)
                skipped += 1
                continue

            try:
                chunks = split_into_chunks(full_text)
                chunk_count = len(chunks)
                index_size = sum(len(c.encode("utf-8")) for c in chunks)

                if args.dry_run:
                    logger.info(
                        "[预览] %s -> chunks=%d, index_size=%d, text_len=%d",
                        paper.id,
                        chunk_count,
                        index_size,
                        len(full_text),
                    )
                    updated += 1
                    continue

                paper.chunk_count = chunk_count
                paper.index_size = index_size
                db.commit()
                logger.info(
                    "[更新] %s -> chunks=%d, index_size=%d, text_len=%d",
                    paper.id,
                    paper.chunk_count,
                    paper.index_size,
                    len(full_text),
                )
                updated += 1
            except Exception as e:
                logger.exception("[失败] %s: %s", paper.id, e)
                failed += 1
                db.rollback()

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    logger.info("=" * 60)
    logger.info(
        "回填完成：更新=%d, 跳过=%d, 失败=%d",
        updated,
        skipped,
        failed,
    )


if __name__ == "__main__":
    main()
