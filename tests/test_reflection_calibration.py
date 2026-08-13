"""reflection_calibration 单测：感悟报告 4 维 per-dim 确定性校准层。

覆盖：
1. apply_dim_offsets 的逐维减法 / clamp / None 保留 / 非 4 维键透传。
2. 开关与 env 覆盖（PAPERFORGE_REFLECTION_DIM_CALIBRATION / _DIM_OFFSETS）。
3. 金标回归：用 41 篇人工金标配对（ornstein_vs_human_full.csv）验证
   II（innovative_insights）系统偏差被确定性压到 ≈0，总 MAE 改善。

设计原则：不依赖 LLM / 数据库；金标 CSV 缺失时整体 skip（不误触发、不依赖网络）。
"""
from __future__ import annotations

import csv
import os

import pytest
from mock_api.reflection_calibration import (
    DIMENSIONS,
    apply_dim_offsets,
    get_dim_offsets,
    reset_dim_offsets,
)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ORN = os.path.join(_REPO, "deliverables", "ornstein_vs_human_full.csv")

_DIM_COLS = {
    "understanding_accuracy": ("human_UA", "orn_UA"),
    "analysis_depth": ("human_AD", "orn_AD"),
    "innovative_insights": ("human_II", "orn_II"),
    "evidence_support": ("human_ES", "orn_ES"),
}


def _mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


