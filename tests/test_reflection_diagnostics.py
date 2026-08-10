"""感悟报告流水线的「诊断字段透传」回归测试（2026-08-05）。

背景：41 篇批次里 19 篇被写成全 0.3，事后无法从 CSV 分辨到底是
「LLM 超时被硬规则 R1 压分」还是「学生报告真的没证据」——因为
effective_evidence_count / hardcoded_overrides / parse_failed 全被
流水线丢弃，truncated 还因为对 dict 做 getattr 而恒为 False，
report_chars 恒为 0。本文件锁死修复后的行为。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from mock_api.reflection_docx_parser import ReflectionDoc
from mock_api.reflection_fidelity import FidelityResult

REPORT_RAW = "报告正文" * 50


def _fake_parse() -> ReflectionDoc:
    return ReflectionDoc(
        student_id="20240001",
        name="张三",
        paper_title="Deep Learning Advances",
        paper_author="Alice et al.",
        paper_source="NeurIPS 2024",
        sections={
            "q": "问题段落",
            "tech": "技术段落",
            "exp": "实验段落",
            "reflection": "感想段落",
        },
        raw_text=REPORT_RAW,
    )


def _fake_fidelity() -> FidelityResult:
    return FidelityResult(
        fidelity=0.65,
        anchors=[{"sentence": "matched", "sim": 0.72}],
        stray_claims=[],
        status="ok",
        message="",
    )


def _fake_ai() -> dict:
    return {
        "ai_likelihood": 0.12,
        "tier": "human",
        "signals": {},
        "confidence": "low",
        "note": "advisory only",
    }


def _review_result(
    *,
    effective_evidence_count: int = 3,
    hardcoded_overrides: list[str] | None = None,
    parse_failed: bool = False,
    truncated: bool = False,
    llm_calls: int = 1,
    llm_empty: int = 0,
) -> SimpleNamespace:
    """字段类型真实（非 Mock 自动属性）的 review 返回。"""
    return SimpleNamespace(
        scores={
            "understanding_accuracy": 0.80,
            "analysis_depth": 0.75,
            "innovative_insights": 0.70,
            "evidence_support": 0.85,
            "average": 0.78,
            "verdict": "needs_evidence",
        },
        effective_evidence_count=effective_evidence_count,
        hardcoded_overrides=hardcoded_overrides or [],
        parse_failed=parse_failed,
        truncated=truncated,
        llm_calls=llm_calls,
        llm_empty=llm_empty,
    )


def _patch(monkeypatch, review_result) -> None:
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
        lambda sections, full, emb: _fake_fidelity(),
    )
    monkeypatch.setattr(
        "mock_api.reflection_pipeline.compute_ai_likelihood",
        lambda text: _fake_ai(),
    )
    monkeypatch.setattr(
        "mock_api.reflection_pipeline._get_paper_text_emb",
        lambda db, pid: ("full paper text " * 10, [0.1, 0.2, 0.3]),
    )
    monkeypatch.setattr(
        "mock_api.depth_eval_reflection.ReflectionReviewer",
        lambda: Mock(
            review=lambda rid, title, raw, student_id="", paper_text="", **kwargs: review_result
        ),
    )


def _run(tmp_path, monkeypatch, review_result) -> dict:
    from mock_api.reflection_pipeline import analyze_reflection_file

    f = tmp_path / "r.docx"
    f.write_bytes(b"x")
    _patch(monkeypatch, review_result)
    return analyze_reflection_file(str(f), Mock())


class TestDiagnosticPropagation:
    """4 维评审的诊断信息必须原样出现在流水线返回里。"""

    def test_diag_fields_propagated(self, tmp_path, monkeypatch):
        res = _run(
            tmp_path,
            monkeypatch,
            _review_result(
                effective_evidence_count=1,
                hardcoded_overrides=["R1:evidence<2 -> cap 0.3"],
                llm_calls=2,
            ),
        )
        assert res["effective_evidence_count"] == 1
        assert res["hardcoded_overrides"] == ["R1:evidence<2 -> cap 0.3"]
        assert res["llm_calls"] == 2
        assert res["llm_empty"] == 0
        assert res["parse_failed"] is False
        assert res["llm_failed"] is False

    def test_truncated_not_stuck_on_false(self, tmp_path, monkeypatch):
        """回归：truncated 曾对 dict 做 getattr，永远返回 False。"""
        res = _run(tmp_path, monkeypatch, _review_result(truncated=True))
        assert res["truncated"] is True

    def test_report_chars_not_stuck_on_zero(self, tmp_path, monkeypatch):
        """回归：report_chars 恒 0；且返回值不得夹带全文。"""
        res = _run(tmp_path, monkeypatch, _review_result())
        assert res["report_chars"] == len(REPORT_RAW)
        assert res["paper_chars"] > 0
        # 该 dict 会被整体写进 DB 的 analysis_v2 并回给前端，不能带全文
        assert "raw_text" not in res


class TestLLMFailureFlag:
    """LLM 故障必须被标记出来，而不是伪装成一个真实的低分。"""

    def test_llm_empty_marks_failure(self, tmp_path, monkeypatch):
        res = _run(tmp_path, monkeypatch, _review_result(llm_calls=2, llm_empty=2))
        assert res["llm_failed"] is True
        assert res["average"] is None
        assert res["verdict"] == "llm_failed"
        assert res["scores"]["analysis_depth"] is None

    def test_parse_failed_marks_failure(self, tmp_path, monkeypatch):
        res = _run(tmp_path, monkeypatch, _review_result(parse_failed=True))
        assert res["parse_failed"] is True
        assert res["llm_failed"] is True

    def test_reviewer_exception_marks_failure(self, tmp_path, monkeypatch):
        """reviewer 抛异常 → 降级启发式，但必须标记 llm_failed。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        f = tmp_path / "r.docx"
        f.write_bytes(b"x")
        _patch(monkeypatch, _review_result())

        def _boom():
            raise RuntimeError("llama-server down")

        monkeypatch.setattr("mock_api.depth_eval_reflection.ReflectionReviewer", _boom)
        assert analyze_reflection_file(str(f), Mock())["llm_failed"] is True


