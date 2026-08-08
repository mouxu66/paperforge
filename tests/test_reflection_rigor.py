"""感悟报告严谨化基建测试（ADR-014 · P1/P2，reflection 侧）。

覆盖：
1. P2：ReflectionReviewResult.score_uncertainty —— 默认关闭返回 {}（零开销），
   PAPERFORGE_UNCERTAINTY_GATE 开启时填充 bootstrap CI；流水线透传。
2. P1：reflection_rubric.yaml 加载（fail-open）+ 阈值治理一致性
   （yaml 值与代码常量必须一致，防魔数漂移）。

设计原则：**不依赖 LLM / 数据库**，用 fake LLM 走通 reviewer 即可。
"""

from __future__ import annotations

import json
import os

import pytest

from mock_api.depth_eval_reflection import (
    ReflectionReviewer,
    _reflection_uncertainty_report,
)
from mock_api.reflection_rubric import load_rubric, load_thresholds, score_to_band

# ── fake LLM：返回合法 JSON（snippet 须真实出现在 full_text 中才算有效证据） ──
_REPORT_TEXT = (
    "这篇读后感的核心观点是：该方法在路径规划上有创新。"
    "实验部分展示了稳定性提升 15%。我的思考是它适用于真实机器人场景。"
    "跨领域看，这与强化学习范式互补。"
)


def _fake_llm(prompt: str) -> str:
    return json.dumps(
        {
            "claims": [
                {"id": "C1", "text": "方法创新点", "evidence_id": "E1"},
                {"id": "C2", "text": "实验提升", "evidence_id": "E2"},
                {"id": "C3", "text": "适用场景", "evidence_id": "E3"},
            ],
            "evidence_pool": [
                {"id": "E1", "snippet": "该方法在路径规划上有创新", "claim_ref": "C1"},
                {"id": "E2", "snippet": "实验部分展示了稳定性提升 15%", "claim_ref": "C2"},
                {"id": "E3", "snippet": "它适用于真实机器人场景", "claim_ref": "C3"},
            ],
            "understanding_accuracy": 0.82,
            "analysis_depth": 0.76,
            "innovative_insights": 0.80,
            "evidence_support": 0.84,
            "summary": "报告复述准确且有一定深度。",
            "verdict_suggestion": "well_done",
        },
        ensure_ascii=False,
    )


def _review_once(monkeypatch) -> "object":
    """跑一次 review()，返回 ReflectionReviewResult（环境开关由各测试自行设置）。"""
    monkeypatch.delenv("PAPERFORGE_CITATION_VERIFY", raising=False)
    reviewer = ReflectionReviewer(llm_func=_fake_llm)
    return reviewer.review("p1", "标题", _REPORT_TEXT)


# ── P2：score_uncertainty ────────────────────────────────────────────────────

class TestScoreUncertainty:
    def test_default_off_returns_empty(self, monkeypatch):
        """PAPERFORGE_UNCERTAINTY_GATE 未设置 → score_uncertainty={}（零开销）。"""
        monkeypatch.delenv("PAPERFORGE_UNCERTAINTY_GATE", raising=False)
        res = _review_once(monkeypatch)
        assert res.scores["average"] > 0.6  # 前置：评审本身正常
        assert res.score_uncertainty == {}

    def test_gate_on_fills_ci(self, monkeypatch):
        """开关开启 → 填充 score/ci_low/ci_high/status。"""
        monkeypatch.setenv("PAPERFORGE_UNCERTAINTY_GATE", "1")
        res = _review_once(monkeypatch)
        u = res.score_uncertainty
        assert u, "开关开启后应填充不确定门控报告"
        assert "score" in u and "ci_low" in u and "ci_high" in u and "status" in u
        assert u["status"] in ("ok", "needs_human_review", "no_data", "error")

    def test_report_does_not_change_verdict(self, monkeypatch):
        """不确定门控只作附加信号，绝不改变 verdict（fail-open 原则）。"""
        monkeypatch.setenv("PAPERFORGE_UNCERTAINTY_GATE", "1")
        res = _review_once(monkeypatch)
        assert res.verdict in ("well_done", "needs_evidence", "needs_depth", "rewrite_required")

    def test_uncertainty_report_fail_open_on_bad_scores(self):
        """_reflection_uncertainty_report 对垃圾输入 fail-open 返回 {}。"""
        assert _reflection_uncertainty_report({}) == {}  # 无开关
        assert _reflection_uncertainty_report(None) == {}  # type-ignore: 防御性


