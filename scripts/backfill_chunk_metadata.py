# -*- coding: utf-8 -*-
"""回填 `chunk_count` / `index_size`。

背景
----
这两列只在 **PDF 解析链路**里被写入（`mock_api/pdf_parser.py:1056-1057`）。
通过 arXiv 元数据导入、或只回填了 `full_text` 的论文，正文在库里但两列一直是 0，
卡片于是显示「0 个文本块 / 0 B」，看起来像文件损坏，实际只是**统计字段没回填**。

同样的坑在感悟报告链路上已经修过一次
（`mock_api/routers/reflection.py:339` → "修复报告卡片显示 0 chunks / 0 B"），
本脚本把同一口径套用到存量论文上：只对**有正文**的行重算，正文为空的行保持 0
（不去复刻解析器 `chunks = [""] → count=1` 那种把空文档记成 1 块的行为）。

用法
----
    python scripts/backfill_chunk_metadata.py            # 只看计划
    python scripts/backfill_chunk_metadata.py --apply    # 真正执行
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mock_api.pdf_parser import split_into_chunks  # noqa: E402

DB_PATH = ROOT / "mock_api" / "paperforge_mock.db"
DIAG_DIR = ROOT / "deliverables" / "diag"

TARGET_SQL = """
SELECT id AS pid, COALESCE(full_text,'') AS ft, chunk_count AS cc, index_size AS sz
FROM papers
WHERE LENGTH(COALESCE(full_text,'')) > 0
  AND (chunk_count IS NULL OR chunk_count = 0
       OR index_size IS NULL OR index_size = 0)
ORDER BY id
"""

RESIDUE_SQL = """
SELECT COUNT(*) FROM papers
WHERE LENGTH(COALESCE(full_text,'')) > 0
  AND (chunk_count IS NULL OR chunk_count = 0
       OR index_size IS NULL OR index_size = 0)
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="回填 chunk_count / index_size")
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省只 dry-run）")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--diag-dir", default=str(DIAG_DIR), help="清单输出目录")
    args = ap.parse_args()
    diag_dir = Path(args.diag_dir)

    db_path = Path(args.db)
    con = sqlite3.connect(str(db_path))
    con.isolation_level = None
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute(TARGET_SQL)
    rows = cur.fetchall()
    print("=" * 66)
    print(f"待回填论文: {len(rows)} 篇（有正文但 chunk_count/index_size 为 0）")
    print("=" * 66)
    if not rows:
        print("无需回填。")
        con.close()
        return 0

    plan: list[tuple[str, int, int]] = []
    for r in rows:
        chunks = split_into_chunks(r["ft"])
        plan.append((r["pid"], len(chunks), sum(len(c.encode("utf-8")) for c in chunks)))

    for pid, cc, sz in plan[:8]:
        print(f"  {pid[:46]:<48} -> {cc:>4} 块 / {sz:>9} B")
    if len(plan) > 8:
        tail = plan[8:]
        print(f"  ... 其余 {len(tail)} 篇：合计 {sum(x[1] for x in tail)} 块 / "
              f"{sum(x[2] for x in tail)} B")

    total_chunks = sum(x[1] for x in plan)
    total_bytes = sum(x[2] for x in plan)
    print()
    print(f"合计将写入: {len(plan)} 篇 / {total_chunks} 块 / {total_bytes} B")

    if not args.apply:
        print()
        print("[dry-run] 未写库。确认无误后加 --apply 执行。")
        con.close()
        return 0

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak_pre_chunk_backfill_{stamp}")
    if backup.exists():
        backup.unlink()
    cur.execute(f"VACUUM INTO '{backup.as_posix()}'")
    print()
    print(f"[备份] {backup.name}  ({backup.stat().st_size / 1048576:.1f} MB)")

    try:
        con.execute("BEGIN")
        cur.executemany(
            "UPDATE papers SET chunk_count = ?, index_size = ? WHERE id = ?",
            [(cc, sz, pid) for pid, cc, sz in plan],
        )
        con.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001
        con.execute("ROLLBACK")
        print(f"[x] 回填失败已回滚: {exc}")
        con.close()
        return 1

    cur.execute(RESIDUE_SQL)
    residue = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM papers WHERE chunk_count > 0")
    nonzero = cur.fetchone()[0]
    cur.execute("PRAGMA integrity_check")
    integrity = cur.fetchone()[0]

    print()
    print("=" * 66)
    print(f"回填完成；残余未回填 {residue} 篇；chunk_count>0 现有 {nonzero} 篇")
    print(f"完整性检查: {integrity}")
    print("=" * 66)

    diag_dir.mkdir(parents=True, exist_ok=True)
    manifest = diag_dir / f"chunk_metadata_backfill_{stamp}.json"
    manifest.write_text(
        json.dumps(
            {
                "timestamp": stamp,
                "db": str(db_path),
                "backup": str(backup),
                "updated_count": len(plan),
                "total_chunks": total_chunks,
                "total_bytes": total_bytes,
                "sample": plan[:20],
                "residue_after": residue,
                "integrity_check": integrity,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    try:
        shown = manifest.relative_to(ROOT)
    except ValueError:
        shown = manifest  # 输出目录在项目外（如测试用临时目录）
    print(f"[清单] {shown}")
    con.close()
    return 1 if residue else 0


if __name__ == "__main__":
    sys.exit(main())