class TestDiagExtractionIsolation:
    """诊断提取失败只能让诊断降级，不能连累评分。"""

    def test_bad_diag_types_do_not_break_scoring(self, tmp_path, monkeypatch):
        # 只有 scores 是真值，其余属性都是 Mock 自动属性（int()/迭代都会抛异常）
        bad = Mock()
        bad.scores = _review_result().scores
        res = _run(tmp_path, monkeypatch, bad)

        # 4 维仍来自 reviewer，而不是被打回结构启发式（那会给 0.25 的整数倍）
        assert res["scores"]["understanding_accuracy"] == 0.80
        assert res["scores"]["evidence_support"] == 0.85
        # 诊断退回安全默认值
        assert res["llm_calls"] == 0
        assert res["hardcoded_overrides"] == []


class TestBenchRowFailureHandling:
    """CSV 层：LLM 故障时不得写出 0.3 这种看似真实的分数。"""

    @staticmethod
    def _res(**over) -> dict:
        base = {
            "paper_title": "T",
            "bound_paper_id": "p1",
            "report_chars": 1234,
            "paper_chars": 5678,
            "scores": {
                "understanding_accuracy": 0.3,
                "analysis_depth": 0.3,
                "innovative_insights": 0.3,
                "evidence_support": 0.3,
                "fidelity": 0.71,
                "coverage": 0.66,
            },
            "average": 0.3,
            "verdict": "rewrite_required",
            "copy_ratio": 0.02,
            "stray_claims": [],
            "truncated": False,
            "fidelity_status": "ok",
            "coverage_status": "ok",
            "effective_evidence_count": 0,
            "hardcoded_overrides": ["R1"],
            "llm_calls": 3,
            "llm_empty": 3,
            "llm_failed": True,
            "elapsed_s": 312.4,
        }
        base.update(over)
        return base

    def test_llm_failed_blanks_llm_derived_columns(self):
        from scripts.reflection_bench import row_from_result

        row = row_from_result("999900000011", "某某", "x.docx", self._res())

        assert row["error"] == "llm_failed"
        for k in (
            "understanding_accuracy",
            "analysis_depth",
            "innovative_insights",
            "evidence_support",
            "average",
            "verdict",
        ):
            assert row[k] == "", f"{k} 不该保留 LLM 故障时的分数"
        # 确定性指标保留，供排查
        assert row["fidelity"] == 0.71
        assert row["coverage"] == 0.66
        assert row["hardcoded_overrides"] == "R1"
        assert row["llm_empty"] == 3
        assert row["elapsed_s"] == 312.4

    def test_healthy_row_keeps_scores(self):
        from scripts.reflection_bench import row_from_result

        res = self._res(
            llm_failed=False,
            llm_empty=0,
            hardcoded_overrides=[],
            effective_evidence_count=4,
            average=0.72,
            verdict="well_done",
            scores={
                "understanding_accuracy": 0.85,
                "analysis_depth": 0.70,
                "innovative_insights": 0.60,
                "evidence_support": 0.80,
                "fidelity": 0.71,
                "coverage": 0.66,
            },
        )
        row = row_from_result("999900000001", "林某", "y.docx", res)

        assert row["error"] == ""
        assert row["average"] == 0.72
        assert row["verdict"] == "well_done"
        assert row["understanding_accuracy"] == 0.85
        assert row["effective_evidence"] == 4
        assert row["hardcoded_overrides"] == ""

    def test_all_fields_covered_by_csv_header(self):
        """新增诊断列必须进 FIELDS，否则 DictWriter 会静默丢弃。"""
        from scripts.reflection_bench import FIELDS, row_from_result

        row = row_from_result("1", "n", "f.docx", self._res())
        assert set(row) <= set(FIELDS), set(row) - set(FIELDS)
        for col in ("hardcoded_overrides", "llm_calls", "llm_empty", "elapsed_s"):
            assert col in FIELDS


