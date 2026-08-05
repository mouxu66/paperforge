"""修复全库 authors 字段存储格式（一次性数据修复脚本，幂等可重跑）。

背景（2026-08-03 全库作者乱码修复）：
历史数据里 papers.authors 存在三种存储格式：
1. 标准 JSON 数组文本：["Tim Dettmers", ...]（ORM 读出为 list）
2. 双重编码：\"[\"Cosmin Pohoata\"]\"（json.dumps 写入 JSON 列导致，ORM 读出为字符串）
3. 引号包裹逗号串：\"Edward J. Hu, Yelong Shen, ...\"

格式 2/3 经 ORM 读出为字符串，旧代码 list(p.authors) 会逐字符拆分导致乱码。
本脚本将全库 authors 统一规范化为格式 1（真正的 list），并重建 paper_fts.authors
（该列存储空格连接串，损坏数据会污染按作者名检索）。

用法：
    python scripts/backfill_authors.py [--dry-run]

输出：统计修复了多少篇；--dry-run 只统计不写入。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 确保项目根目录在 sys.path（脚本位于 scripts/ 子目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from mock_api.author_utils import parse_authors  # noqa: E402
from mock_api.database import SessionLocal  # noqa: E402
from mock_api.models import Paper  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="修复全库 authors 字段存储格式")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        papers = db.query(Paper).all()
        total = len(papers)
        fixed = 0
        unchanged = 0
        for p in papers:
            parsed = parse_authors(p.authors)
            # 比较 ORM 读取值（list 或 str）与解析结果
            if parsed != p.authors:
                fixed += 1
                if not args.dry_run:
                    p.authors = parsed
            else:
                unchanged += 1

        if args.dry_run:
            print(f"[dry-run] 待修复 {fixed}/{total} 篇（已正常 {unchanged} 篇）")
            return 0

        db.commit()
        print(f"[1/2] authors 修复完成：fixed={fixed} " f"unchanged={unchanged} total={total}")

        # 重建 paper_fts.authors（空格连接解析后的人名）
        fts_fixed = 0
        rows = db.execute(
            text("SELECT paper_id, authors FROM paper_fts")
        ).all()
        for pid, fts_authors in rows:
            parsed = parse_authors(fts_authors)
            joined = " ".join(parsed)
            if joined != (fts_authors or ""):
                fts_fixed += 1
                if not args.dry_run:
                    db.execute(
                        text("UPDATE paper_fts SET authors = :a WHERE paper_id = :pid"),
                        {"a": joined, "pid": pid},
                    )
        if not args.dry_run:
            db.commit()
        print(f"[2/2] paper_fts.authors 重建完成：" f"fixed={fts_fixed} rows={len(rows)}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
