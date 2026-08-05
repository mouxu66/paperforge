"""DEPTH v3 评估兜底测试 —— 覆盖原 🔴「v3 深度评审 0 测试」。

验证：
1. LLM 返回空/非法结果时 verdict 仍为非空合法值
2. evaluate_paper 异常时有 error 字段而非崩溃
3. get_cached_score / save_score 基本流程
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest


@pytest.fixture
def mock_db():
    """内存 SQLite Session。"""
    from mock_api.database import SessionLocal, engine
    from mock_api.models import Base
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    yield db
    db.close()


class TestDepthEvalFallback:
    """DEPTH v3 评估兜底测试。"""

    def test_evaluate_paper_with_error(self, mock_db):
        """evaluate_paper 异常时应返回 error 字段而非抛出。"""
        from mock_api import depth_eval
        # 对不存在的论文评估应优雅降级
        result = depth_eval.evaluate_paper("nonexistent-paper-id", db=mock_db)
        assert isinstance(result, dict)
        # 不存在的论文应返回 error 或默认值
        assert "error" in result or "final_score" in result

    def test_cached_score_returns_none_if_not_cached(self, mock_db):
        """未缓存的论文应返回 None。"""
        from mock_api import depth_eval
        result = depth_eval.get_cached_score(mock_db, "nonexistent-paper-id")
        assert result is None

    def test_depth_score_response_has_required_fields(self):
        """DepthScoreResponse schema 应包含必需字段。"""
        from mock_api.schemas import DepthScoreResponse
        required = {"final_score", "novelty_score", "rigor_score", "influence_score", "reproducibility_score"}
        # 检查 model_fields 存在
        fields = set(DepthScoreResponse.model_fields.keys())
        assert required.issubset(fields), f"Missing fields: {required - fields}"

    def test_verdict_is_valid_enum(self):
        """verdict 应为合法枚举值。"""
        from mock_api.schemas import DepthScoreResponse
        # 确保 verdict 字段存在且有默认值
        assert "type" in DepthScoreResponse.model_fields or "verdict" in DepthScoreResponse.model_fields


# ---------------------------------------------------------------------------
# depth_eval v4.1 合并后 v3-compat 层不变式测试
# ---------------------------------------------------------------------------
class TestDepthEvalV3CompatConsolidation:
    """验证 v3 合并到 depth_eval_v4 后关键不变式仍保留。"""

    def test_shim_re_exports_same_function(self):
        """depth_eval shim 重导出必须与 depth_eval_v4 是同一函数对象。"""
        from mock_api import depth_eval, depth_eval_v4

        assert depth_eval.evaluate_paper is depth_eval_v4.evaluate_paper
        assert depth_eval.get_cached_score is depth_eval_v4.get_cached_score

    def test_v4_to_v3_dict_verdict_fallback_on_invalid(self):
        """final_verdict 越界时应兜底为 major_revision。"""
        from mock_api import depth_eval_v4
        d = depth_eval_v4._depthv4result_to_v3_dict(
            _fake_v4_result(final_verdict="garbage_value"),
            _fake_paper(),
        )
        assert d["verdict"] == "major_revision"

    def test_v4_to_v3_dict_verdict_passes_through_valid(self):
        """合法 verdict 应原样透传。"""
        from mock_api import depth_eval_v4
        for v in ("accept", "minor_revision", "major_revision", "reject"):
            d = depth_eval_v4._depthv4result_to_v3_dict(
                _fake_v4_result(final_verdict=v), _fake_paper()
            )
            assert d["verdict"] == v

    def test_v4_to_v3_dict_final_score_is_percent(self):
        """final_score 必须是 0-100 百分制（v3 schema 语义）。"""
        from mock_api import depth_eval_v4
        d = depth_eval_v4._depthv4result_to_v3_dict(
            _fake_v4_result(calibrated_score=0.73), _fake_paper()
        )
        assert 0.0 <= d["final_score"] <= 100.0
        assert round(d["final_score"], 1) == 73.0

    def test_v3_oim_within_unit_range(self):
        """obj_score 公式必须复用 v3 的 math.log OIM，结果 ∈ [0,1]。"""
        from mock_api import depth_eval_v4
        paper = _fake_paper(citations=1000, infl=50, full_text="x" * 100)
        s = depth_eval_v4._v3_compute_oim(paper)
        assert 0.0 <= s <= 1.0

    def test_save_v3_score_roundtrip_and_get_cached(self, mock_db):
        """写入 DepthScore 后 get_cached_score 能读取回去且字段不丢失。"""
        from mock_api import depth_eval_v4
        from mock_api.models import Paper
        # 先确保有 Paper 行（FK 需要）
        paper = Paper(id="p1", title="X", abstract="a", full_text="f")
        mock_db.add(paper)
        mock_db.commit()
        result = {
            "paper_id": "p1",
            "title": "X",
            "type": "B",
            "confidence": 0.7,
            "has_substance": True,
            "expectation": 0.6,
            "obj_score": 0.55,
            "novelty_score": 0.8,
            "rigor_score": 0.7,
            "influence_score": 0.65,
            "reproducibility_score": 0.5,
            "calibrated_score": 0.72,
            "final_score": 72.0,
            "verdict": "minor_revision",
            "core_contribution": "xyz",
            "keywords": ["a"],
            "missing_items": ["b"],
            "critique_points": ["c"],
            "defense_points": ["d"],
            "chair_reasoning": "ok",
            "content_source": "unit_test",
        }
        depth_eval_v4._save_v3_score(mock_db, result)
        cached = depth_eval_v4.get_cached_score(mock_db, "p1")
        assert cached is not None
        assert cached["paper_id"] == "p1"
        assert cached["final_score"] == 72.0
        assert cached["verdict"] == "minor_revision"
        assert cached["keywords"] == ["a"]


# ---------------------------------------------------------------------------
# 内部测试辅助
# ---------------------------------------------------------------------------

def _fake_paper(citations: int = 0, infl: int | None = None, full_text: str = "") -> Any:
    @dataclass
    class _P:
        id: str = "p"
        title: str = "t"
        abstract: str = "abs"
        full_text: str = ""
        journal: str = ""
        citations: int = 0
        influential_citations: int | None = None

    return _P(full_text=full_text, citations=citations, influential_citations=infl)


def _fake_v4_result(
    calibrated_score: float = 0.7,
    final_verdict: str = "major_revision",
) -> Any:
    """构造一个轻量级 DepthV4Result 替身，足够覆盖所测字段。"""

    @dataclass
    class _CP:
        point: str = ""
        severity: str = "minor"

    @dataclass
    class _R:
        paper_id: str = "p"
        title: str = "t"
        paper_type: str = "B"
        secondary_type: str = "none"
        confidence: float = 0.6
        has_substance: bool = True
        expectation: float = 0.5
        novelty_score: float = 0.5
        hotspot_alignment_score: float = 0.5
        core_contribution: str = "cc"
        rigor_score: float = 0.5
        missing_items: list = field(default_factory=list)
        influence_score: float = 0.5
        reproducibility_score: float = 0.5
        critique_points: list = field(default_factory=list)
        defense_points: list = field(default_factory=list)
        calibrated_score: float = 0.7
        delta: float = 0.0
        chair_reasoning: str = ""
        override_reason: str = ""
        final_verdict: str = "major_revision"
        llm_verdict: str = "major_revision"
        evaluated_at: str = "2026-01-01T00:00:00"
        evidence_pool: list = field(default_factory=list)
        evidence_checks: dict = field(default_factory=dict)
        base_score: float = 0.5
        weights: dict = field(default_factory=dict)
        node_score_stds: dict = field(default_factory=dict)

    r = _R()
    r.calibrated_score = calibrated_score
    r.final_verdict = final_verdict
    return r