class TestApplyDimOffsets:
    """apply_dim_offsets 的行为契约。"""

    def test_subtracts_ii_bias_and_clamps(self, monkeypatch):
        monkeypatch.setenv(
            "PAPERFORGE_REFLECTION_DIM_OFFSETS",
            '{"innovative_insights": 0.0737, "analysis_depth": -0.02}',
        )
        out = apply_dim_offsets(
            {
                "understanding_accuracy": 0.80,
                "analysis_depth": 0.75,
                "innovative_insights": 0.70,
                "evidence_support": 0.85,
                "verdict": "needs_evidence",
            }
        )
        # II 减去正偏移；AD 减去负偏移（=加上 0.02）
        assert out["innovative_insights"] == pytest.approx(0.70 - 0.0737)
        assert out["analysis_depth"] == pytest.approx(0.75 + 0.02)
        # 无偏移键不受影响；非 4 维键透传
        assert out["understanding_accuracy"] == 0.80
        assert out["evidence_support"] == 0.85
        assert out["verdict"] == "needs_evidence"

    def test_clamp_to_zero(self, monkeypatch):
        monkeypatch.setenv(
            "PAPERFORGE_REFLECTION_DIM_OFFSETS", '{"innovative_insights": 0.5}'
        )
        assert apply_dim_offsets({"innovative_insights": 0.2})["innovative_insights"] == 0.0
        assert apply_dim_offsets({"innovative_insights": 0.9})["innovative_insights"] == 0.4

    def test_none_preserved(self, monkeypatch):
        monkeypatch.setenv(
            "PAPERFORGE_REFLECTION_DIM_OFFSETS", '{"innovative_insights": 0.0737}'
        )
        out = apply_dim_offsets({"innovative_insights": None, "evidence_support": 0.8})
        assert out["innovative_insights"] is None
        assert out["evidence_support"] == 0.8

    def test_disable_returns_identity(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_REFLECTION_DIM_CALIBRATION", "0")
        src = {
            "understanding_accuracy": 0.80,
            "analysis_depth": 0.75,
            "innovative_insights": 0.70,
            "evidence_support": 0.85,
        }
        out = apply_dim_offsets(dict(src))
        assert out == src

    def test_empty_scores_identity(self):
        assert apply_dim_offsets({}) == {}


class TestDimOffsetsResolution:
    """get_dim_offsets 的开关 / 覆盖 / 兜底。"""

    def test_default_enabled_and_loads_file(self, monkeypatch):
        monkeypatch.delenv("PAPERFORGE_REFLECTION_DIM_CALIBRATION", raising=False)
        monkeypatch.delenv("PAPERFORGE_REFLECTION_DIM_OFFSETS", raising=False)
        reset_dim_offsets()
        offs = get_dim_offsets()
        assert set(offs) == set(DIMENSIONS)
        # II 是主要修正量：应为正（系统偏高，需减去）
        assert offs["innovative_insights"] > 0.0
        # 2026-08-13 重跑（标题+摘要 + 分级帽 + II 中位数采样）后 II 偏移 ≈ +0.076
        assert abs(offs["innovative_insights"] - 0.0761) < 0.01

    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_REFLECTION_DIM_OFFSETS", '{"innovative_insights": 0.10}')
        offs = get_dim_offsets()
        assert offs["innovative_insights"] == pytest.approx(0.10)

    def test_bad_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_REFLECTION_DIM_OFFSETS", "not-json")
        offs = get_dim_offsets()
        assert offs["innovative_insights"] > 0.0  # 回退到 JSON 文件偏移（唯一事实源）

    def test_missing_json_file_fails_open(self, monkeypatch):
        """JSON 缺失 → 校准层 fail-open（空偏移），绝不用过期的内嵌常量副本。"""
        import mock_api.reflection_calibration as cal

        monkeypatch.delenv("PAPERFORGE_REFLECTION_DIM_OFFSETS", raising=False)
        monkeypatch.setattr(cal, "_OFFSETS_PATH", "/nonexistent/reflection_dim_offsets.json")
        cal.reset_dim_offsets()
        assert get_dim_offsets() == {}
        # apply_dim_offsets 恒等：不伪造/不套旧偏移
        assert apply_dim_offsets({"innovative_insights": 0.7}) == {"innovative_insights": 0.7}


@pytest.mark.skipif(not os.path.exists(_ORN), reason="41 篇系统分 CSV 缺失，跳过")
class TestGoldBiasRegression:
    """金标回归：校准层应确定性压平 II 系统偏差、改善总 MAE。"""

    def test_ii_bias_removed_and_mae_improved(self, monkeypatch):
        monkeypatch.delenv("PAPERFORGE_REFLECTION_DIM_CALIBRATION", raising=False)
        monkeypatch.delenv("PAPERFORGE_REFLECTION_DIM_OFFSETS", raising=False)
        reset_dim_offsets()

        with open(_ORN, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) >= 30

        human = {dim: [float(r[hcol]) for r in rows] for dim, (hcol, _) in _DIM_COLS.items()}
        system = {dim: [float(r[scol]) for r in rows] for dim, (_, scol) in _DIM_COLS.items()}

        # 校准前逐维 bias
        bias_before = {
            dim: _mean([s - h for s, h in zip(system[dim], human[dim])]) for dim in DIMENSIONS
        }
        # 经 apply_dim_offsets 后逐维 bias
        corrected = {
            dim: [
                apply_dim_offsets({dim: s})[dim]
                for s in system[dim]
            ]
            for dim in DIMENSIONS
        }
        bias_after = {
            dim: _mean([c - h for c, h in zip(corrected[dim], human[dim])]) for dim in DIMENSIONS
        }

        # II 是唯一显著偏差：校准后应接近 0，且绝对值显著缩小
        assert abs(bias_before["innovative_insights"]) > 0.05
        assert abs(bias_after["innovative_insights"]) < 0.02
        assert abs(bias_after["innovative_insights"]) < abs(bias_before["innovative_insights"])

        # 总 MAE（4 维合并）应改善或持平
        def _total_mae(system_map):
            flat_s, flat_h = [], []
            for dim in DIMENSIONS:
                flat_s.extend(system_map[dim])
                flat_h.extend(human[dim])
            return _mean([abs(s - h) for s, h in zip(flat_s, flat_h)])

        assert _total_mae(corrected) < _total_mae(system)
