"""统计合理性检测单测（depth_eval_v4 的数据造假指纹）。

覆盖：正文 markdown 表格路径（回归）+ 新增的「任意数值序列 / 图内曲线点 /
CSV 补充数据」三个输入源（等差 / 重复 / 恒定偏移 / Benford）。
"""
from __future__ import annotations

import pytest

from mock_api.depth_eval_v4 import (
    _arith_progression_flag,
    _benford_flag,
    _leading_digit,
    _constant_offset_flag,
    _repeated_value_flag,
    _stat_penalty,
    _statistical_plausibility_check,
    _statistical_redline,
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
        # 50 个首位数字全是 9 且跨 4 个数量级的数值 → 严重偏离 Benford（适用性成立）
        vals = [9.0 * 10**k + j * 0.1 for k in range(5) for j in range(10)]
        assert _leading_digit(vals[0]) == 9
        assert max(vals) / min(vals) >= 100  # 前置：跨两个数量级以上
        f = _benford_flag(vals, "表格")
        assert f is not None and f.startswith("[Benford偏离]")

    def test_benford_small_sample_ignored(self):
        assert _benford_flag([1, 2, 3], "表格") is None

    # ── 2026-09-16 假阳性修复回归 ────────────────────────────────────────
    def test_leading_digit_uses_first_significant_digit(self):
        """首位数字必须取首位**有效**数字，而不是字符串第一个字符。

        旧实现 int(str(0.85)[0]) == 0，而 0 不在 1–9 期望集合内，
        使得 ≥31 个 0.x 比率的表格 χ² 恰等于样本量 → 必然误报。
        """
        assert _leading_digit(0.85) == 8
        assert _leading_digit(0.0132) == 1
        assert _leading_digit(12.3) == 1
        assert _leading_digit(-3.4) == 3
        assert _leading_digit(9.9999999) == 9
        assert _leading_digit(0) is None
        assert _leading_digit(float("nan")) is None

    def test_benford_ignores_bounded_ratios(self):
        """有界比率（0.x accuracy/F1/AUC）不适用 Benford，必须不报旗。

        真实回归场景：论文表格里 60 个 0.10~0.99 的准确率。
        旧实现必报 [Benford偏离]，进而叠一条 [std过低] 就能触发 reject 红线。
        """
        ratios = [round(0.10 + 0.013 * i, 3) for i in range(60)]
        assert all(0.09 < v < 1.0 for v in ratios)
        assert _benford_flag(ratios, "表格") is None
        assert _benford_flag([float(v) for v in range(90, 100)] * 12, "表格") is None

    def test_benford_requires_wide_magnitude_span(self):
        """跨两个数量级以下的数据放弃检验（不报旗，也不误扣分）。"""
        narrow = [90 + (i % 10) for i in range(100)]  # min 90 / max 99
        assert _benford_flag(narrow, "表格") is None
        wide = [9 * 10**k for k in range(3) for _ in range(20)]  # 9 / 90 / 900
        assert _benford_flag(wide, "表格") is not None

    def test_benford_min_samples_is_configurable(self):
        """最小样本量可调（≥20），过小样本不检验。"""
        vals = [9.0 * 10**k + j * 0.1 for k in range(5) for j in range(10)]  # n=50
        assert _benford_flag(vals, "表格", min_samples=50) is not None
        assert _benford_flag(vals[:22], "表格", min_samples=50) is None


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


# ---------------------------------------------------------------------------
# 硬红线：≥2 条确定性指纹（Benford/std/p值）→ 直接 reject + Benford 扣分
# ---------------------------------------------------------------------------
class TestStatPenalty:
    def test_benford_included_in_deduction(self):
        assert _stat_penalty(["[Benford偏离] 表格首位数字严重偏离"]) == pytest.approx(0.05)

    def test_deterministic_fingerprints_weight_0_05_each(self):
        flags = [
            "[std过低] 声称 std 过低",
            "[p值不可能] 声称所有比较 p<0.001",
            "[Benford偏离] 首位数字偏离",
        ]
        assert _stat_penalty(flags) == pytest.approx(0.15)

    def test_weaker_signals_weight_0_03(self):
        assert _stat_penalty(["[表格文本矛盾] 夸大约 15pp"]) == pytest.approx(0.03)
        assert _stat_penalty(["[消融数字过整] 3 项 delta 小数部分相同"]) == pytest.approx(0.03)

    def test_penalty_capped_at_0_15(self):
        many = ["[std过低] a", "[p值不可能] b", "[Benford偏离] c", "[Benford偏离] d", "[Benford偏离] e"]
        assert _stat_penalty(many) == pytest.approx(0.15)

    def test_empty_no_penalty(self):
        assert _stat_penalty([]) == 0.0


class TestStatisticalRedline:
    def test_two_red_flags_reject(self):
        flags = ["[std过低] x", "[Benford偏离] y"]
        verdict, reason = _statistical_redline(flags, "accept", "原理由")
        assert verdict == "reject"
        assert "统计红线" in reason
        assert "2" in reason

    def test_single_red_flag_no_reject(self):
        flags = ["[Benford偏离] y"]
        verdict, reason = _statistical_redline(flags, "accept", "原理由")
        assert verdict == "accept"
        assert reason == "原理由"

    def test_already_reject_unchanged(self):
        flags = ["[std过低] x", "[Benford偏离] y"]
        verdict, reason = _statistical_redline(flags, "reject", "原理由")
        assert verdict == "reject"
        assert reason == "原理由"

    def test_non_redline_flags_ignored(self):
        flags = ["[等差数字] x", "[重复数字] y", "[恒定偏移] z"]
        verdict, reason = _statistical_redline(flags, "accept", "原理由")
        assert verdict == "accept"

    def test_mixed_two_redline_plus_others_reject(self):
        flags = ["[p值不可能] a", "[std过低] b", "[等差数字] c"]
        verdict, reason = _statistical_redline(flags, "minor_revision", "")
        assert verdict == "reject"
        assert reason.startswith("[统计红线]")
