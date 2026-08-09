"""实验审计 P0-5 可复现性清单 + schemas 单测。"""

from __future__ import annotations

import pytest
from mock_api.experiment_audit import reproducibility
from mock_api.experiment_audit.schemas import (
    FINDING_TYPES,
    assign_finding_ids,
    make_finding,
)

_COMPLETE_TEXT = """
We train all models with random seed 42 and report the standard deviation
over five runs. Experiments run on a single NVIDIA RTX 3090 GPU.
We use a learning rate of 1e-4, batch size 32, for 100 epochs with
early stopping on the validation set.
"""

_PARTIAL_TEXT = """
We train the model for 100 epochs with batch size 32 on a GPU.
"""


class TestReproChecklist:
    def test_all_present_no_finding(self):
        assert reproducibility.check_reproducibility_info(_COMPLETE_TEXT) == []

    def test_partial_missing_reported(self):
        findings = reproducibility.check_reproducibility_info(_PARTIAL_TEXT)
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "MISSING_REPRO_INFO"
        # epochs/batch/hardware 已报告；seed/std/lr/checkpoint 缺失
        assert "随机种子" in f["claim"]
        assert "学习率" in f["claim"]
        assert "批大小" not in f["claim"]
        assert f["needs_human_review"] is True

    def test_empty_text_no_finding(self):
        assert reproducibility.check_reproducibility_info("") == []


class TestSchemas:
    def test_registry_has_ten_types(self):
        assert len(FINDING_TYPES) == 10
        for meta in FINDING_TYPES.values():
            assert meta["severity"] in ("high", "medium", "low")
            assert meta["description"]

    def test_make_finding_default_severity_from_registry(self):
        f = make_finding("NUMERIC_MISMATCH", title="t")
        assert f["severity"] == "high"
        assert f["finding_id"] == ""  # 由编排层统一编号

    def test_make_finding_unknown_type_raises(self):
        with pytest.raises(ValueError):
            make_finding("NOT_A_TYPE")

    def test_assign_finding_ids_sorted_by_severity(self):
        findings = [
            make_finding("CHART_AXIS_RISK", title="low"),  # low
            make_finding("NUMERIC_MISMATCH", title="high"),  # high
            make_finding("MISSING_REPRO_INFO", title="medium"),  # medium
        ]
        ordered = assign_finding_ids(findings)
        assert [f["finding_id"] for f in ordered] == ["F-001", "F-002", "F-003"]
        assert [f["severity"] for f in ordered] == ["high", "medium", "low"]
