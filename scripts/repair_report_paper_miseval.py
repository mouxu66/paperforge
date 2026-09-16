# -*- coding: utf-8 -*-
"""清理「感悟报告被论文链路误评」的存量记录（一次性数据修复）。

背景
----
``POST /api/depth/v4/review-batch`` 曾未排除 ``papers.category='report'``，
自动把感悟/读后报告抓进 DEPTH v4.2 **论文**链路。论文链路按创新性/严谨性评分，
Q3 要求「实验验证数据 / 消融实验 / 基线对比」，Q5a 把这些缺失记作致命缺陷，
凑够 FATAL_VETO_MIN 即一票否决 → ``final_verdict='reject'``。

后果：同一份报告在 ``depth_reviews_v4`` 里同时存在
``kind='report'``（写得好/需深化…）与 ``kind='paper'``（拒稿）两条互相矛盾的记录；
前端「深度审稿记录」列表按后者显示，于是报告显示为「拒稿」。

本脚本删除 ``kind='paper'`` 且其 ``papers.category='report'`` 的记录
（它们全部是走错链路的产物）。代码侧的入口守卫见
``tests/test_report_paper_pipeline_boundary.py``。

安全措施
--------
1. 默认 **dry-run**，只打印不写库；必须显式 ``--apply`` 才执行。
2. ``--apply`` 前用 ``VACUUM INTO`` 做一致性快照备份（WAL 安全），
   文件名命中 ``.gitignore`` 的 ``mock_api/*.bak_pre_*``，不会进公开仓库。
3. 被删除记录逐行导出 JSON 清单到 ``deliverables/diag/``（同样已 gitignore），
   含学号，**仅供本地复核，勿提交**。
4. 删除在单个事务内完成，失败回滚。

用法
----
    # 先看会删什么
    .venv/Scripts/python.exe scripts/repair_report_paper_miseval.py
    # 确认后执行
    .venv/Scripts/python.exe scripts/repair_report_paper_miseval.py --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "mock_api" / "paperforge_mock.db"
MANIFEST_DIR = REPO_ROOT / "deliverables" / "diag"

REPORT_CATEGORY = "report"
PAPER_KIND = "paper"

SKIP_COLUMNS = frozenset(
    {"id", "paper_id", "status", "kind", "version", "created_at", "completed_at"}
)


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=60.0)
    con.row_factory = sqlite3.Row
    return con


def _select_targets(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """待删记录：论文链路的 kind='paper' 行，且其论文 category='report'。"""
    cur = con.execute(
        """
        SELECT d.* FROM depth_reviews_v4 AS d
        JOIN papers AS p ON p.id = d.paper_id
        WHERE d.kind = ? AND TRIM(LOWER(COALESCE(p.category, ''))) = ?
        ORDER BY d.paper_id, d.created_at
        """,
        (PAPER_KIND, REPORT_CATEGORY),
    )
    return cur.fetchall()


def _verdict_of(row: sqlite3.Row) -> str:
    raw = row["final_verdict"]
    if not raw:
        return "<none>"
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return "<unparseable>"
    if isinstance(parsed, dict):
        return str(parsed.get("final_verdict") or "<none>")
    return "<unparseable>"


def _reports_losing_last_record(con: sqlite3.Connection, targets: list[sqlite3.Row]) -> list[str]:
    """删掉这些行后，将不再有任何评审记录的报告 id。"""
    target_pairs = {(r["paper_id"], r["id"]) for r in targets}
    paper_ids = sorted({r["paper_id"] for r in targets})
    orphaned: list[str] = []
    for pid in paper_ids:
        cur = con.execute(
            "SELECT id FROM depth_reviews_v4 WHERE paper_id = ?",
            (pid,),
        )
        remaining = [r["id"] for r in cur.fetchall() if (pid, r["id"]) not in target_pairs]
        if not remaining:
            orphaned.append(pid)
    return orphaned


def _backup(con: sqlite3.Connection, db_path: Path, stamp: str) -> Path:
    """VACUUM INTO 一致性快照（WAL 模式下安全，且不阻塞读）。

    文件名命中 .gitignore 的 ``mock_api/*.bak_pre_*``。
    目标文件必须不存在，否则 SQLite 报错。
    """
    dest = db_path.with_name(f"{db_path.name}.bak_pre_report_miseval_{stamp}")
    if dest.exists():
        raise SystemExit(f"备份目标已存在，拒绝覆盖: {dest}")
    con.execute("VACUUM INTO ?", (str(dest),))
    return dest


def _dump_manifest(rows: list[sqlite3.Row], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = []
    for row in rows:
        item = {k: row[k] for k in row.keys() if k not in SKIP_COLUMNS}
        item["id"] = row["id"]
        item["paper_id"] = row["paper_id"]
        item["kind"] = row["kind"]
        item["version"] = row["version"]
        item["created_at"] = row["created_at"]
        payload.append(item)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理报告被论文链路误评的存量记录")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 库路径")
    parser.add_argument("--apply", action="store_true", help="真正删除（默认只 dry-run）")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"[x] 数据库不存在: {args.db}", file=sys.stderr)
        return 2

    con = _connect(args.db)
    try:
        targets = _select_targets(con)
        if not targets:
            print("[ok] 没有需要清理的记录（数据库已干净）")
            return 0

        by_verdict = Counter(_verdict_of(r) for r in targets)
        by_paper = Counter(r["paper_id"] for r in targets)
        orphaned = _reports_losing_last_record(con, targets)

        print(f"库: {args.db}")
        print(f"待删记录: {len(targets)} 条，涉及 {len(by_paper)} 份报告")
        print(f"  判决分布: {dict(by_verdict.most_common())}")
        print()
        print("受影响报告（前 20）:")
        for pid, n in by_paper.most_common(20):
            print(f"  {pid}: {n} 条")
        print()
        print(
            f"⚠️ 其中 {len(orphaned)} 份报告删除后将不再有任何评测记录"
            f"（需要重跑 reflection 链路）"
        )
        for pid in orphaned[:10]:
            print(f"     - {pid}")
        if len(orphaned) > 10:
            print(f"     … 另有 {len(orphaned) - 10} 份，完整清单见 manifest")

        if not args.apply:
            print()
            print("[dry-run] 未修改数据库。确认无误后加 --apply 执行。")
            return 0

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = _backup(con, args.db, stamp)
        print()
        print(f"[1/3] 已备份: {backup_path} ({backup_path.stat().st_size / 1e6:.1f} MB)")

        manifest_path = MANIFEST_DIR / f"report_miseval_removed_{stamp}.json"
        _dump_manifest(targets, manifest_path)
        print(f"[2/3] 已导出清单: {manifest_path}（含学号，勿提交）")

        ids = [(r["id"],) for r in targets]
        con.execute("BEGIN")
        try:
            con.executemany("DELETE FROM depth_reviews_v4 WHERE id = ?", ids)
            con.commit()
        except Exception:
            con.rollback()
            raise
        left = con.execute(
            """
            SELECT COUNT(*) AS c FROM depth_reviews_v4 AS d
            JOIN papers AS p ON p.id = d.paper_id
            WHERE d.kind = ? AND TRIM(LOWER(COALESCE(p.category, ''))) = ?
            """,
            (PAPER_KIND, REPORT_CATEGORY),
        ).fetchone()["c"]
        print(f"[3/3] 已删除 {len(ids)} 条；复查剩余同类记录: {left} 条")

        if left != 0:
            print("[x] 复查不通过，残留记录仍在，请人工介入", file=sys.stderr)
            return 1

        report_kept = con.execute(
            "SELECT COUNT(*) AS c FROM depth_reviews_v4 WHERE kind = ?",
            ("report",),
        ).fetchone()["c"]
        print(f"    报告链路记录完好: {report_kept} 条")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
