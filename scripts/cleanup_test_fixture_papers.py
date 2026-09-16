# -*- coding: utf-8 -*-
"""清理「测试夹具论文」残渣。

背景
----
`tests/test_security.py` 等用例过去会在**真实开发库**里留下夹具论文
（`ssrf-*` / `rebind-*` / `file-*`，标题统一为 "Test Paper"），
表现为论文列表里一堆 0 正文 / 0 文本块 / 0 B 的空白条目。
现已确认 `tests/conftest.py::pytest_configure` 把 `SessionLocal` 换成内存库后
**测试不再污染真库**（判决实验：跑 tests/test_security.py 前后真库行数不变）。
故本脚本只做一次性清洗。

安全护栏（宁可少删，不可误删）
----------------------------
1. 只删「标题恰为 Test Paper」或「id 命中已知测试前缀」，且**正文为空**的行；
   任何有正文的行一律保留（防误删真论文）。
2. **显式排除 `category='fraud_test'`** —— `fraud_berberine` / `kjpp` /
   `fraud_demo_001` 是实验审计（造假检测）的正式夹具，被 `scripts/` 与
   `ExperimentAuditPage` 引用，不能删。
3. 默认 dry-run，必须显式 `--apply` 才写库。
4. 写库前 `VACUUM INTO` 一致性快照；写库后复查残余，!=0 则退出码 1。

用法
----
    python scripts/cleanup_test_fixture_papers.py            # 只看计划
    python scripts/cleanup_test_fixture_papers.py --apply    # 真正执行
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "mock_api" / "paperforge_mock.db"
DIAG_DIR = ROOT / "deliverables" / "diag"

# 与每个被删 paper 关联的 (表, 列) —— 删论文时必须一起清，否则留孤儿行
RELATED: list[tuple[str, str]] = [
    ("depth_reviews_v4", "paper_id"),
    ("depth_scores", "paper_id"),
    ("paper_embeddings", "paper_id"),
    ("paper_figures", "paper_id"),
    ("pdf_annotations", "paper_id"),
    ("paper_notes", "paper_id"),
    ("translation_history", "paper_id"),
    ("experiment_audits", "paper_id"),
    ("depth_fulltext_cache", "paper_id"),
    ("audit_findings", "paper_id"),
    ("favorites", "paper_id"),
    ("paper_fts", "paper_id"),
]

TARGET_SQL = """
SELECT id AS pid, COALESCE(title,'') AS ttl, COALESCE(category,'') AS cat,
       LENGTH(COALESCE(full_text,'')) AS ftlen,
       chunk_count AS cc, index_size AS sz
FROM papers
WHERE (
        TRIM(COALESCE(title,'')) = 'Test Paper'
     OR id LIKE 'ssrf-%' OR id LIKE 'rebind-%' OR id LIKE 'file-%'
     OR id = 'clip_cnki_49d9fd75a8557465'
      )
  AND COALESCE(category,'') <> 'fraud_test'
  AND LENGTH(COALESCE(full_text,'')) = 0
