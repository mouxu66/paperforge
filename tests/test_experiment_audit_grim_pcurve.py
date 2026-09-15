"""实验审计 P1-2 GRIM + P1-3 p-curve 单测（grim_pcurve.py）。

覆盖：
- grim_consistent：报告精度感知的 GRIM 判定（4.56/n=30 违例、0.333/n=3 可达）
- extract_mean_n_pairs：同句配对 + 整数语境/M= 门槛 + 百分比/连续量排除
- check_grim：违例产 Finding，一致不产
- extract_pvalues / analyze_pvalues / check_pcurve：勉强显著聚集、重复 p 值、
  0.05 右侧断崖
"""

from __future__ import annotations

from mock_api.experiment_audit import grim_pcurve


class TestGrimConsistent:
    def test_classic_grim_violation(self):
        # 经典示例：mean=4.56, n=30 → 136/30=4.533→4.53、137/30=4.567→4.57
        assert grim_pcurve.grim_consistent("4.56", 30) is False

    def test_one_third_rounded_is_achievable(self):
        # 1/3=0.3333…→0.333，n=3 可达；不能用「均值×n 恰为整数」误判
        assert grim_pcurve.grim_consistent("0.333", 3) is True

    def test_zero_point_three_five_with_n3_is_violation(self):
        # n=3 报 0.35：0.35×3=1.05，k=1→0.333、k=2→0.667，都不是 0.35
        assert grim_pcurve.grim_consistent("0.35", 3) is False

    def test_achievable_mean_not_flagged(self):
        # 797/24=33.208→33.2，可达
        assert grim_pcurve.grim_consistent("33.2", 24) is True

    def test_exact_half_is_achievable(self):
        assert grim_pcurve.grim_consistent("1.5", 2) is True

    def test_point_five_with_n9_is_violation(self):
        # k/9 在 1 位小数的取值无 0.5（k=4.5 非整数）
        assert grim_pcurve.grim_consistent("24.5", 9) is False

    def test_trailing_zero_claims_higher_precision(self):
        # 33.20（2 位小数）声称比 33.2 更高精度，n=24 不可达
        assert grim_pcurve.grim_consistent("33.20", 24) is False

    def test_integer_mean_always_consistent(self):
        assert grim_pcurve.grim_consistent("3", 4) is True

    def test_zero_mean_consistent(self):
        assert grim_pcurve.grim_consistent("0.000", 30) is True


class TestExtractMeanNPairs:
    def test_mean_age_pair(self):
        pairs = grim_pcurve.extract_mean_n_pairs("mean age was 33.2 years (n = 24)")
        assert pairs == [("33.2", 24, "mean age was 33.2 years (n = 24)")]

    def test_m_equals_sd_n(self):
        pairs = grim_pcurve.extract_mean_n_pairs("M = 4.56, SD = 0.23, N = 30")
        assert ("4.56", 30) in [(m, n) for m, n, _ in pairs]

    def test_percentage_mean_excluded(self):
        # accuracy 是连续量，GRIM 不适用；% 排除
        assert grim_pcurve.extract_mean_n_pairs("mean accuracy of 95.2% (n = 3)") == []

    def test_continuous_measure_excluded(self):
        # enzyme activity 是连续量，无整数语境词也无 M= → 排除
        assert grim_pcurve.extract_mean_n_pairs("enzyme activity 0.763 ± 0.05 (n = 6)") == []

    def test_no_sample_size_no_pair(self):
        assert grim_pcurve.extract_mean_n_pairs("the mean was 4.56") == []

    def test_sample_size_without_mean_no_pair(self):
        assert grim_pcurve.extract_mean_n_pairs("n = 30 participants") == []


class TestCheckGrim:
    def test_violation_produces_finding(self):
        findings = grim_pcurve.check_grim("M = 4.56, SD = 0.23, N = 30")
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "GRIM_INCONSISTENCY"
        assert f["severity"] == "medium"
        assert f["needs_human_review"] is True
        assert "4.56" in f["claim"]
        assert "n = 30" in f["claim"]

    def test_consistent_mean_no_finding(self):
        assert grim_pcurve.check_grim("mean age was 33.2 years (n = 24)") == []

    def test_mixed_only_violation_flagged(self):
        text = (
            "mean age was 33.2 years (n = 24). "
            "Participants rated the item M = 4.56, SD = 0.23, N = 30."
        )
        findings = grim_pcurve.check_grim(text)
        assert len(findings) == 1
        assert "4.56" in findings[0]["claim"]

    def test_empty_text_no_finding(self):
        assert grim_pcurve.check_grim("") == []


class TestExtractPvalues:
    def test_various_operators(self):
        text = "p = 0.043, p < 0.05, p=.048, P = .04, p-value = 0.001"
        pvalues = grim_pcurve.extract_pvalues(text)
        assert pvalues == [
            ("=", 0.043),
            ("<", 0.05),
            ("=", 0.048),
            ("=", 0.04),
            ("=", 0.001),
        ]

    def test_out_of_range_excluded(self):
        assert grim_pcurve.extract_pvalues("p = 0.000, p = 1.0, p = 1.2") == []

    def test_no_pvalues(self):
        assert grim_pcurve.extract_pvalues("no statistics reported here") == []


class TestCheckPcurve:
    def test_clustering_produces_finding(self):
        # 8 个勉强显著、1 个强显著、无 0.05~0.10 → 聚集 + 断崖
        text = " ".join(f"p = {v}" for v in [
            0.041, 0.043, 0.044, 0.045, 0.046, 0.047, 0.048, 0.049, 0.001,
        ])
        findings = grim_pcurve.check_pcurve(text)
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "PCURVE_ANOMALY"
        assert f["severity"] == "medium"
        assert f["needs_human_review"] is True
        assert "p值聚集" in f["computed"]
        assert "p曲线断崖" in f["computed"]

    def test_repeated_just_sig_pvalue_flagged(self):
        text = "p = 0.045, p = 0.045, p = 0.045, p = 0.03, p = 0.02"
        findings = grim_pcurve.check_pcurve(text)
        assert len(findings) == 1
        assert "p值重复" in findings[0]["computed"]

    def test_spread_pvalues_no_finding(self):
        text = " ".join(f"p = {v}" for v in [0.001, 0.02, 0.15, 0.3, 0.5, 0.8])
        assert grim_pcurve.check_pcurve(text) == []

    def test_too_few_pvalues_no_finding(self):
        assert grim_pcurve.check_pcurve("p = 0.043, p = 0.047") == []

    def test_empty_text_no_finding(self):
        assert grim_pcurve.check_pcurve("") == []