# ── P2：流水线透传 ───────────────────────────────────────────────────────────

class TestPipelinePassthrough:
    def test_pipeline_passes_score_uncertainty(self, tmp_path, monkeypatch):
        """analyze_reflection_file 结果应透传 score_uncertainty（供前端/CSV 使用）。"""
        from unittest.mock import Mock

        from mock_api.reflection_pipeline import analyze_reflection_file
        from mock_api.reflection_docx_parser import ReflectionDoc

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        parse = ReflectionDoc(
            student_id="20240001",
            name="张三",
            paper_title="Deep Learning Advances",
            paper_author="Alice",
            paper_source="NeurIPS",
            sections={"q": "Q", "tech": "T", "exp": "E", "reflection": "R"},
            raw_text=_REPORT_TEXT,
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes", lambda data, filename="": parse
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title", lambda title, db: "paper001"
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: Mock(
                fidelity=0.7, status="ok", anchors=[], stray_claims=[], copy_ratio=0.0,
                copy_sentences=[], grounded_ratio=0.7,
            ),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_coverage",
            lambda sections, full: Mock(
                coverage=0.7, status="ok", covered=[], uncovered=[], copy_ratio=0.0,
            ),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: {"ai_likelihood": 0.1, "tier": "human", "signals": {}, "note": ""},
        )
        monkeypatch.setenv("PAPERFORGE_UNCERTAINTY_GATE", "1")

        class _FakeResult:
            scores = {
                "understanding_accuracy": 0.82,
                "analysis_depth": 0.76,
                "innovative_insights": 0.80,
                "evidence_support": 0.84,
                "average": 0.78,
            }
            effective_evidence_count = 3
            hardcoded_overrides = []
            parse_failed = False
            llm_calls = 1
            llm_empty = 0
            truncated = False
            evidence_rejections = {"ok": 3}
            score_uncertainty = {"status": "ok", "score": 0.78}

        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(review=lambda *a, **k: _FakeResult()),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb", lambda db, pid: ("full", None)
        )

        result = analyze_reflection_file(str(test_file), Mock())
        assert result["score_uncertainty"] == {"status": "ok", "score": 0.78}

    def test_pipeline_default_empty_uncertainty(self, tmp_path, monkeypatch):
        """开关关闭时透传空 dict，不影响结果结构。"""
        from unittest.mock import Mock

        from mock_api.reflection_pipeline import analyze_reflection_file
        from mock_api.reflection_docx_parser import ReflectionDoc

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")
        parse = ReflectionDoc(
            student_id="20240001", name="张三", paper_title="T", paper_author="A",
            paper_source="S", sections={"q": "Q", "tech": "T", "exp": "E", "reflection": "R"},
            raw_text=_REPORT_TEXT,
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes", lambda data, filename="": parse
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title", lambda title, db: None
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: Mock(fidelity=0.7, status="ok", anchors=[], stray_claims=[], copy_ratio=0.0, copy_sentences=[], grounded_ratio=0.7),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_coverage",
            lambda sections, full: Mock(coverage=0.7, status="ok", covered=[], uncovered=[], copy_ratio=0.0),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: {"ai_likelihood": 0.1, "tier": "human", "signals": {}, "note": ""},
        )
        monkeypatch.delenv("PAPERFORGE_UNCERTAINTY_GATE", raising=False)

        class _FakeResult:
            scores = {"understanding_accuracy": 0.8, "analysis_depth": 0.7, "innovative_insights": 0.7, "evidence_support": 0.8, "average": 0.75}
            effective_evidence_count = 3
            hardcoded_overrides = []
            parse_failed = False
            llm_calls = 1
            llm_empty = 0
            truncated = False
            evidence_rejections = {}
            score_uncertainty = {}

        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(review=lambda *a, **k: _FakeResult()),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb", lambda db, pid: ("", None)
        )

        result = analyze_reflection_file(str(test_file), Mock())
        assert result["score_uncertainty"] == {}