ORDER BY id
"""

RESIDUE_SQL = """
SELECT COUNT(*) FROM papers
WHERE (
        TRIM(COALESCE(title,'')) = 'Test Paper'
     OR id LIKE 'ssrf-%' OR id LIKE 'rebind-%' OR id LIKE 'file-%'
     OR id = 'clip_cnki_49d9fd75a8557465'
      )
  AND COALESCE(category,'') <> 'fraud_test'
  AND LENGTH(COALESCE(full_text,'')) = 0
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="清理测试夹具论文残渣")
    ap.add_argument("--apply", action="store_true", help="真正写库（缺省只 dry-run）")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--diag-dir", default=str(DIAG_DIR), help="清单输出目录")
    args = ap.parse_args()
    diag_dir = Path(args.diag_dir)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[x] 找不到数据库: {db_path}")
        return 2

    con = sqlite3.connect(str(db_path))
    con.isolation_level = None  # 关掉隐式事务，由脚本显式 BEGIN/COMMIT
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute(TARGET_SQL)
    targets = cur.fetchall()
    ids = [r["pid"] for r in targets]

    print("=" * 66)
    print(f"待清理论文: {len(targets)} 篇")
    print("=" * 66)
    if not targets:
        print("没有命中任何测试夹具，无需清理。")
        con.close()
        return 0

    for r in targets[:8]:
        print(f"  {r['pid'][:44]:<46} cat={r['cat']:<12} text={r['ftlen']} | {r['ttl'][:20]}")
    if len(targets) > 8:
        print(f"  ... 其余 {len(targets) - 8} 篇同形态（id 为测试前缀、标题 Test Paper、正文为空）")

    # 关联行统计
    print()
    print("--- 需连带清理的关联行 ---")
    related_counts: dict[str, int] = {}
    placeholders = ",".join("?" * len(ids))
    for table, col in RELATED:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IN ({placeholders})", ids)
            n = cur.fetchone()[0]
        except sqlite3.OperationalError as exc:
            print(f"  {table:<22} 跳过（{exc}）")
            continue
        related_counts[table] = n
        if n:
            print(f"  {table:<22} {n} 行")
    # 引用关系表
    try:
        cur.execute(
            f"SELECT COUNT(*) FROM citation_sentiments "
            f"WHERE source_paper_id IN ({placeholders}) OR target_paper_id IN ({placeholders})",
            ids + ids,
        )
        n = cur.fetchone()[0]
        related_counts["citation_sentiments"] = n
        if n:
            print(f"  {'citation_sentiments':<22} {n} 行")
    except sqlite3.OperationalError as exc:
        print(f"  citation_sentiments   跳过（{exc}）")

    if not args.apply:
        print()
        print("[dry-run] 未写库。确认无误后加 --apply 执行。")
        con.close()
        return 0

    # ---- 备份 ----
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak_pre_fixture_cleanup_{stamp}")
    if backup.exists():
        backup.unlink()
    cur.execute(f"VACUUM INTO '{backup.as_posix()}'")
    print()
    print(f"[备份] {backup.name}  ({backup.stat().st_size / 1048576:.1f} MB)")

    # ---- 单事务删除 ----
    deleted: dict[str, int] = {}
    try:
        con.execute("BEGIN")
        dcur = con.cursor()
        for table, col in RELATED:
            if table not in related_counts:
                continue
            dcur.execute(f"DELETE FROM {table} WHERE {col} IN ({placeholders})", ids)
            deleted[table] = dcur.rowcount
        if "citation_sentiments" in related_counts:
            dcur.execute(
                f"DELETE FROM citation_sentiments "
                f"WHERE source_paper_id IN ({placeholders}) OR target_paper_id IN ({placeholders})",
                ids + ids,
            )
            deleted["citation_sentiments"] = dcur.rowcount
        dcur.execute(f"DELETE FROM papers WHERE id IN ({placeholders})", ids)
        deleted["papers"] = dcur.rowcount
        con.execute("COMMIT")
    except Exception as exc:  # noqa: BLE001
        con.execute("ROLLBACK")
        print(f"[x] 删除失败已回滚: {exc}")
        con.close()
        return 1

    # ---- 复查 ----
    cur.execute(RESIDUE_SQL)
    residue = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM papers")
    remain = cur.fetchone()[0]

    print()
    print("=" * 66)
    print(f"已删除 {len(ids)} 篇论文；残余 {residue} 篇；库内剩余 {remain} 篇")
    print("=" * 66)

    diag_dir.mkdir(parents=True, exist_ok=True)
    manifest = diag_dir / f"test_fixture_cleanup_{stamp}.json"
    manifest.write_text(
        json.dumps(
            {
                "timestamp": stamp,
                "db": str(db_path),
                "backup": str(backup),
                "deleted_count": len(ids),
                "deleted_ids": ids,
                "deleted_rows": deleted,
                "related_rows_before": related_counts,
                "residue_after": residue,
                "papers_remaining": remain,
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