# ── 证据拒绝原因归类（P2：消除 0.85/0.3 掷硬币）────────────────────
#
# 113 号报告首轮把注入的【原论文参考内容】当成报告去引证，snippet 在报告里
# 命中率 0.00 → 有效证据 1 条 → R1 把四维压到 0.3；同样输入重试一次得到 0.85。
# 光看「只有 1 条证据」根本分不清是模型引错来源还是报告真没料。

REPORT_TEXT = "我在复现时发现批大小从 32 调到 128 后收敛明显变慢。作者的解释是学习率没有同步缩放。"
PAPER_TEXT = "We observe that large-batch training degrades generalization unless the learning rate is scaled."


class TestEvidenceRejectionDiagnosis:
    def test_snippet_from_report_is_ok(self):
        from mock_api.depth_eval_reflection import diagnose_evidence_rejections

        pool = [{"id": "E1", "snippet": "批大小从 32 调到 128 后收敛明显变慢"}]
        d = diagnose_evidence_rejections(pool, REPORT_TEXT, PAPER_TEXT)
        assert d["counts"] == {"ok": 1, "from_paper": 0, "not_found": 0}

    def test_snippet_from_paper_is_flagged(self):
        """引用原论文 ≠ 报告没证据，必须单独归类。"""
        from mock_api.depth_eval_reflection import diagnose_evidence_rejections

        pool = [{"id": "E1", "snippet": "large-batch training degrades generalization"}]
        d = diagnose_evidence_rejections(pool, REPORT_TEXT, PAPER_TEXT)
        assert d["counts"]["from_paper"] == 1
        assert d["counts"]["not_found"] == 0
        assert d["samples"]["from_paper"]

    def test_fabricated_snippet_is_not_found(self):
        from mock_api.depth_eval_reflection import diagnose_evidence_rejections

        pool = [{"id": "E1", "snippet": "本文提出了一种全新的量子退火优化器"}]
        d = diagnose_evidence_rejections(pool, REPORT_TEXT, PAPER_TEXT)
        assert d["counts"]["not_found"] == 1
        assert d["counts"]["from_paper"] == 0

    def test_no_paper_text_falls_back_to_not_found(self):
        """没提供原论文时不能瞎猜来源，一律算 not_found。"""
        from mock_api.depth_eval_reflection import diagnose_evidence_rejections

        pool = [{"id": "E1", "snippet": "large-batch training degrades generalization"}]
        d = diagnose_evidence_rejections(pool, REPORT_TEXT, "")
        assert d["counts"]["not_found"] == 1

    def test_empty_snippets_ignored(self):
        from mock_api.depth_eval_reflection import diagnose_evidence_rejections

        d = diagnose_evidence_rejections([{"id": "E1", "snippet": "  "}], REPORT_TEXT)
        assert d["counts"] == {"ok": 0, "from_paper": 0, "not_found": 0}


class TestRetryFeedbackText:
    def test_mentions_paper_source(self):
        from mock_api.depth_eval_reflection import build_evidence_diagnosis_text

        text = build_evidence_diagnosis_text(
            {"counts": {"ok": 0, "from_paper": 2, "not_found": 0}, "samples": {"from_paper": "abc"}}
        )
        assert "原论文" in text and "2" in text

    def test_mentions_rewrite(self):
        from mock_api.depth_eval_reflection import build_evidence_diagnosis_text

        text = build_evidence_diagnosis_text(
            {"counts": {"ok": 0, "from_paper": 0, "not_found": 3}, "samples": {"not_found": "xyz"}}
        )
        assert "查不到" in text and "3" in text

    def test_empty_when_nothing_rejected(self):
        from mock_api.depth_eval_reflection import build_evidence_diagnosis_text

        assert build_evidence_diagnosis_text(
            {"counts": {"ok": 3, "from_paper": 0, "not_found": 0}, "samples": {}}
        ) == ""

    def test_retry_template_renders_with_diagnosis(self):
        from mock_api.depth_eval_reflection import RETRY_EVIDENCE_TEMPLATE

        out = RETRY_EVIDENCE_TEMPLATE.format(n=1, diagnosis="【原因】xxx\n")
        assert "【原因】xxx" in out
        assert "只输出了 1 条" in out


