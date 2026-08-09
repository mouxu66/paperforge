"""实验审计编排层单测（service.py）。

覆盖：
- 正常审计（仅全文、无本地 PDF）→ completed，表格类检测 skipped
- Finding 编号与排序
- 论文不存在 → failed 落库
- checks 过滤参数生效
- get_latest_audit 取最新记录
- 读路径容错：DB 脏 findings 降级为占位条目
"""

from __future__ import annotations

from mock_api.experiment_audit.schemas import coerce_findings
from mock_api.experiment_audit.service import AuditService, get_latest_audit
from mock_api.models import Paper

# 含一个 P/R/F1 不自洽句（P=82, R=85, F1=84.6 → 差 1.13 > 0.5）
# 且缺失 seed/checkpoint 等复现信息 → 同时产出 METRIC_INCONSISTENCY + MISSING_REPRO_INFO
_AUDIT_TEXT = (
    "Our approach achieves precision = 82.0, recall = 85.0 and F1 = 84.6 "
    "on the benchmark. We train with batch size 32 for 50 epochs on a "
    "single GPU with learning rate 3e-4."
)


def _seed_paper(db, paper_id: str, full_text: str) -> Paper:
    p = Paper(id=paper_id, title="Audit Test Paper", full_text=full_text)
    db.add(p)
    db.commit()
    return p


class TestRunPaperAudit:
    def test_text_only_audit_completes(self, db_session):
        _seed_paper(db_session, "p-audit-1", _AUDIT_TEXT)
        audit = AuditService().run_paper_audit(db_session, "p-audit-1")

        assert audit.status == "completed"
        assert audit.completed_at is not None
        types = {f["type"] for f in audit.findings}
        assert "METRIC_INCONSISTENCY" in types
        assert "MISSING_REPRO_INFO" in types

        # 编号连续且 high 在前
        ids = [f["finding_id"] for f in audit.findings]
        assert ids[0] == "F-001"
        assert audit.findings[0]["severity"] == "high"

        # 表格类检测应标记 skipped（本地无 PDF）
        by_check = {c["check"]: c for c in audit.checks_run}
        assert by_check["table_extraction"]["status"] == "skipped"
        assert by_check["P0-1_numeric_mismatch"]["status"] == "skipped"
        assert by_check["P0-2_metric_consistency"]["status"] == "ok"

    def test_missing_paper_fails(self, db_session):
        audit = AuditService().run_paper_audit(db_session, "no-such-paper")
        assert audit.status == "failed"
        assert "不存在" in (audit.error_message or "")

    def test_paper_without_any_text_fails(self, db_session):
        _seed_paper(db_session, "p-empty", "")
        audit = AuditService().run_paper_audit(db_session, "p-empty")
        assert audit.status == "failed"

    def test_checks_filter(self, db_session):
        _seed_paper(db_session, "p-filter", _AUDIT_TEXT)
        audit = AuditService().run_paper_audit(
            db_session, "p-filter", checks=["P0-2_metric_consistency"]
        )
        assert audit.status == "completed"
        names = {c["check"] for c in audit.checks_run}
        assert "P0-2_metric_consistency" in names
        assert "P0-5_reproducibility" not in names
        assert all(f["type"] == "METRIC_INCONSISTENCY" for f in audit.findings)

    def test_latest_audit_returns_newest(self, db_session):
        _seed_paper(db_session, "p-latest", _AUDIT_TEXT)
        svc = AuditService()
        first = svc.run_paper_audit(db_session, "p-latest")
        second = svc.run_paper_audit(
            db_session, "p-latest", checks=["P0-5_reproducibility"]
        )
        latest = get_latest_audit(db_session, "p-latest")
        assert latest is not None
        assert latest.id == second.id
        assert latest.id != first.id


class TestCoerceFindings:
    """读路径容错：DB 里的脏 findings 不得击穿前端/报告。"""

    def test_non_list_inputs_become_empty(self):
        assert coerce_findings(None) == []
        assert coerce_findings("not-a-list") == []
        assert coerce_findings(123) == []

    def test_valid_findings_pass_through(self):
        good = [
            {
                "type": "METRIC_INCONSISTENCY",
                "severity": "high",
                "title": "P/R/F1 不自洽",
                "page": 3,
                "evidence_sources": [{"type": "text", "snippet": "..."}],
                "needs_human_review": True,
            }
        ]
        out = coerce_findings(good)
        assert len(out) == 1
        assert out[0]["type"] == "METRIC_INCONSISTENCY"
        assert out[0]["page"] == 3
        assert out[0]["evidence_sources"][0]["snippet"] == "..."

    def test_dirty_finding_becomes_placeholder(self):
        dirty = [
            {"type": "METRIC_INCONSISTENCY", "severity": "high", "title": "ok"},
            {
                "type": "METRIC_INCONSISTENCY",
                "severity": "high",
                "page": "not-an-int",
                "finding_id": "F-002",
            },
            "garbage-string-item",
            {"type": "X", "evidence_sources": ["not-a-dict"]},
        ]
        out = coerce_findings(dirty)
        # 第一条合法 → 原样保留；其余脏数据 → 占位条目
        assert len(out) == 4
        assert out[0]["type"] == "METRIC_INCONSISTENCY"
        assert out[0]["title"] == "ok"
        for f in out[1:]:
            assert f["type"] == "UNKNOWN_RECORD"
            assert f["severity"] == "low"
            assert f["needs_human_review"] is True
        # 占位条目继承原始 finding_id，报告里不出现空编号行
        assert out[1]["finding_id"] == "F-002"

    def test_coerce_roundtrip_after_assign_ids(self, db_session):
        """正常审计产物经 coerce 后不丢字段（回归：写库前校验不破坏合法数据）。"""
        _seed_paper(db_session, "p-coerce", _AUDIT_TEXT)
        audit = AuditService().run_paper_audit(db_session, "p-coerce")
        out = coerce_findings(audit.findings)
        assert len(out) == len(audit.findings)
        assert all(f["finding_id"].startswith("F-") for f in out)
        types = {f["type"] for f in out}
        assert "METRIC_INCONSISTENCY" in types
