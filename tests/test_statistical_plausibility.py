"""统计合理性检测单测（depth_eval_v4 的数据造假指纹）。

覆盖：正文 markdown 表格路径（回归）+ 新增的「任意数值序列 / 图内曲线点 /
CSV 补充数据」三个输入源（等差 / 重复 / 恒定偏移 / Benford）。
"""
from __future__ import annotations

from mock_api.depth_eval_v4 import (
    _arith_progression_flag,
    _benford_flag,
    _constant_offset_flag,
    _repeated_value_flag,
    _statistical_plausibility_check,
    statistical_flags_from_curve_points,
    statistical_flags_from_series,
    statistical_flags_from_table_file,
)


# ---------------------------------------------------------------------------
# 核心信号 helper
# ---------------------------------------------------------------------------
class TestCoreSignalHelpers:
    def test_arith_progression_detected(self):
        f = _arith_progression_flag([82.5, 84.0, 85.5, 87.0], "测试列")
        assert f is not None and f.startswith("[等差数字]")

    def test_arith_sequence_number_column_excluded(self):
        # 差为 ±1 的序号列（epoch/层数/种子）不算
        assert _arith_progression_flag([1, 2, 3, 4, 5], "epoch") is None
        assert _arith_progression_flag([0, -1, -2, -3], "逆序") is None

    def test_arith_short_series_ignored(self):
        assert _arith_progression_flag([1, 2, 3], "短") is None

    def test_repeated_value_detected(self):
        f = _repeated_value_flag([1.23, 4.5, 1.23, 1.23, 9.9], "列")
        assert f is not None and f.startswith("[重复数字]")

    def test_repeated_under_threshold_ignored(self):
        assert _repeated_value_flag([1.1, 1.1, 2.2], "列") is None

    def test_constant_offset_detected(self):
        f = _constant_offset_flag([10, 11, 12, 13], [10.1, 11.1, 12.1, 13.1], "A vs B")
        assert f is not None and f.startswith("[恒定偏移]")

    def test_constant_offset_requires_alignment(self):
        assert _constant_offset_flag([1, 2, 3, 4], [1.1, 2.2, 3.1, 4.2], "A vs B") is None

    def test_benford_deviation_detected(self):
        # 100 个首位数字全是 9 的数值 → 严重偏离 Benford
        vals = [90 + (i % 10) for i in range(100)]
        f = _benford_flag(vals, "表格")
        assert f is not None and f.startswith("[Benford偏离]")

    def test_benford_small_sample_ignored(self):
        assert _benford_flag([1, 2, 3], "表格") is None


# ---------------------------------------------------------------------------
# 任意数值序列入口
# ---------------------------------------------------------------------------
class TestStatisticalFlagsFromSeries:
    def test_arithmetic_and_offset(self):
        flags = statistical_flags_from_series(
            [("组A", [1.1, 2.2, 3.3, 4.4, 5.5]), ("组B", [1.2, 2.3, 3.4, 4.5, 5.6])]
        )
        assert any(f.startswith("[等差数字]") for f in flags)
        assert any(f.startswith("[恒定偏移]") for f in flags)

    def test_existing_flags_preserved(self):
        out = statistical_flags_from_series([], existing_flags=["x"])
        assert out == ["x"]


# ---------------------------------------------------------------------------
# 图内曲线点入口（series 的 x 采样不对齐 → 不做恒定偏移）
# ---------------------------------------------------------------------------
class TestStatisticalFlagsFromCurvePoints:
    def test_arithmetic_y_values_detected(self):
        points = [
            {"series": "#ff0000", "x": i, "y": v}
            for i, v in enumerate([10.0, 12.0, 14.0, 16.0, 18.0])
        ]
        flags = statistical_flags_from_curve_points(points)
        assert any(f.startswith("[等差数字]") and "#ff0000" in f for f in flags)
        # 单一序列不应做恒定偏移（pairwise_offset=False）
        assert not any(f.startswith("[恒定偏移]") for f in flags)

    def test_empty_returns_empty(self):
        assert statistical_flags_from_curve_points([]) == []


# ---------------------------------------------------------------------------
# CSV/TSV 补充数据入口
# ---------------------------------------------------------------------------
class TestStatisticalFlagsFromTableFile:
    def test_arithmetic_column_detected(self, tmp_path):
        p = tmp_path / "supp.csv"
        p.write_text(
            "group,valA,valB\ng0,0,0\ng1,1.1,1.2\ng2,2.2,2.4\ng3,3.3,3.6\ng4,4.4,4.8\n",
            encoding="utf-8",
        )
        flags = statistical_flags_from_table_file(str(p))
        assert any(f.startswith("[等差数字]") and "valA" in f for f in flags)

    def test_missing_file_fail_open(self):
        assert statistical_flags_from_table_file("/no/such/file.csv") == []

    def test_no_numeric_columns_returns_empty(self, tmp_path):
        p = tmp_path / "text.csv"
        p.write_text("a,b\nx,y\nz,w\n", encoding="utf-8")
        assert statistical_flags_from_table_file(str(p)) == []


# ---------------------------------------------------------------------------
# 正文 markdown 表格路径回归（重构后行为不变）
# ---------------------------------------------------------------------------
class TestMarkdownTableRegression:
    def test_arithmetic_table_still_flagged(self):
        fake = (
            "Table 1: quantification\n"
            "| Method | AUC | Precision |\n"
            "|--------|-----|-----------|\n"
            "| A | 82.5 | 90.0 |\n"
            "| B | 84.0 | 91.5 |\n"
            "| C | 85.5 | 93.0 |\n"
            "| D | 87.0 | 94.5 |\n"
        )
        flags = _statistical_plausibility_check(fake)
        assert any(f.startswith("[等差数字]") for f in flags)
        assert any(f.startswith("[恒定偏移]") for f in flags)

    def test_no_table_no_flags(self):
        assert _statistical_plausibility_check("普通正文，没有表格。") == []
