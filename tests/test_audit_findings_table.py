"""audit_findings 索引表（ExperimentAudit.findings 镜像）单测。

覆盖：
- 写路径同步：sync_audit_findings 落行（type/severity 索引列 + payload）
- 幂等：重复同步不产生重复行
- CASCADE：删论文/删审计 → 镜像行级联清理
- /list 过滤：min_severity（low/medium/high 语义包含）/ finding_type 走索引表
- /findings/summary 聚合：按论文计数 + by_type 分布
- 不加过滤时 /list 行为与旧版一致（不触索引表）
"""
from __future__ import annotations

from sqlalchemy import text

from mock_api.experiment_audit.schemas import assign_finding_ids, make_finding
from mock_api.experiment_audit.service import sync_audit_findings
from mock_api.models import AuditFinding, ExperimentAudit, Paper


def _seed_paper(db, paper_id: str) -> Paper:
    paper = Paper(id=paper_id, title=f"Paper {paper_id}", full_text="t")
    db.add(paper)
    db.commit()
    return paper


def _seed_completed_audit(db, paper_id: str, findings: list[dict]) -> ExperimentAudit:
    audit = ExperimentAudit(
        paper_id=paper_id,
        status="completed",
        findings=assign_finding_ids(findings),
    )
    db.add(audit)
    db.commit()
    sync_audit_findings(db, audit, audit.findings)
    db.commit()
    return audit


class TestSyncAuditFindings:
    def test_mirrors_rows_with_index_columns(self, db_session):
        _seed_paper(db_session, "p1")
        findings = [
            make_finding("SUSPICIOUS_DATA_PATTERN", title="a"),
            make_finding("CHART_AXIS_RISK", title="b"),
            make_finding("NUMERIC_MISMATCH", title="c"),
        ]
        audit = _seed_completed_audit(db_session, "p1", findings)

        rows = db_session.query(AuditFinding).filter(AuditFinding.audit_id == audit.id).all()
        assert len(rows) == 3
        by_type = {r.type: r for r in rows}
        assert set(by_type) == {"SUSPICIOUS_DATA_PATTERN", "CHART_AXIS_RISK", "NUMERIC_MISMATCH"}
        # 索引列与 payload 一致
        for r in rows:
            assert r.severity == r.payload["severity"]
            assert r.payload["finding_id"] == r.finding_id
        # paper_id 冗余列
        assert all(r.paper_id == "p1" for r in rows)

    def test_resync_idempotent(self, db_session):
        _seed_paper(db_session, "p2")
        audit = _seed_completed_audit(db_session, "p2", [make_finding("NUMERIC_MISMATCH")])
        # 再次同步（如审计重跑）不产生重复行
        sync_audit_findings(db_session, audit, audit.findings)
        db_session.commit()
        count = (
            db_session.query(AuditFinding).filter(AuditFinding.audit_id == audit.id).count()
        )
        assert count == 1

    def test_resync_reflects_latest_findings(self, db_session):
        _seed_paper(db_session, "p3")
        audit = _seed_completed_audit(db_session, "p3", [make_finding("NUMERIC_MISMATCH")])
        # 重审后 findings 变化 → 镜像随之更新（先删后插）
        new_findings = assign_finding_ids(
            [make_finding("RELABELED_IMAGE_REUSE"), make_finding("IMAGE_TAMPERING_CANDIDATE")]
        )
        sync_audit_findings(db_session, audit, new_findings)
        db_session.commit()
        types = {
            r.type
            for r in db_session.query(AuditFinding)
            .filter(AuditFinding.audit_id == audit.id)
            .all()
        }
        assert types == {"RELABELED_IMAGE_REUSE", "IMAGE_TAMPERING_CANDIDATE"}

    def test_cascade_on_paper_delete(self, db_session):
        # conftest 的内存 engine 未挂 PRAGMA foreign_keys=ON 监听（生产 engine
        # 有，database.py:124），本测试按连接开启以验证模型声明的 CASCADE。
        db_session.execute(text("PRAGMA foreign_keys=ON"))
        try:
            paper = _seed_paper(db_session, "p4")
            _seed_completed_audit(db_session, "p4", [make_finding("NUMERIC_MISMATCH")])
            assert db_session.query(AuditFinding).count() == 1
            db_session.delete(paper)
            db_session.commit()
            assert db_session.query(AuditFinding).count() == 0
        finally:
            db_session.execute(text("PRAGMA foreign_keys=OFF"))


