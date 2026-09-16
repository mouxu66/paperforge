"""
DEPTH 校准层（`mock_api/depth_calibration.py`）单元测试。

覆盖：
- ``apply_score_offset``        (clamp[0,1] + 全局偏移)
- ``apply_top_tier_cap``        (高端 taper 封顶)
- ``correct_final_score``       (分档偏移入口)
- ``resolve_context_offset``    (source/year 分档查表)
- ``offset_corrected_verdict``  (偏移后 verdict)
- ``auto_offset_from_calibration`` (校准集搜索最优偏移)
- ``calibrate_verdict_thresholds`` (校准集重标定阈值)
- ``get_score_offset``/``reset_score_offset`` 的 PAPERFORGE_DEPTH_SCORE_OFFSET 读取路径

标记：本文件全部用例属于校准一致性护栏，统一挂 ``@pytest.mark.critical``。
运行：
    python -m pytest tests/test_depth_calibration.py -v
"""
from __future__ import annotations

import pytest

from mock_api import depth_calibration as dc


# 自动清理：每个用例前后清掉偏移 env 与 lru_cache，避免跨用例污染。
@pytest.fixture(autouse=True)
def _reset_offset_env(monkeypatch):
    monkeypatch.delenv("PAPERFORGE_DEPTH_SCORE_OFFSET", raising=False)
    dc.reset_score_offset()
    yield
    dc.reset_score_offset()


# 模拟「未显式配置 offset」。
# 仓库根 .env 里写着 PAPERFORGE_DEPTH_SCORE_OFFSET=0（注释「禁用伪金标校准」），
# 2026-09-16 起该值会经 Settings 通道生效并**最高优先**（这是修复目标）。
# 因此凡是要验证「分档表 / offset 参数」通道的用例，必须显式屏蔽显式配置，
# 否则测的其实是显式配置分支。
@pytest.fixture
def no_explicit_offset(monkeypatch):
    monkeypatch.setattr(dc, "_explicit_offset", lambda: None)


# ===========================================================================
# 1. apply_score_offset —— clamp[0,1] + 全局偏移
# ===========================================================================
@pytest.mark.critical
class TestApplyScoreOffset:
    def test_explicit_offset_subtracts(self):
        """显式 offset=-0.09：0.90 -> 0.81。"""
        assert dc.apply_score_offset(0.90, offset=-0.09) == pytest.approx(0.81)

    def test_clamp_upper(self):
        """score=2.0 超过 1.0 → 封顶 1.0。"""
        assert dc.apply_score_offset(2.0, offset=0.0) == 1.0

    def test_clamp_lower(self):
        """负结果 < 0 → 封底 0.0。"""
        assert dc.apply_score_offset(0.0, offset=-0.5) == 0.0

    def test_default_offset_zero_identity(self):
        """offset 默认走 get_score_offset()(=0) → 仅 clamp。"""
        assert dc.apply_score_offset(0.5) == 0.5
        assert dc.apply_score_offset(1.5) == 1.0

    def test_env_driven_default_offset(self, monkeypatch):
        """PAPERFORGE_DEPTH_SCORE_OFFSET 经 get_score_offset 生效。"""
        monkeypatch.setenv("PAPERFORGE_DEPTH_SCORE_OFFSET", "-0.12")
        dc.reset_score_offset()
        assert dc.apply_score_offset(0.90) == pytest.approx(0.78)

    def test_invalid_env_falls_back_to_zero(self, monkeypatch):
        """非法 env 值 → 回退 0.0（不抛异常）。"""
        monkeypatch.setenv("PAPERFORGE_DEPTH_SCORE_OFFSET", "not-a-float")
        dc.reset_score_offset()
        assert dc.apply_score_offset(0.5) == 0.5


