"""DEPTH × 实验审计联动红线（ADR-012 扩展）单测。

覆盖 _audit_fraud_redline 的判定矩阵：
- 无审计记录 / 审计未完成 → 裁决不变（天然不联动）
- 高危造假 Finding 不足门槛（1 条）→ 不触发
- ≥2 条高危造假 Finding → 升级 reject，理由带 [审计红线]
- 白名单外类型 / 非 high severity 不计入
- 已是 reject → 不重复升级
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from mock_api.depth_eval_v4 import (
    AUDIT_FRAUD_REDLINE_MIN,
    AUDIT_FRAUD_REDLINE_TYPES,
    _audit_fraud_redline,
)
from mock_api.experiment_audit.schemas import assign_finding_ids, make_finding
from mock_api.models import ExperimentAudit, Paper


def _seed_paper(db, paper_id: str = "p-audit-link") -> Paper:
    paper = Paper(id=paper_id, title="Audit Link Paper", full_text="some text")
    db.add(paper)
    db.commit()
    return paper


_SEQ = {"n": 0}


def _seed_audit(db, paper_id: str, findings: list[dict], status: str = "completed"):
    # created_at 用默认 datetime.now() 时，同测试内连续两次提交可能取到同微秒，
    # 导致 get_latest_audit 的 order_by(created_at.desc()) 在 tie 时取到旧审计。
    # 这里显式递增 created_at，保证「多轮审计取最新一条」的语义稳定可测。
    _SEQ["n"] += 1
    audit = ExperimentAudit(
        paper_id=paper_id,
        status=status,
        findings=assign_finding_ids(findings),
        created_at=datetime.now() + timedelta(microseconds=_SEQ["n"]),
    )
    db.add(audit)
    db.commit()
    return audit


class TestAuditFraudRedline:
    def test_no_audit_record_keeps_verdict(self, db_session):
        _seed_paper(db_session)
        verdict, reason, detail = _audit_fraud_redline("p-audit-link", "accept", "")
        assert (verdict, reason, detail) == ("accept", "", [])

    def test_incomplete_audit_ignored(self, db_session):
        _seed_paper(db_session)
        findings = [
            make_finding("IMAGE_TAMPERING_CANDIDATE", title=f"clone {i}") for i in range(2)
        ]
        _seed_audit(db_session, "p-audit-link", findings, status="running")
        verdict, _, _ = _audit_fraud_redline("p-audit-link", "accept", "")
        assert verdict == "accept"

    def test_single_high_fraud_below_threshold(self, db_session):
        _seed_paper(db_session)
        _seed_audit(db_session, "p-audit-link", [make_finding("RELABELED_IMAGE_REUSE")])
        verdict, reason, detail = _audit_fraud_redline("p-audit-link", "accept", "原理由")
        assert verdict == "accept"
        assert reason == "原理由"
        assert detail == []

    def test_two_high_frauds_trigger_reject(self, db_session):
        _seed_paper(db_session)
        findings = [
            make_finding("SUSPICIOUS_DATA_PATTERN", title="WT/H186R 数值完全相同"),
            make_finding("IMAGE_TAMPERING_CANDIDATE", title="泳道克隆"),
        ]
        _seed_audit(db_session, "p-audit-link", findings)
        verdict, reason, detail = _audit_fraud_redline("p-audit-link", "minor", "原理由")
        assert verdict == "reject"
        assert "[审计红线]" in reason and "原理由" in reason
        assert len(detail) == 2
        assert any("SUSPICIOUS_DATA_PATTERN" in d for d in detail)

    def test_whitelist_and_severity_filter(self, db_session):
        """白名单外的 high / 白名单内的非 high 都不计入门槛。"""
        _seed_paper(db_session)
        findings = [
            make_finding("NUMERIC_MISMATCH"),  # high 但不在白名单
            make_finding("CHART_AXIS_RISK"),  # 白名单外且 low
        ]
        _seed_audit(db_session, "p-audit-link", findings)
        verdict, _, _ = _audit_fraud_redline("p-audit-link", "accept", "")
        assert verdict == "accept"

    def test_already_reject_no_double_escalation(self, db_session):
        _seed_paper(db_session)
        findings = [
            make_finding("IMAGE_TAMPERING_CANDIDATE"),
            make_finding("RELABELED_IMAGE_REUSE"),
        ]
        _seed_audit(db_session, "p-audit-link", findings)
        verdict, reason, _ = _audit_fraud_redline("p-audit-link", "reject", "[统计红线] ...")
        assert verdict == "reject"
        assert "[审计红线]" not in reason  # 已 reject 不追加

    @pytest.mark.real_llm
    def test_latest_audit_wins(self, db_session):
        """多轮审计取最新一条：旧审计触红但新审计干净 → 不联动。"""
        _seed_paper(db_session)
        old = [
            make_finding("IMAGE_TAMPERING_CANDIDATE"),
            make_finding("IMAGE_TAMPERING_CANDIDATE"),
        ]
        _seed_audit(db_session, "p-audit-link", old)
        _seed_audit(db_session, "p-audit-link", [])  # 更新的干净审计
        verdict, _, _ = _audit_fraud_redline("p-audit-link", "accept", "")
        assert verdict == "accept"

    def test_constants_are_guardrails(self):
        """ADR-012：门槛与白名单是护栏常量，回归测试防误调。"""
        assert AUDIT_FRAUD_REDLINE_MIN == 2
        assert set(AUDIT_FRAUD_REDLINE_TYPES) == {
            "SUSPICIOUS_DATA_PATTERN",
            "RELABELED_IMAGE_REUSE",
            "IMAGE_TAMPERING_CANDIDATE",
        }