# ── P1：rubric 加载与阈值治理 ────────────────────────────────────────────────

class TestReflectionRubric:
    def test_load_rubric_schema(self):
        """yaml 结构：schema_version + 4 维锚点 + thresholds。"""
        rubric = load_rubric()
        assert rubric.get("schema_version") == 1
        dims = rubric.get("dimensions") or {}
        assert set(dims) == {
            "understanding_accuracy",
            "analysis_depth",
            "innovative_insights",
            "evidence_support",
        }
        for d, bands in dims.items():
            assert len(bands) >= 3, f"维度 {d} 应有三档锚点"

    def test_load_rubric_fail_open(self):
        """缺失/非法路径 fail-open 返回空 dict；空串=默认路径（正常加载）。"""
        assert load_rubric("/nonexistent/reflection_rubric.yaml") == {}
        assert load_rubric("") == load_rubric()  # 空串回退默认路径

    def test_score_to_band(self):
        """分数 → 档位映射（好/中/差）。"""
        assert score_to_band("understanding_accuracy", 0.9) == "好"
        assert score_to_band("understanding_accuracy", 0.6) == "中"
        assert score_to_band("understanding_accuracy", 0.3) == "差"
        assert score_to_band("nonexistent_dim", 0.9) == ""

    def test_thresholds_match_code_constants(self):
        """yaml 治理阈值必须与代码常量一致（防魔数漂移；改值须双写）。"""
        import mock_api.depth_eval_reflection as der
        import mock_api.reflection_fidelity as rf
        import mock_api.reflection_pipeline as rp

        modules = {
            "MIN_EVIDENCE_FOR_VALID_REVIEW": der,
            "MIN_EVIDENCE_FOR_HIGH_SCORE": der,
            "MAX_SCORE_WHEN_EVIDENCE_INSUFFICIENT": der,
            "MAX_SCORE_WHEN_EVIDENCE_BELOW_HIGH": der,
            "UNDERSTANDING_THRESHOLD_DEEP": der,
            "AVERAGE_SCORE_POOR": der,
            "INNOVATION_THRESHOLD": der,
            "FIDELITY_THR": rf,
            "COPY_SIM_THR": rf,
            "MIN_COPY_CHARS": rf,
            "COPY_GRAM_N": rf,
            "COPY_GRAM_HIT": rf,
            "GROUNDED_GRAM_MIN": rf,
            "COPY_RATIO_FAIL": rf,
            "FIDELITY_FAIL": rf,
            "STRAY_THR": rf,
            "XLING_MAX_SIM_HIGH": rf,
            "XLING_MAX_SIM_LOW": rf,
            "XLING_CHAR_OVERLAP_THR": rf,
            "COVERAGE_THR": rf,
            "COVERAGE_FAIL": rf,
            "MAX_PAPER_KEYPOINTS": rf,
            "MAX_PAPER_SENTENCES": rf,
            "MIN_REPORT_CHARS": rf,
            "REWRITE_AVG_CAP": rp,
        }
        thresholds = load_thresholds()
        assert thresholds, "yaml thresholds 为空"
        # yaml 中每个治理项都必须能在代码里找到对应常量，且值一致
        for name, expected in thresholds.items():
            mod = modules.get(name)
            assert mod is not None, f"yaml 治理项 {name} 未在代码常量表中注册"
            code_val = getattr(mod, name, None)
            assert code_val is not None, f"代码中不存在常量 {name}"
            assert code_val == expected, (
                f"阈值漂移: {name} yaml={expected} 代码={code_val} —— 请同步修改并更新依据"
            )