class TestPromptForbidsPaperCitation:
    """prompt 必须显式禁止引用原论文——这是 113 掷硬币的直接诱因。"""

    def test_main_prompt_states_prohibition(self):
        from mock_api.depth_eval_reflection import PROMPT_REFLECTION

        assert "禁止引用【原论文参考内容】" in PROMPT_REFLECTION

    def test_paper_section_states_prohibition(self):
        from mock_api.depth_eval_reflection import PAPER_SECTION_TEMPLATE

        assert "不得作为 evidence snippet 引用" in PAPER_SECTION_TEMPLATE


class TestReviewerRecordsRejections:
    """走一遍 review()：证据引错来源时必须留痕，且重试提示带上原因。"""

    @staticmethod
    def _payload(snippet: str) -> str:
        import json

        return json.dumps(
            {
                "claims": [{"id": "C1", "text": "观点", "evidence_id": "E1"}],
                "evidence_pool": [{"id": "E1", "snippet": snippet, "claim_ref": "C1"}],
                "understanding_accuracy": 0.85,
                "analysis_depth": 0.7,
                "innovative_insights": 0.5,
                "evidence_support": 0.85,
                "summary": "总结",
                "verdict_suggestion": "well_done",
            },
            ensure_ascii=False,
        )

    def test_from_paper_recorded_and_fed_back(self):
        from mock_api.depth_eval_reflection import ReflectionReviewer

        prompts: list[str] = []

        def fake_llm(prompt: str) -> str:
            prompts.append(prompt)
            # 两轮都引用原论文 → 有效证据始终为 0
            return self._payload("large-batch training degrades generalization")

        r = ReflectionReviewer(llm_func=fake_llm)
        res = r.review("p1", "T", REPORT_TEXT, paper_text=PAPER_TEXT)

        assert res.evidence_rejections.get("from_paper", 0) >= 1
        assert res.llm_calls == 2  # 首轮 + 证据不足定向重试
        # 重试提示里必须写明「摘自原论文」，否则模型会原样再来一遍
        assert "原论文" in prompts[1]
        assert any("R1-diag" in o for o in res.hardcoded_overrides)

    def test_successful_retry_clears_stale_source_diagnosis(self):
        import json
        from mock_api.depth_eval_reflection import ReflectionReviewer

        good = json.dumps({
            "claims": [
                {"id": "C1", "text": "观点一", "evidence_id": "E1"},
                {"id": "C2", "text": "观点二", "evidence_id": "E2"},
            ],
            "evidence_pool": [
                {"id": "E1", "snippet": "批大小从 32 调到 128 后收敛明显变慢", "claim_ref": "C1"},
                {"id": "E2", "snippet": "作者的解释是学习率没有同步缩放", "claim_ref": "C2"},
            ],
            "understanding_accuracy": 0.85,
            "analysis_depth": 0.7,
            "innovative_insights": 0.5,
            "evidence_support": 0.85,
            "summary": "总结",
            "verdict_suggestion": "well_done",
        }, ensure_ascii=False)
        calls = [self._payload("large-batch training degrades generalization"), good]
        r = ReflectionReviewer(llm_func=lambda _: calls.pop(0))
        res = r.review("p1", "T", REPORT_TEXT, paper_text=PAPER_TEXT)

        assert res.effective_evidence_count >= 2
        assert res.evidence_rejections.get("from_paper", 0) == 0
        assert not any("R1-diag" in o for o in res.hardcoded_overrides)

    def test_valid_report_snippet_needs_no_retry(self):
        from mock_api.depth_eval_reflection import ReflectionReviewer

        import json

        payload = json.dumps(
            {
                "claims": [
                    {"id": "C1", "text": "观点一", "evidence_id": "E1"},
                    {"id": "C2", "text": "观点二", "evidence_id": "E2"},
                ],
                "evidence_pool": [
                    {"id": "E1", "snippet": "批大小从 32 调到 128 后收敛明显变慢", "claim_ref": "C1"},
                    {"id": "E2", "snippet": "作者的解释是学习率没有同步缩放", "claim_ref": "C2"},
                ],
                "understanding_accuracy": 0.85,
                "analysis_depth": 0.7,
                "innovative_insights": 0.5,
                "evidence_support": 0.85,
                "summary": "总结",
                "verdict_suggestion": "well_done",
            },
            ensure_ascii=False,
        )
        r = ReflectionReviewer(llm_func=lambda p: payload)
        res = r.review("p1", "T", REPORT_TEXT, paper_text=PAPER_TEXT)

        assert res.effective_evidence_count >= 2
        assert res.llm_calls == 1  # 证据够，不该触发重试
        assert res.scores["understanding_accuracy"] > 0.3  # 没被 R1 压分