# ===========================================================================
# 2. apply_top_tier_cap —— 高分 taper 封顶（保护真顶刊）
# ===========================================================================
@pytest.mark.critical
class TestApplyTopTierCap:
    def test_positive_or_zero_offset_identity(self):
        """off>=0 → 直接 clamp(score+off)，不走 taper。"""
        assert dc.apply_top_tier_cap(0.5, offset=0.0) == 0.5
        assert dc.apply_top_tier_cap(0.5, offset=0.1) == pytest.approx(0.6)

    def test_mid_segment_full_offset(self):
        """中段 (score<thr) 施加完整负向偏移。"""
        # score=0.5 < 0.85 → 完整 -0.09
        assert dc.apply_top_tier_cap(0.5, offset=-0.09, threshold=0.85, taper=0.15) == pytest.approx(0.41)

    def test_threshold_boundary_full_offset(self):
        """score==threshold 处 taper 系数=1 → 完整偏移。"""
        # t = (1-0.85)/(1-0.85) = 1.0 → eff = -0.09
        assert dc.apply_top_tier_cap(0.85, offset=-0.09, threshold=0.85, taper=0.15) == pytest.approx(0.76)

    def test_top_full_score_zero_offset(self):
        """score=1.0 → taper 系数=0 → 真满分不被压低。"""
        assert dc.apply_top_tier_cap(1.0, offset=-0.09, threshold=0.85, taper=0.15) == 1.0

    def test_linear_taper_midpoint(self):
        """score=0.925 处于 [thr,1] 中点 → 偏移折半。"""
        # t = (1-0.925)/(1-0.85) = 0.5 → eff = -0.045 → 0.88
        assert dc.apply_top_tier_cap(0.925, offset=-0.09, threshold=0.85, taper=0.15) == pytest.approx(0.88)

    def test_taper_zero_disables_taper(self):
        """taper<=0 → 不 taper，整段施加完整偏移。"""
        # score=0.9 >= thr 但 taper=0 → 整段 -0.09 → 0.81
        assert dc.apply_top_tier_cap(0.9, offset=-0.09, threshold=0.85, taper=0.0) == pytest.approx(0.81)

    def test_clamp_after_taper(self):
        """taper 后结果仍封顶 [0,1]。"""
        assert dc.apply_top_tier_cap(0.0, offset=-0.5, threshold=0.85, taper=0.15) == 0.0


# ===========================================================================
# 3. resolve_context_offset —— (source, year) 分档查表
# ===========================================================================
@pytest.mark.critical
class TestResolveContextOffset:
    def _tbl(self):
        # peerread=0.0 与 2026-08-09 生产表一致（0.6 阈值重扫结论，旧 0.18 已移除）
        return {"default": -0.09, "peerread": 0.0, "arxiv:2021": 0.0}

    def test_exact_source_year_match(self):
        """'arxiv:2021' 精确命中。"""
        assert dc.resolve_context_offset("arxiv", 2021, table=self._tbl()) == 0.0

    def test_peerread_legacy_year(self):
        """peerread + 2010(legacy) → 命中 'peerread'=0.0。"""
        assert dc.resolve_context_offset("peerread", 2010, table=self._tbl()) == 0.0

    def test_modern_default_fallback(self):
        """arxiv + 2022(modern) 无具体键 → 命中 'default'=-0.09。"""
        assert dc.resolve_context_offset("arxiv", 2022, table=self._tbl()) == -0.09

    def test_explicit_src_only_match(self):
        """仅 source（无 year）→ 命中 'peerread'。"""
        assert dc.resolve_context_offset("peerread", None, table=self._tbl()) == 0.0

    def test_unmatched_table_returns_none(self):
        """自定义表无匹配键 → 返回 None（调用方回退全局偏移）。"""
        tiny = {"zzz": 0.5}
        assert dc.resolve_context_offset("arxiv", 2022, table=tiny) is None

    def test_empty_table_returns_none(self):
        assert dc.resolve_context_offset("arxiv", 2022, table={}) is None

    def test_default_table_modern_uses_default(self):
        """默认表（P0 生产生效）：现代论文命中 default=-0.09。"""
        assert dc.resolve_context_offset("arxiv", 2024) == -0.09

    def test_default_table_peerread(self):
        """默认表（P0 生产生效）：peerread 已按 0.6 阈值重扫为 0.0（旧 0.18 移除）。"""
        assert dc.resolve_context_offset("peerread", 2010) == 0.0


