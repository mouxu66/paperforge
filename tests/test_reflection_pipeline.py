"""
reflection_pipeline 单测 — 0% → 80% 覆盖率提升。

Mock reflection_pipeline 的 LLM/embedding/docx 依赖，覆盖：
- _heuristic_four：结构启发式降级
- _get_paper_text_emb：DB 查询 / 嵌入获取
- analyze_reflection_file：完整流水线（happy path / LLM 降级 / fidelity 边角 / 无绑定）
"""

from __future__ import annotations

from unittest.mock import Mock

from mock_api.reflection_docx_parser import ReflectionDoc
from mock_api.reflection_fidelity import FidelityResult


# ── helpers ────────────────────────────────────────────────────
def _fake_parse(sections=None, raw="fake report text for pipeline testing"):
    """构造 ReflectionDoc 假数据。"""
    return ReflectionDoc(
        student_id="20240001",
        name="张三",
        paper_title="Deep Learning Advances",
        paper_author="Alice et al.",
        paper_source="NeurIPS 2024",
        sections=sections or {
            "q": "问题段落",
            "tech": "技术段落",
            "exp": "实验段落",
            "reflection": "感想段落",
        },
        raw_text=raw,
    )


def _fake_fidelity(fidelity=0.65):
    """构造 FidelityResult 假数据。"""
    return FidelityResult(
        fidelity=fidelity,
        anchors=[{"sentence": "matched sentence", "sim": 0.72}],
        stray_claims=[],
        status="ok",
        message="",
    )


def _fake_ai():
    """构造 AI likelihood 假数据。"""
    return {
        "ai_likelihood": 0.12,
        "tier": "human",
        "signals": {},
        "confidence": "low",
        "note": "advisory only",
    }


def _fake_review_result(average=0.78, verdict="needs_evidence"):
    """构造 ReflectionReviewer.review() 假返回。"""
    m = Mock()
    m.scores = {
        "understanding_accuracy": 0.80,
        "analysis_depth": 0.75,
        "innovative_insights": 0.70,
        "evidence_support": 0.85,
        "average": average,
        "verdict": verdict,
    }
    return m


# ── _heuristic_four ────────────────────────────────────────────

class TestHeuristicFour:
    """结构启发式降级：四段齐全度 → 4 维均分。"""

    def test_all_sections_present(self):
        from mock_api.reflection_pipeline import _heuristic_four

        result = _heuristic_four({
            "q": "问题内容",
            "tech": "技术内容",
            "exp": "实验内容",
            "reflection": "感想内容",
        })
        assert result["understanding_accuracy"] == 1.0
        assert result["analysis_depth"] == 1.0
        assert result["innovative_insights"] == 1.0
        assert result["evidence_support"] == 1.0
        assert result["average"] == 1.0

    def test_two_sections_missing(self):
        from mock_api.reflection_pipeline import _heuristic_four

        result = _heuristic_four({
            "q": "Q",
            "tech": " ",
            "exp": "E",
            "reflection": "",
        })
        assert result["average"] == 0.5  # q + exp = 2/4

    def test_all_empty(self):
        from mock_api.reflection_pipeline import _heuristic_four

        result = _heuristic_four({})
        assert result["average"] == 0.0

    def test_spaces_considered_empty(self):
        from mock_api.reflection_pipeline import _heuristic_four

        result = _heuristic_four({
            "q": "   \n  ",
            "tech": "T",
            "exp": "",
            "reflection": "",
        })
        assert result["average"] == 0.25  # 1/4


# ── _get_paper_text_emb ────────────────────────────────────────

class TestGetPaperTextEmb:
    """原论文全文与嵌入获取。"""

    def test_paper_found_with_embedding(self, monkeypatch):
        from mock_api.reflection_pipeline import _get_paper_text_emb

        fake_db = Mock()
        fake_paper = Mock()
        fake_paper.full_text = "full paper content here"
        fake_db.query.return_value.filter.return_value.first.return_value = fake_paper

        monkeypatch.setattr(
            "mock_api.crud.get_paper_embedding",
            lambda db, paper_id: [0.1, 0.2, 0.3],
        )

        full, emb = _get_paper_text_emb(fake_db, "paper123")
        assert full == "full paper content here"
        assert emb == [0.1, 0.2, 0.3]

    def test_paper_not_found(self, monkeypatch):
        from mock_api.reflection_pipeline import _get_paper_text_emb

        fake_db = Mock()
        fake_db.query.return_value.filter.return_value.first.return_value = None

        full, emb = _get_paper_text_emb(fake_db, "nonexistent")
        assert full == ""
        assert emb is None

    def test_none_paper_id(self, monkeypatch):
        from mock_api.reflection_pipeline import _get_paper_text_emb

        fake_db = Mock()
        full, emb = _get_paper_text_emb(fake_db, None)
        assert full == ""
        assert emb is None


