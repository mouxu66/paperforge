#!/usr/bin/env python3
"""把存量 ExperimentAudit.findings（JSON 列）回填到 audit_findings 索引表。

背景：2026-08 起 audit_findings 表由写路径自动镜像（service.sync_audit_findings），
但之前的旧审计只存 JSON 列。本脚本逐条回填，让 severity/type 过滤
（/api/experiment-audit/list?min_severity=high）与跨论文聚合
（/api/experiment-audit/findings/summary）覆盖全库。

幂等：sync_audit_findings 先删后插，重复执行无副作用。
口径：只回填 status=completed 的审计（failed/running 的 findings 非最终态）。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/backfill_audit_findings.py          # 全量
    .venv/Scripts/python.exe scripts/backfill_audit_findings.py --dry-run # 只统计
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 控制台 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from mock_api.database import SessionLocal  # noqa: E402
from mock_api.experiment_audit.schemas import coerce_findings  # noqa: E402
from mock_api.experiment_audit.service import sync_audit_findings  # noqa: E402
from mock_api.models import AuditFinding, Base, ExperimentAudit  # noqa: E402
from mock_api.database import engine  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只统计缺多少条，不写库")
    parser.add_argument(
        "--only-missing",
        action="store_true",
        help="只处理索引表中尚无记录的审计（默认全量重建，幂等先删后插）",
    )
    args = parser.parse_args()

    # 自确保 schema：目标库可能还没用新模型启动过（create_all 只建缺失的表，
    # 幂等；否则报 no such table 而非静默跳过——本脚本即为建表入口之一）
    Base.metadata.create_all(bind=engine, tables=[AuditFinding.__table__])

    db = SessionLocal()
    try:
        query = db.query(ExperimentAudit).filter(ExperimentAudit.status == "completed")
        audits = query.order_by(ExperimentAudit.created_at.asc()).all()
        done = backfilled = skipped = failed = 0
        for audit in audits:
            findings = coerce_findings(audit.findings)
            if args.only_missing:
                has_rows = (
                    db.query(AuditFinding.id)
                    .filter(AuditFinding.audit_id == audit.id)
                    .first()
                    is not None
                )
                if has_rows:
                    skipped += 1
                    continue
            if args.dry_run:
                done += 1
                continue
            try:
                sync_audit_findings(db, audit, findings)
                db.commit()
                backfilled += len(findings)
                done += 1
            except Exception as e:  # noqa: BLE001 - 单条失败不拖垮回填
                db.rollback()
                failed += 1
                print(f"[fail] audit {audit.id} (paper {audit.paper_id}): {e}")
        mode = "dry-run" if args.dry_run else f"回填 {backfilled} 条 Finding"
        print(f"[backfill_audit_findings] {mode}：处理 {done} 条审计，跳过 {skipped}，失败 {failed}")
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