# ===========================================================================
# 4. correct_final_score —— 分档偏移入口（含 paper 参数）
# ===========================================================================
@pytest.mark.critical
class TestCorrectFinalScore:
    """offset 参数 / 分档表通道（显式配置已由 no_explicit_offset 屏蔽）。"""

    def test_paper_none_behaves_like_cap(self, no_explicit_offset):
        """paper=None → 等同 apply_top_tier_cap(score, offset)。"""
        assert dc.correct_final_score(0.5, offset=-0.09, paper=None) == pytest.approx(0.41)

    def test_paper_peerread_uses_tiered_offset(self, no_explicit_offset):
        """paper=peerread/2010 → 解析偏移 0.0（2026-08-09 重扫结论，原样返回）。"""
        paper = {"source": "peerread", "year": 2010}
        assert dc.correct_final_score(0.5, paper=paper) == pytest.approx(0.5)

    def test_paper_modern_uses_default_offset(self, no_explicit_offset):
        """paper=arxiv/2024 → 解析偏移 -0.09（中段封顶生效）。"""
        paper = {"source": "arxiv", "year": 2024}
        # 中段 score=0.5 < thr → 完整 -0.09
        assert dc.correct_final_score(0.5, paper=paper) == pytest.approx(0.41)

    def test_use_cap_false_uses_plain_offset(self, no_explicit_offset):
        """use_cap=False → 走 apply_score_offset（无 taper）。"""
        # 高分 0.9 用 apply_score_offset 仍被压 -0.09 → 0.81（与 cap 路径不同）
        assert dc.correct_final_score(0.9, offset=-0.09, use_cap=False) == pytest.approx(0.81)

    def test_paper_object_attribute_access(self, no_explicit_offset):
        """paper 为对象（属性访问）也能抽取 source/year。"""

        class P:
            source = "peerread"
            year = 2010

        assert dc.correct_final_score(0.5, paper=P()) == pytest.approx(0.5)


# ===========================================================================
# 4b. 显式 offset 优先级（2026-09-16 修复：分档表不得覆盖显式配置）
# ===========================================================================
@pytest.mark.critical
class TestExplicitOffsetPriority:
    """回归护栏：P4「配置显式、失败响亮」——显式配置必须最高优先。

    病根：旧实现 `correct_final_score` 在 paper 非空时**无条件**用 (source, year)
    分档表覆盖 offset；而默认表恒含 "default": -0.09 键 → 查询永不返回 None →
    运维在 .env 写的 PAPERFORGE_DEPTH_SCORE_OFFSET=0 被静默忽略，
    490/695 条评审（70.5%）仍被扣 -0.09。
    """

    def test_explicit_zero_disables_tiered_default(self, monkeypatch):
        """显式 0.0 + 现代论文 → 不得再被 default 档扣 -0.09。"""
        monkeypatch.setenv("PAPERFORGE_DEPTH_SCORE_OFFSET", "0")
        paper = {"source": "arxiv", "year": 2024}
        assert dc.correct_final_score(0.70, paper=paper) == pytest.approx(0.70)

    def test_explicit_zero_disables_tiered_for_null_source(self, monkeypatch):
        """source/year 为 None（分档表仍会命中 default）也不得覆盖显式 0.0。"""
        monkeypatch.setenv("PAPERFORGE_DEPTH_SCORE_OFFSET", "0")
        assert dc.correct_final_score(0.70, paper={"source": None, "year": None}) == pytest.approx(0.70)
        assert dc.correct_final_score(0.70, paper={}) == pytest.approx(0.70)

    def test_explicit_env_overrides_tiered_peerread(self, monkeypatch):
        """显式配置覆盖 peerread 档（0.0）—— 显式优先于分档表。"""
        monkeypatch.setenv("PAPERFORGE_DEPTH_SCORE_OFFSET", "-0.09")
        paper = {"source": "peerread", "year": 2010}
        assert dc.correct_final_score(0.70, paper=paper) == pytest.approx(0.61)

    def test_settings_env_file_channel_is_read(self):
        """Settings(.env) 通道：.env 不注入 os.environ，必须经 Settings 读到。

        仓库根 .env 显式写了 PAPERFORGE_DEPTH_SCORE_OFFSET=0，故未设 os.environ 时
        `_explicit_offset()` 应返回 0.0（而非 None）——这正是修复前失效的那条通道。
        """
        assert dc._explicit_offset() == pytest.approx(0.0)

    def test_env_takes_priority_over_settings(self, monkeypatch):
        """os.environ 显式值优先于 Settings(.env)。"""
        monkeypatch.setenv("PAPERFORGE_DEPTH_SCORE_OFFSET", "-0.12")
        assert dc._explicit_offset() == pytest.approx(-0.12)

    def test_invalid_explicit_env_falls_back_to_zero(self, monkeypatch, caplog):
        """非法显式 env → 响亮警告 + 按 0.0（关闭偏移）处理，不静默用错值。"""
        monkeypatch.setenv("PAPERFORGE_DEPTH_SCORE_OFFSET", "abc")
        with caplog.at_level("WARNING"):
            assert dc._explicit_offset() == pytest.approx(0.0)
        assert any("非法" in r.message for r in caplog.records)


