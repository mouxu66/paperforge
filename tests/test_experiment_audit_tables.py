"""实验审计表格模块单测（tables.py）。

覆盖：
- caption 匹配（上方优先 / 水平无重叠排除 / 距离阈值）
- 结果表判定 / 表号查找 / 单元格定位
- P0-8 不确定度/显著性缺失（两维度分别报告：UNCERTAINTY_MISSING / SIGNIFICANCE_MISSING）
- extract_tables_from_pdf fail-open（坏字节流）
"""

from __future__ import annotations

from mock_api.experiment_audit import tables


def _table(rows, table_id="Table 2", page=6):
    return tables.ExtractedTable(
        page=page, table_index=0, table_id=table_id, bbox=[100, 200, 500, 400], rows=rows
    )


class TestCaptionMatching:
    def test_caption_above_table(self):
        # 表格 bbox [100,200,500,400]；caption 在正上方
        blocks = [((90, 180, 510, 198), "Table 2: Main results on CIFAR")]
        assert tables._match_table_caption([100, 200, 500, 400], blocks) == "Table 2"

    def test_caption_below_accepted(self):
        blocks = [((90, 402, 510, 420), "Table 3. Ablation study")]
        assert tables._match_table_caption([100, 200, 500, 400], blocks) == "Table 3"

    def test_far_text_excluded(self):
        # 水平无重叠（正文远处的 "as in Table 5"）
        blocks = [((600, 210, 700, 228), "as shown in Table 5")]
        assert tables._match_table_caption([100, 200, 500, 400], blocks) is None

    def test_too_far_vertical_excluded(self):
        # 垂直距离超过表格高度（矮表格 bbox，高度 20）
        blocks = [((90, 10, 510, 28), "Table 7: something")]
        assert tables._match_table_caption([100, 380, 500, 400], blocks) is None

    def test_above_preferred_over_below(self):
        blocks = [
            ((90, 180, 510, 198), "Table 1: caption above"),
            ((90, 402, 510, 420), "Table 9: caption below"),
        ]
        assert tables._match_table_caption([100, 200, 500, 400], blocks) == "Table 1"


class TestTableHelpers:
    def test_header_metrics(self):
        t = _table([["Method", "Accuracy (%)", "F1"], ["Ours", "84.2", "82.3"]])
        metrics = t.header_metrics()
        assert "accuracy" in metrics
        assert "f1" in metrics

    def test_is_results_table(self):
        t = _table([["Method", "BLEU"], ["Ours", "34.5"]])
        assert tables.is_results_table(t) is True
        # 无指标表头但数值够多
        t2 = _table([["a", "b"], ["1", "2"], ["3", "4"], ["5", "6"]], table_id=None)
        assert tables.is_results_table(t2, min_numeric=4) is True

    def test_find_table_by_id_case_insensitive(self):
        t = _table([["x"], ["1"]])
        assert tables.find_table_by_id([t], "table 2") is t
        assert tables.find_table_by_id([t], "Table 9") is None

    def test_find_cell_value(self):
        t = _table([["Method", "Accuracy", "F1"], ["Ours", "84.2", "82.3"]])
        assert tables.find_cell_value(t, "Ours", "F1") == (82.3, "82.3")
        assert tables.find_cell_value(t, "Missing", "F1") is None
        assert tables.find_cell_value(t, "Ours", "NoCol") is None

    def test_uncertainty_detection(self):
        assert _table([["M", "Acc"], ["Ours", "84.2±0.3"]]).has_uncertainty_values()
        assert not _table([["M", "Acc"], ["Ours", "84.2"]]).has_uncertainty_values()


class TestSignificanceMissing:
    def _results(self, cells):
        return _table([["Method", "Accuracy"]] + cells)

    def test_missing_both_flagged(self):
        # 既无不确定度也无显著性 → 两个维度各出一条
        findings = tables.detect_significance_missing(
            [self._results([["Ours", "84.2"]])], "We train our model on CIFAR."
        )
        assert {f["type"] for f in findings} == {
            "UNCERTAINTY_MISSING",
            "SIGNIFICANCE_MISSING",
        }
        u = next(f for f in findings if f["type"] == "UNCERTAINTY_MISSING")
        assert u["page"] == 6

    def test_both_reported_no_finding(self):
        # 表有 ± 且全文有 p 值 → 两个维度都不缺
        findings = tables.detect_significance_missing(
            [self._results([["Ours", "84.2±0.3"]])],
            "The difference is significant (p = 0.045).",
        )
        assert findings == []

    def test_uncertainty_only_missing(self):
        # 报告了 p 值但无 ± → 只缺不确定度（p 值不豁免）
        findings = tables.detect_significance_missing(
            [self._results([["Ours", "84.2"]])],
            "The difference is significant (p = 0.045).",
        )
        assert [f["type"] for f in findings] == ["UNCERTAINTY_MISSING"]
        assert "不确定度" in findings[0]["title"]

    def test_significance_only_missing(self):
        # 表有 ±（已报不确定度）但全文无统计检验 → 只缺显著性
        findings = tables.detect_significance_missing(
            [self._results([["Ours", "84.2±0.3"]])], "plain text without stats"
        )
        assert [f["type"] for f in findings] == ["SIGNIFICANCE_MISSING"]
        assert findings[0]["page"] == 6

    def test_uncertainty_text_suppresses_uncertainty_only(self):
        # 全文有 standard deviation（不确定度）但无 p 值 → 只缺显著性
        findings = tables.detect_significance_missing(
            [self._results([["Ours", "84.2"]])],
            "We report the mean over five runs with standard deviation.",
        )
        assert [f["type"] for f in findings] == ["SIGNIFICANCE_MISSING"]

    def test_ttest_suppresses_significance_only(self):
        # t-test 报告了显著性，但无 ± → 只缺不确定度
        findings = tables.detect_significance_missing(
            [self._results([["Ours", "84.2"]])],
            "We compared the two groups with a t-test.",
        )
        assert [f["type"] for f in findings] == ["UNCERTAINTY_MISSING"]

    def test_non_results_table_ignored(self):
        findings = tables.detect_significance_missing(
            [_table([["name", "value"], ["lr", "1e-4"]])], "nothing here"
        )
        assert findings == []


class TestExtractFailOpen:
    def test_garbage_bytes_returns_empty(self):
        assert tables.extract_tables_from_pdf(b"not a pdf at all") == []