class TestListAuditsFilter:
    def test_min_severity_filter(self, client, db_session):
        _seed_paper(db_session, "pa")
        _seed_paper(db_session, "pb")
        _seed_completed_audit(db_session, "pa", [make_finding("NUMERIC_MISMATCH")])  # high
        _seed_completed_audit(db_session, "pb", [make_finding("CHART_AXIS_RISK")])  # low

        body = client.get("/api/experiment-audit/list?min_severity=high").json()
        assert body["total"] == 1
        assert body["items"][0]["paper_id"] == "pa"

        # medium 门槛：high + medium 都算，low 排除
        body = client.get("/api/experiment-audit/list?min_severity=medium").json()
        assert body["total"] == 1  # CHART_AXIS_RISK 是 low，仍排除

        # 无过滤：旧版行为，全部返回
        body = client.get("/api/experiment-audit/list").json()
        assert body["total"] == 2

    def test_finding_type_filter(self, client, db_session):
        _seed_paper(db_session, "pc")
        _seed_completed_audit(
            db_session,
            "pc",
            [make_finding("NUMERIC_MISMATCH"), make_finding("GRIM_INCONSISTENCY")],
        )
        body = client.get(
            "/api/experiment-audit/list?finding_type=GRIM_INCONSISTENCY"
        ).json()
        assert body["total"] == 1
        body = client.get("/api/experiment-audit/list?finding_type=NOT_A_TYPE").json()
        assert body["total"] == 0

    def test_bad_min_severity_400(self, client):
        assert (
            client.get("/api/experiment-audit/list?min_severity=critical").status_code == 400
        )


class TestFindingsSummary:
    def test_aggregates_by_paper(self, client, db_session):
        _seed_paper(db_session, "px")
        _seed_paper(db_session, "py")
        # px：2 high；py：1 high + 1 low
        _seed_completed_audit(
            db_session,
            "px",
            [make_finding("SUSPICIOUS_DATA_PATTERN"), make_finding("IMAGE_TAMPERING_CANDIDATE")],
        )
        _seed_completed_audit(
            db_session,
            "py",
            [make_finding("RELABELED_IMAGE_REUSE"), make_finding("CHART_AXIS_RISK")],
        )

        body = client.get("/api/experiment-audit/findings/summary?min_severity=high").json()
        assert body["papers_with_findings"] == 2
        assert body["total_findings"] == 3  # low 的不计
        assert body["by_type"]["SUSPICIOUS_DATA_PATTERN"] == 1
        # 按 findings_count 降序：px(2) 在前
        assert body["papers"][0]["paper_id"] == "px"
        assert body["papers"][0]["findings_count"] == 2
        assert body["papers"][0]["paper_title"] == "Paper px"

    def test_low_threshold_counts_everything(self, client, db_session):
        _seed_paper(db_session, "pz")
        _seed_completed_audit(
            db_session, "pz", [make_finding("NUMERIC_MISMATCH"), make_finding("CHART_AXIS_RISK")]
        )
        body = client.get("/api/experiment-audit/findings/summary?min_severity=low").json()
        assert body["total_findings"] == 2

    def test_empty_and_bad_param(self, client):
        body = client.get("/api/experiment-audit/findings/summary").json()
        assert body["papers"] == [] and body["total_findings"] == 0
        assert (
            client.get("/api/experiment-audit/findings/summary?min_severity=x").status_code
            == 400
        )

    def test_backfill_covers_legacy_audit(self, client, db_session):
        """旧审计（只有 JSON、无镜像行）经 backfill 后进入聚合口径。"""
        _seed_paper(db_session, "pl")
        # 模拟旧库：直接写 ExperimentAudit，不走 sync
        legacy = ExperimentAudit(
            paper_id="pl",
            status="completed",
            findings=assign_finding_ids([make_finding("NUMERIC_MISMATCH")]),
        )
        db_session.add(legacy)
        db_session.commit()

        before = client.get("/api/experiment-audit/findings/summary").json()
        assert before["total_findings"] == 0  # 未回填：不计入（口径已声明）

        sync_audit_findings(db_session, legacy, legacy.findings)
        db_session.commit()
        after = client.get("/api/experiment-audit/findings/summary").json()
        assert after["total_findings"] == 1