# ── analyze_reflection_file ─────────────────────────────────────

class TestAnalyzeReflectionFile:
    """主流水线：docx 解析 → 绑定 → fidelity → 4 维 → 融合。"""

    def test_happy_path_full_pipeline(self, tmp_path, monkeypatch):
        """全流水线正常路径：mock 所有依赖。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        # 本测试锁的是「原始分透传」机制，关闭确定性校准层以免逐维偏移干扰精确断言。
        monkeypatch.setenv("PAPERFORGE_REFLECTION_DIM_CALIBRATION", "0")

        # 创建临时文件
        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        # Mock 解析器
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _fake_parse(),
        )
        # Mock 绑定
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: "paper001",
        )
        # Mock fidelity
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: _fake_fidelity(0.65),
        )
        # Mock AI likelihood
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: _fake_ai(),
        )
        # Mock 4 维 reviewer — ReflectionReviewer 在 analyze_reflection_file 内 try/except 导入
        # 源模块是 depth_eval_reflection，不是 reflection_pipeline
        # 注意：流水线现向 review() 传 student_id（交叉验证加分链路）
        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(review=lambda rid, title, raw, student_id="", paper_text="", **kwargs: _fake_review_result(0.78)),
        )
        # Mock _get_paper_text_emb
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("full paper text", [0.1, 0.2, 0.3]),
        )

        fake_db = Mock()
        result = analyze_reflection_file(str(test_file), fake_db)

        # 验证返回结构
        assert result["student_id"] == "20240001"
        assert result["student_name"] == "张三"
        assert result["paper_title"] == "Deep Learning Advances"
        assert result["bound_paper_id"] == "paper001"
        assert result["scores"]["understanding_accuracy"] == 0.80
        assert result["scores"]["fidelity"] == 0.65
        # 融合层现为 6 维加权评分；coverage 未命中时按 0 计入。
        assert result["average"] == round(
            0.80 * 0.05 + 0.75 * 0.15 + 0.70 * 0.35 + 0.85 * 0.05
            + 0.65 * 0.05 + (result["coverage"] or 0.0) * 0.35,
            4,
        )
        assert result["verdict"] == "needs_evidence"
        assert result["fidelity_status"] == "ok"
        assert result["ai_likelihood"] == 0.12
        assert result["ai_likelihood_tier"] == "human"

    def test_llm_unavailable_falls_back_to_heuristic(self, tmp_path, monkeypatch):
        """LLM 不可用时降级为 _heuristic_four。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _fake_parse(),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: "paper001",
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: _fake_fidelity(0.65),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: _fake_ai(),
        )
        # LLM 抛异常
        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(
                review=lambda rid, title, raw, student_id="", paper_text="", **kwargs: (_ for _ in ()).throw(RuntimeError("LLM down"))
            ),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("full paper text", None),
        )

        fake_db = Mock()
        result = analyze_reflection_file(str(test_file), fake_db)

        # LLM 故障时不能把结构启发式伪装成真实评分；确定性指标仍保留。
        assert result["scores"]["understanding_accuracy"] is None
        assert result["scores"]["analysis_depth"] is None
        assert result["average"] is None
        assert result["llm_failed"] is True
        assert result["scores"]["fidelity"] == 0.65
        assert result["verdict"] == "llm_failed"
        assert result["fidelity_status"] == "ok"

    def test_fidelity_below_fail_threshold_forces_rewrite(self, tmp_path, monkeypatch):
        """忠实度过低 → verdict 强制 rewrite_required。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _fake_parse(),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: "paper001",
        )
        # fidelity < FIDELITY_FAIL (0.30)
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: _fake_fidelity(0.15),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: _fake_ai(),
        )
        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(
                review=lambda rid, title, raw, student_id="", paper_text="", **kwargs: _fake_review_result(0.78, "needs_evidence")
            ),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("full paper text", None),
        )

        fake_db = Mock()
        result = analyze_reflection_file(str(test_file), fake_db)

        assert result["fidelity"] == 0.15
        assert result["verdict"] == "rewrite_required"  # 强制覆盖

    def test_no_paper_binding_fidelity_unavailable(self, tmp_path, monkeypatch):
        """未绑定到原论文 → fidelity 返回 no_paper。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        # 报告没有 paper_title，绑定也返回 None
        parse = _fake_parse()
        parse.paper_title = None

        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": parse,
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: None,
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: FidelityResult(
                fidelity=None, status="no_paper", message="未绑定有效原论文"
            ),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: _fake_ai(),
        )
        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(review=lambda rid, title, raw, paper_text="", **kwargs: _fake_review_result()),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("", None),
        )

        fake_db = Mock()
        result = analyze_reflection_file(str(test_file), fake_db)

        assert result["bound_paper_id"] is None
        assert result["fidelity"] is None
        assert result["fidelity_status"] == "no_paper"

    def test_source_paper_id_overrides_auto_binding(self, tmp_path, monkeypatch):
        """显式 source_paper_id 优先于自动标题匹配。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        binding_called = []

        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _fake_parse(),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: binding_called.append(title) or "auto_match",
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: _fake_fidelity(),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: _fake_ai(),
        )
        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(review=lambda rid, title, raw, paper_text="", **kwargs: _fake_review_result()),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("full text", None),
        )

        fake_db = Mock()
        result = analyze_reflection_file(str(test_file), fake_db, source_paper_id="manual_123")

        # match_paper_by_title 不应被调用
        assert len(binding_called) == 0
        assert result["bound_paper_id"] == "manual_123"

    def test_fidelity_none_does_not_crash_verdict(self, tmp_path, monkeypatch):
        """fidelity 为 None 时（no_paper/too_short），不触发 rewrite 覆盖。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        # 锁原始分透传与加权公式，关闭校准层以免逐维偏移干扰精确断言。
        monkeypatch.setenv("PAPERFORGE_REFLECTION_DIM_CALIBRATION", "0")

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _fake_parse(),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: None,
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: FidelityResult(
                fidelity=None, status="no_paper"
            ),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: _fake_ai(),
        )
        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(
                review=lambda rid, title, raw, student_id="", paper_text="", **kwargs: _fake_review_result(verdict="needs_evidence")
            ),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("", None),
        )

        fake_db = Mock()
        result = analyze_reflection_file(str(test_file), fake_db)

        # fidelity None 时不触发 FIDELITY_FAIL 的 rewrite 覆盖
        assert result["verdict"] == "needs_evidence"
        assert result["average"] == round(
            0.80 * 0.05 + 0.75 * 0.15 + 0.70 * 0.35 + 0.85 * 0.05
            + 0.0 * 0.05 + (result["coverage"] or 0.0) * 0.35,
            4,
        )  # fidelity=0 in average

    def test_rewrite_required_caps_average(self, tmp_path, monkeypatch):
        """verdict=rewrite_required 时 average 封顶 REWRITE_AVG_CAP（照抄/编造防分叉）。"""
        from mock_api.reflection_pipeline import REWRITE_AVG_CAP, analyze_reflection_file

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _fake_parse(),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: "paper001",
        )
        # fidelity 很低 → verdict 会被强制 rewrite_required
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: _fake_fidelity(0.10),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: _fake_ai(),
        )
        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(review=lambda rid, title, raw, student_id="", paper_text="", **kwargs: _fake_review_result()),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("full paper text", None),
        )
        # coverage 很高 → 未封顶时总分会被拉高
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_coverage",
            lambda sections, full: type("Cov", (), {"coverage": 0.90, "copy_ratio": None, "status": "ok", "covered": [], "uncovered": []})(),
        )

        fake_db = Mock()
        result = analyze_reflection_file(str(test_file), fake_db)

        assert result["verdict"] == "rewrite_required"
        assert result["average"] <= REWRITE_AVG_CAP + 1e-9

    def test_returns_ai_likelihood_advisory_only(self, tmp_path, monkeypatch):
        """验证 AI likelihood 字段存在且不影响 scores/verdict。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _fake_parse(),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: "paper001",
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: _fake_fidelity(0.65),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: {
                "ai_likelihood": 0.92,
                "tier": "likely_ai",
                "signals": {"test": {}},
                "confidence": "low",
                "note": "仅供参考",
            },
        )
        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(review=lambda rid, title, raw, student_id="", paper_text="", **kwargs: _fake_review_result()),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("full", None),
        )

        fake_db = Mock()
        result = analyze_reflection_file(str(test_file), fake_db)

        # AI likelihood 高但 verdict 不受影响；LLM 正常时仍保留评审结果。
        assert result["ai_likelihood"] == 0.92
        assert result["ai_likelihood_tier"] == "likely_ai"
        assert result["verdict"] == "needs_evidence"
        assert result["llm_failed"] is False
        assert "ai_likelihood" not in result["scores"]  # 不进入 scores


class TestCitationIntegrityVerdict:
    """ADR-014 P4：引用真值进硬校验层（fabricated → rewrite_required）。"""

    def _run(self, tmp_path, monkeypatch, flag, llm_verdict="needs_evidence"):
        """搭一条走通全管线的分析，注入指定 integrity_flag。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _fake_parse(),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: "paper001",
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: _fake_fidelity(0.65),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: _fake_ai(),
        )
        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(
                review=lambda rid, title, raw, student_id="", paper_text="", **kwargs: _fake_review_result(
                    0.78, llm_verdict
                )
            ),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("full paper text", None),
        )
        # 引用真值校验：开关 + mock 结果
        monkeypatch.setenv("PAPERFORGE_CITATION_VERIFY", "1")
        monkeypatch.setattr(
            "mock_api.integrity.citation_verifier.assess_citation_integrity",
            lambda text, verify_online=True: {"integrity_flag": flag},
        )
        return analyze_reflection_file(str(test_file), Mock())

    def test_fabricated_forces_rewrite_required(self, tmp_path, monkeypatch):
        """Crossref 查无 DOI（疑似编造参考文献）→ verdict=rewrite_required。"""
        from mock_api.reflection_pipeline import REWRITE_AVG_CAP

        result = self._run(tmp_path, monkeypatch, "fabricated_suspected")
        assert result["verdict"] == "rewrite_required"
        assert result["citation_override_reason"]
        # 编造引用 → 总分封顶（与照抄/编造同一防分叉机制）
        assert result["average"] <= REWRITE_AVG_CAP + 1e-9

    def test_inconsistent_forces_needs_evidence(self, tmp_path, monkeypatch):
        """文中引用与参考文献列表不一致 → verdict 至少 needs_evidence。"""
        result = self._run(tmp_path, monkeypatch, "inconsistent", llm_verdict="well_done")
        assert result["verdict"] == "needs_evidence"
        assert result["citation_override_reason"]

    def test_flag_ok_no_override(self, tmp_path, monkeypatch):
        """integrity_flag=ok（未发现明显问题）→ 不触发引用否决。"""
        result = self._run(tmp_path, monkeypatch, "ok", llm_verdict="well_done")
        assert result["verdict"] == "well_done"
        assert result["citation_override_reason"] == ""

    def test_unknown_flag_no_override(self, tmp_path, monkeypatch):
        """integrity_flag=unknown（未核验/降级）→ 不误杀报告。"""
        result = self._run(tmp_path, monkeypatch, "unknown", llm_verdict="well_done")
        assert result["verdict"] == "well_done"

    def test_verify_off_by_default_no_citation_field(self, tmp_path, monkeypatch):
        """PAPERFORGE_CITATION_VERIFY=0 显式关闭：不调用校验器，citation_integrity 为空。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        monkeypatch.setenv("PAPERFORGE_CITATION_VERIFY", "0")
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _fake_parse(),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: "paper001",
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: _fake_fidelity(0.65),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: _fake_ai(),
        )
        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(
                review=lambda rid, title, raw, student_id="", paper_text="", **kwargs: _fake_review_result(
                    0.78, "well_done"
                )
            ),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("full paper text", None),
        )

        result = analyze_reflection_file(str(test_file), Mock())
        assert result["citation_integrity"] == {}
        assert result["citation_override_reason"] == ""
        assert result["verdict"] == "well_done"  # 完全向后兼容

    def test_citation_verify_default_auto_triggers_verifier(self, tmp_path, monkeypatch):
        """默认未设 PAPERFORGE_CITATION_VERIFY：应自动跑 offline 引用校验（≠旧版默认关闭）。

        回归防护：reflection_pipeline 默认从「跳过」改为「自动 offline 校验」，
        必须保证未显式置 0 时校验器被实际调用（且为 offline，不触网）。
        """
        from mock_api.reflection_pipeline import analyze_reflection_file

        called = []

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        monkeypatch.delenv("PAPERFORGE_CITATION_VERIFY", raising=False)
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _fake_parse(),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: "paper001",
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: _fake_fidelity(0.65),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: _fake_ai(),
        )
        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(
                review=lambda rid, title, raw, student_id="", paper_text="", **kwargs: _fake_review_result(
                    0.78, "well_done"
                )
            ),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("full paper text", None),
        )
        monkeypatch.setattr(
            "mock_api.integrity.citation_verifier.assess_citation_integrity",
            lambda text, verify_online=False: called.append((text, verify_online)) or {"integrity_flag": "ok"},
        )

        result = analyze_reflection_file(str(test_file), Mock())
        assert called, "默认未设 PAPERFORGE_CITATION_VERIFY 时应自动执行引用校验"
        assert result["citation_integrity"] == {"integrity_flag": "ok"}
        # 默认必须为 offline（不触网），仅显式 1/true/on 才触网
        assert called[0][1] is False

    def test_inconsistent_uses_reviewer_top_level_verdict(self, tmp_path, monkeypatch):
        """生产形态：verdict 是结果对象顶层字段（不在 scores dict 里），
        inconsistent 降级仍应生效（回归：reviewer_verdict 取值的修复）。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        monkeypatch.setenv("PAPERFORGE_CITATION_VERIFY", "1")
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _fake_parse(),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: "paper001",
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: _fake_fidelity(0.65),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: _fake_ai(),
        )

        # 生产形态：scores 只有 4 维 + average，verdict 是顶层字段
        class _ProdShapedResult:
            scores = {
                "understanding_accuracy": 0.82,
                "analysis_depth": 0.76,
                "innovative_insights": 0.80,
                "evidence_support": 0.84,
                "average": 0.78,
            }
            verdict = "well_done"

        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(review=lambda rid, title, raw, student_id="", paper_text="", **kwargs: _ProdShapedResult()),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("full paper text", None),
        )
        monkeypatch.setattr(
            "mock_api.integrity.citation_verifier.assess_citation_integrity",
            lambda text, verify_online=True: {"integrity_flag": "inconsistent"},
        )

        result = analyze_reflection_file(str(test_file), Mock())
        assert result["verdict"] == "needs_evidence"  # well_done 被 inconsistent 降级
        assert result["citation_override_reason"]

    def test_verifier_exception_fail_open(self, tmp_path, monkeypatch):
        """校验器抛异常 → fail-open，verdict 不受影响。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        test_file = tmp_path / "report.docx"
        test_file.write_bytes(b"fake docx bytes")

        monkeypatch.setenv("PAPERFORGE_CITATION_VERIFY", "1")
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _fake_parse(),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: "paper001",
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_fidelity",
            lambda sections, full, emb: _fake_fidelity(0.65),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.compute_ai_likelihood",
            lambda text: _fake_ai(),
        )
        monkeypatch.setattr(
            "mock_api.depth_eval_reflection.ReflectionReviewer",
            lambda: Mock(
                review=lambda rid, title, raw, student_id="", paper_text="", **kwargs: _fake_review_result(
                    0.78, "well_done"
                )
            ),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: ("full paper text", None),
        )
        monkeypatch.setattr(
            "mock_api.integrity.citation_verifier.assess_citation_integrity",
            lambda text, verify_online=True: (_ for _ in ()).throw(RuntimeError("Crossref down")),
        )

        result = analyze_reflection_file(str(test_file), Mock())
        assert result["citation_integrity"] == {}
        assert result["verdict"] == "well_done"