# ===========================================================================
# 5. offset_corrected_verdict —— 偏移后推导 verdict
# ===========================================================================
@pytest.mark.critical
class TestOffsetCorrectedVerdict:
    def test_accept(self):
        assert dc.offset_corrected_verdict(0.9, offset=0.0) == "accept"

    def test_minor_revision_after_offset(self):
        # 0.85 - 0.09 = 0.76 → 落入 [0.7, 0.8) → minor_revision
        assert dc.offset_corrected_verdict(0.85, offset=-0.09) == "minor_revision"

    def test_major_revision(self):
        # offset=0 时 0.55 → 落入 [0.5,0.7) → major_revision
        assert dc.offset_corrected_verdict(0.55, offset=0.0) == "major_revision"
        # 负向偏移 0.60 - 0.09 = 0.51 → 仍 [0.5,0.7) → major_revision
        assert dc.offset_corrected_verdict(0.60, offset=-0.09) == "major_revision"

    def test_reject(self):
        assert dc.offset_corrected_verdict(0.3, offset=0.0) == "reject"

    def test_custom_thresholds(self):
        assert dc.offset_corrected_verdict(0.6, offset=0.0, accept=0.9, reject=0.4) == "major_revision"


# ===========================================================================
# 6. auto_offset_from_calibration —— 校准集搜索最优偏移
# ===========================================================================
@pytest.mark.critical
class TestAutoOffsetFromCalibration:
    def test_empty_samples_returns_zero(self):
        assert dc.auto_offset_from_calibration([], lambda: [0.5]) == (0.0, 0.0)

    def test_perfect_match_kappa_one(self):
        """构造：base=[0.8,0.5,0.3] 与 verdict 完全一致 → offset≈0 时 κ=1。"""
        samples = [
            dc.CalibrationSample(paper_id="p1", text_hash="h1", expert_verdict="accept"),
            dc.CalibrationSample(paper_id="p2", text_hash="h2", expert_verdict="major_revision"),
            dc.CalibrationSample(paper_id="p3", text_hash="h3", expert_verdict="reject"),
        ]
        best_o, best_k = dc.auto_offset_from_calibration(samples, lambda: [0.8, 0.5, 0.3])
        # 多个 offset 均可得 κ=1.0（grid 从负向扫描，取首个最优），验证 κ=1.0 即可
        assert best_k == pytest.approx(1.0)
        assert abs(best_o) <= 0.05  # offset 应在 0 附近

    def test_returns_float_tuple(self):
        samples = [dc.CalibrationSample(paper_id="p1", text_hash="h1", expert_verdict="accept")]
        best_o, best_k = dc.auto_offset_from_calibration(samples, lambda: [0.8])
        assert isinstance(best_o, float)
        assert isinstance(best_k, float)
        assert -1.0 <= best_k <= 1.0


# ===========================================================================
# 7. calibrate_verdict_thresholds —— 校准集重标定阈值
# ===========================================================================
@pytest.mark.critical
class TestCalibrateVerdictThresholds:
    def test_empty_samples_returns_empty_result(self):
        res = dc.calibrate_verdict_thresholds([], lambda: [0.5])
        assert res.accept_threshold is None
        assert res.reject_threshold is None

    def test_returns_thresholds_and_kappa(self):
        samples = [
            dc.CalibrationSample(paper_id="p1", text_hash="h1", expert_verdict="accept"),
            dc.CalibrationSample(paper_id="p2", text_hash="h2", expert_verdict="major_revision"),
            dc.CalibrationSample(paper_id="p3", text_hash="h3", expert_verdict="reject"),
        ]

        def score_fn():
            return [0.85, 0.55, 0.3]

        res = dc.calibrate_verdict_thresholds(samples, score_fn)
        assert isinstance(res.accept_threshold, float)
        assert isinstance(res.reject_threshold, float)
        assert res.accept_threshold >= res.reject_threshold
        assert -1.0 <= res.cohen_kappa <= 1.0


# ===========================================================================
# 8. get_score_offset / reset_score_offset —— env 读取路径
# ===========================================================================
@pytest.mark.critical
class TestGetScoreOffsetEnv:
    def test_default_zero_when_unset(self):
        assert dc.get_score_offset() == 0.0

    def test_reads_env_value(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_DEPTH_SCORE_OFFSET", "0.07")
        dc.reset_score_offset()
        assert dc.get_score_offset() == 0.07

    def test_reset_clears_cache(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_DEPTH_SCORE_OFFSET", "0.05")
        dc.reset_score_offset()
        assert dc.get_score_offset() == 0.05
        monkeypatch.setenv("PAPERFORGE_DEPTH_SCORE_OFFSET", "-0.05")
        # 未 reset 前仍是缓存值
        assert dc.get_score_offset() == 0.05
        dc.reset_score_offset()
        assert dc.get_score_offset() == -0.05
