"""实验审计 P0-1/P0-2 数值检测单测（metrics.py）。

覆盖：
- F1 公式验证（一致/不一致/除零）
- 混淆矩阵推导比对
- 提升幅度口径判定（百分点 / 仅相对 / 都不符）
- 句级 P/R/F1 共现扫描
- 正文-表格交叉比对（匹配 / 不一致 / 无表跳过）
"""

from __future__ import annotations

from mock_api.experiment_audit import metrics
from mock_api.experiment_audit.tables import ExtractedTable


# ---------------------------------------------------------------------------
# P0-2 F1 / 混淆矩阵
# ---------------------------------------------------------------------------
class TestF1Consistency:
    def test_consistent_within_tolerance(self):
        # P=82.0 R=85.0 → F1=83.47，报告 83.5 → 差 0.03 不告警
        assert (
            metrics.check_f1_consistency(82.0, 85.0, 83.5) is None
        )

    def test_inconsistent_produces_finding(self):
        # 指南示例：P=82.0 R=85.0 报告 F1=84.6 → 差 1.13 > 0.5
        f = metrics.check_f1_consistency(82.0, 85.0, 84.6, page=6)
        assert f is not None
        assert f["type"] == "METRIC_INCONSISTENCY"
        assert f["severity"] == "high"
        assert f["page"] == 6
        assert f["computed"] == "83.47"
        assert f["needs_human_review"] is True

    def test_zero_division_guard(self):
        assert metrics.compute_f1(0.0, 0.0) == 0.0
        # P+R=0 时 computed=0，报告值也 0 → 不告警
        assert metrics.check_f1_consistency(0.0, 0.0, 0.0) is None


class TestConfusionMatrix:
    def test_consistent(self):
        # TP=80 FP=20 FN=10 → P=80, R=88.9, F1=84.2
        findings = metrics.check_confusion_matrix_consistency(
            80, 20, 10, {"precision": 80.0, "recall": 88.9, "f1": 84.2}
        )
        assert findings == []

    def test_inconsistent_recall(self):
        findings = metrics.check_confusion_matrix_consistency(
            80, 20, 10, {"recall": 95.0}
        )
        assert len(findings) == 1
        assert findings[0]["type"] == "METRIC_INCONSISTENCY"

    def test_degenerate_matrix_skipped(self):
        # TP+FP=0 → 无法推导，直接跳过
        assert (
            metrics.check_confusion_matrix_consistency(0, 0, 5, {"f1": 90.0}) == []
        )


class TestSentenceScan:
    def test_prf_cooccurrence_inconsistent(self):
        sent = "We achieve precision = 82.0%, recall = 85.0%, F1 = 84.6% on the test set."
        findings = metrics.check_sentence_metric_consistency(sent, page=3)
        assert len(findings) == 1
        assert findings[0]["page"] == 3

    def test_prf_cooccurrence_consistent(self):
        sent = "The model reaches precision = 82.0, recall = 85.0, F1 = 83.5."
        assert metrics.check_sentence_metric_consistency(sent) == []

    def test_confusion_matrix_sentence(self):
        sent = "With TP = 80, FP = 20, FN = 10, the recall is 95.0."
        findings = metrics.check_sentence_metric_consistency(sent)
        assert len(findings) == 1


# ---------------------------------------------------------------------------
# P0-1 提升幅度口径
# ---------------------------------------------------------------------------
class TestPercentageClaim:
    def test_absolute_match(self):
        # 80 → 84.2 差 4.2，声称 4.2 → absolute
        assert metrics.check_percentage_claim(4.2, 80.0, 84.2) == "absolute"

    def test_relative_only(self):
        # 50 → 55 差 5，相对提升 10%；声称 10 → 仅相对口径吻合
        assert metrics.check_percentage_claim(10.0, 50.0, 55.0) == "relative_only"

    def test_mismatch(self):
        # 80 → 81.9 差 1.9，相对 2.4%；声称 4.2 → 都不符
        assert metrics.check_percentage_claim(4.2, 80.0, 81.9) == "mismatch"

    def test_zero_baseline_guard(self):
        assert metrics.check_percentage_claim(0.0, 0.0, 0.0) == "absolute"
        assert metrics.check_percentage_claim(5.0, 0.0, 1.0) == "mismatch"


# ---------------------------------------------------------------------------
# P0-1 正文-表格交叉比对
# ---------------------------------------------------------------------------
def _make_results_table() -> ExtractedTable:
    return ExtractedTable(
        page=6,
        table_index=0,
        table_id="Table 2",
        bbox=[100, 200, 500, 400],
        rows=[
            ["Method", "Accuracy", "F1"],
            ["Baseline", "80.0", "78.5"],
            ["Ours", "84.2", "82.3"],
        ],
    )


class TestNumericClaimsVsTables:
    def test_no_tables_returns_empty(self):
        assert metrics.check_numeric_claims_vs_tables("anything", []) == []

    def test_claim_matching_table_no_finding(self):
        text = (
            "As shown in Table 2, our method achieves accuracy = 84.2. "
            "The result is consistent across runs."
        )
        findings = metrics.check_numeric_claims_vs_tables(text, [_make_results_table()])
        assert findings == []

    def test_claim_mismatch_produces_finding(self):
        text = (
            "As reported in Table 2, we obtain accuracy = 90.1 on this benchmark. "
            "Details are in the appendix."
        )
        findings = metrics.check_numeric_claims_vs_tables(text, [_make_results_table()])
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "NUMERIC_MISMATCH"
        assert f["severity"] == "high"
        assert f["page"] == 6
        assert any(e.get("table_id") == "Table 2" for e in f["evidence_sources"])

    def test_improvement_claim_absolute_ok(self):
        text = (
            "Compared with the baseline in Table 2, our method improves accuracy "
            "by 4.2 percentage points overall."
        )
        findings = metrics.check_numeric_claims_vs_tables(text, [_make_results_table()])
        assert findings == []

    def test_improvement_claim_mismatch(self):
        text = (
            "Compared with the baseline in Table 2, our method improves accuracy "
            "by 9.9 percentage points overall."
        )
        findings = metrics.check_numeric_claims_vs_tables(text, [_make_results_table()])
        assert len(findings) == 1
        assert findings[0]["type"] == "NUMERIC_MISMATCH"
        assert findings[0]["severity"] == "high"

    def test_improvement_claim_relative_only_flagged_low(self):
        # Accuracy 80 → 84.2：百分点差 4.2，相对提升 5.25%；声称 5.2% → 仅相对口径吻合
        text = (
            "Our method improves accuracy by 5.2% as shown in Table 2, "
            "which is a clear gain."
        )
        findings = metrics.check_numeric_claims_vs_tables(text, [_make_results_table()])
        assert len(findings) == 1
        assert findings[0]["severity"] == "low"

    def test_sentence_without_table_ref_ignored(self):
        text = "We achieve accuracy = 99.9 on our private benchmark overall."
        assert metrics.check_numeric_claims_vs_tables(text, [_make_results_table()]) == []


class TestSplitSentences:
    def test_keeps_abbreviations(self):
        text = "See Fig. 3 for details. Our method is better. It gains 2.0 points."
        sents = metrics.split_sentences(text)
        assert len(sents) == 3
        assert sents[0].startswith("See Fig.")
