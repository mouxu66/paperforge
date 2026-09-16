# -*- coding: utf-8 -*-
"""回填存量感悟报告记录的 ``verdict_reason``（一次性数据修复）。

背景
----
``_build_verdict_reason`` 在 **4 维硬校验阶段**就被调用一次，之后 verdict 与分数
还会被至少四处改写：6 维融合（补 fidelity/coverage）、fidelity 硬规则、云端第二
评审、crossval 加分。那段理由字符串当时没人重算，于是历史记录里出现两类错误：

1. **措辞错**：``average`` 有两种口径（4 维简单均值 / 6 维加权平均，后者才是 UI
   「综合得分」），旧文案一律写「4 维平均=…」，与实际展示的数字不同源。
2. **自相矛盾**：``verdict=well_done`` 的记录，理由末尾却写着 needs_depth 的结论
   「需进一步精读并展开分析」——因为 verdict 被后续阶段升级了，理由没跟上。

代码侧已修：``run_depth_reflection_sync`` 在写库前调用
``rebuild_verdict_reason``（见 ``tests/test_report_paper_pipeline_boundary.py``）。
本脚本负责把**已经落库**的旧记录按同一函数重算一遍，让页面不再显示旧措辞。

安全措施
--------
1. 默认 **dry-run**，只打印差异；必须显式 ``--apply`` 才写库。
2. ``--apply`` 前用 ``VACUUM INTO`` 做一致性快照（WAL 安全），文件名命中
   ``.gitignore`` 的 ``mock_api/*.bak_pre_*``，不会进公开仓库。
3. 前后对照清单导出到 ``deliverables/diag/``（含学号，已 gitignore，勿提交）。
4. 更新在单个事务内完成，失败回滚。
5. 只改 ``verdict_reason`` 一个字段，其余字段原样保留。

用法
----
    # 先看会改成什么
    .venv/Scripts/python.exe scripts/rebuild_reflection_reasons.py
    # 确认后执行
    .venv/Scripts/python.exe scripts/rebuild_reflection_reasons.py --apply
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_DB = REPO_ROOT / "mock_api" / "paperforge_mock.db"
MANIFEST_DIR = REPO_ROOT / "deliverables" / "diag"

REPORT_KIND = "report"


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=60.0)
    con.row_factory = sqlite3.Row
    return con


def _select_targets(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """报告链路的已完成记录（失败记录没有 reflection_result，跳过）。"""
    cur = con.execute(
        """
        SELECT id, paper_id, status, reflection_result
        FROM depth_reviews_v4
        WHERE TRIM(LOWER(COALESCE(kind, ''))) = ?
          AND status = 'completed'
          AND reflection_result IS NOT NULL
        ORDER BY created_at
        """,
        (REPORT_KIND,),
    )
    return cur.fetchall()


def _backup(con: sqlite3.Connection, db_path: Path, stamp: str) -> Path:
    dest = db_path.with_name(f"{db_path.name}.bak_pre_reason_rebuild_{stamp}")
    if dest.exists():
        raise SystemExit(f"备份目标已存在，拒绝覆盖: {dest}")
    con.execute("VACUUM INTO ?", (str(dest),))
    return dest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回填感悟报告 verdict_reason（存量数据）")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 库路径")
    parser.add_argument("--apply", action="store_true", help="真正写库（默认只 dry-run）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条（调试用）")
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"[x] 数据库不存在: {args.db}", file=sys.stderr)
        return 2

    # 延迟导入：脚本本身要能在没装齐依赖的环境下打印用法
    from mock_api.depth_eval_reflection import rebuild_verdict_reason

    con = _connect(args.db)
    try:
        rows = _select_targets(con)
        if args.limit:
            rows = rows[: args.limit]
        if not rows:
            print("[ok] 没有可处理的报告记录")
            return 0

        patches: list[tuple[str, str, str, str]] = []  # id, verdict, old, new
        skipped: list[tuple[str, str]] = []  # id, reason
        for row in rows:
            try:
                rr = json.loads(row["reflection_result"])
            except (TypeError, ValueError) as exc:
                skipped.append((row["id"], f"JSON 解析失败: {exc}"))
                continue
            if not isinstance(rr, dict):
                skipped.append((row["id"], f"reflection_result 不是对象: {type(rr).__name__}"))
                continue
            old = rr.get("verdict_reason") or ""
            try:
                new = rebuild_verdict_reason(rr)
            except Exception as exc:  # noqa: BLE001 —— 单条失败不应中断整批
                skipped.append((row["id"], f"重建失败: {exc}"))
                continue
            if new != old:
                patches.append((row["id"], str(rr.get("verdict") or ""), old, new))

        print(f"库: {args.db}")
        print(f"报告链路已完成记录: {len(rows)} 条；需修正: {len(patches)} 条；跳过: {len(skipped)} 条")
        for rid, why in skipped[:5]:
            print(f"  [skip] {rid[:8]} {why}")

        if patches:
            print()
            print("样例（前 3 条 前后对照）:")
            for rid, verdict, old, new in patches[:3]:
                print(f"  ── {rid[:8]}  verdict={verdict}")
                print(f"     旧: {old[:160]}")
                print(f"     新: {new[:160]}")
            stale_label = sum(1 for _, _, old, _ in patches if "4 维平均" in old)
            print()
            print(f"  其中旧文案含「4 维平均」的: {stale_label} 条")

        if not patches:
            print("[ok] 无需修改")
            return 0

        if not args.apply:
            print()
            print("[dry-run] 未修改数据库。确认无误后加 --apply 执行。")
            return 0

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = _backup(con, args.db, stamp)
        print()
        print(f"[1/3] 已备份: {backup_path} ({backup_path.stat().st_size / 1e6:.1f} MB)")

        MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
        manifest_path = MANIFEST_DIR / f"reflection_reason_rebuild_{stamp}.json"
        manifest_path.write_text(
            json.dumps(
                [
                    {"id": rid, "verdict": v, "old_reason": o, "new_reason": n}
                    for rid, v, o, n in patches
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[2/3] 已导出前后对照: {manifest_path}（含学号，勿提交）")

        # 逐条重写 reflection_result（只动 verdict_reason 一个键）
        id_to_new = {rid: new for rid, _, _, new in patches}
        updates: list[tuple[str, str]] = []
        for row in rows:
            if row["id"] not in id_to_new:
                continue
            rr = json.loads(row["reflection_result"])
            rr["verdict_reason"] = id_to_new[row["id"]]
            updates.append((json.dumps(rr, ensure_ascii=False), row["id"]))

        con.execute("BEGIN")
        try:
            con.executemany(
                "UPDATE depth_reviews_v4 SET reflection_result = ? WHERE id = ?",
                updates,
            )
            con.commit()
        except Exception:
            con.rollback()
            raise
        print(f"[3/3] 已更新 {len(updates)} 条")

        # 复查：重算后应无差异
        residual = 0
        for row in _select_targets(con):
            rr = json.loads(row["reflection_result"])
            if rebuild_verdict_reason(rr) != (rr.get("verdict_reason") or ""):
                residual += 1
        print(f"    复查残余差异: {residual} 条")
        if residual:
            print("[x] 复查不通过，请人工介入", file=sys.stderr)
            return 1
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
