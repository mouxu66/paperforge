"""实验审计迭代 2 单测：claims 抽取 / P0-3 ablation / P0-6 baseline。

所有 LLM 调用 mock _text_qwen_chat，不访问真实模型。
"""

from __future__ import annotations

from unittest.mock import patch

from mock_api.experiment_audit import ablation, baseline, claims
from mock_api.experiment_audit.tables import ExtractedTable


# ---------------------------------------------------------------------------
# claims.py
# ---------------------------------------------------------------------------
class TestJsonParsing:
    def test_parse_plain_array(self):
        assert claims._parse_json_array('[{"a": 1}]') == [{"a": 1}]

    def test_parse_fenced_array(self):
        assert claims._parse_json_array('```json\n[{"a": 1}]\n```') == [{"a": 1}]

    def test_parse_array_with_noise(self):
        text = '好的，以下是结果：\n[{"claim_text": "x"}]\n希望有帮助。'
        assert claims._parse_json_array(text) == [{"claim_text": "x"}]

    def test_parse_garbage_returns_empty(self):
        assert claims._parse_json_array("not json at all") == []
        assert claims._parse_json_array("") == []

    def test_parse_object_variants(self):
        assert claims._parse_json_object('{"unfair": true}') == {"unfair": True}
        assert claims._parse_json_object('[{"unfair": false}]') == {"unfair": False}
        assert claims._parse_json_object("```json\n{\"unfair\": true}\n```") == {
            "unfair": True
        }
        assert claims._parse_json_object("garbage") == {}


class TestClaimNormalization:
    def test_missing_text_dropped(self):
        assert claims._normalize_claim({"claim_type": "ablation"}) is None

    def test_invalid_type_falls_back(self):
        c = claims._normalize_claim({"claim_text": "x", "claim_type": "????"})
        assert c["claim_type"] == "comparison"

    def test_valid_fields_kept(self):
        c = claims._normalize_claim(
            {
                "claim_text": "we improve 2%",
                "claim_type": "improvement",
                "related_metrics": ["accuracy"],
                "related_tables": ["2"],
            }
        )
        assert c["claim_type"] == "improvement"
        assert c["related_tables"] == ["2"]


class TestExtractClaims:
    def test_llm_success_preferred(self):
        fake = '[{"claim_text": "acc improves", "claim_type": "improvement"}]'
        with patch.object(claims, "_text_qwen_chat", return_value=fake):
            out = claims.extract_claims("some paragraph about Table 2")
        assert len(out) == 1
        assert out[0]["claim_type"] == "improvement"

    def test_llm_empty_falls_back_to_rules(self):
        with patch.object(claims, "_text_qwen_chat", return_value=""):
            out = claims.extract_claims("As shown in Table 3, accuracy reaches 90.1")
        assert len(out) == 1
        assert out[0]["related_tables"] == ["3"]
        assert "accuracy" in out[0]["related_metrics"]

    def test_rule_based_no_signal_returns_empty(self):
        assert claims.extract_claims_rule_based("nothing numeric here") == []

    def test_allow_llm_false_skips_llm(self):
        with patch.object(claims, "_text_qwen_chat") as m:
            out = claims.extract_claims("see Figure 2 for accuracy of 84.2", allow_llm=False)
        m.assert_not_called()
        assert out[0]["related_figures"] == ["2"]


# ---------------------------------------------------------------------------
# ablation.py (P0-3)
# ---------------------------------------------------------------------------
def _ablation_table(rows, table_id="Table 4"):
    return ExtractedTable(
        page=7, table_index=0, table_id=table_id, bbox=[0, 0, 100, 100], rows=rows
    )


class TestAblation:
    def test_find_ablation_tables_by_wo_rows(self):
        t = _ablation_table(
            [["Variant", "Accuracy"], ["Ours", "84.2"], ["w/o ModuleA", "81.0"]]
        )
        assert ablation.find_ablation_tables([t]) == [t]

    def test_non_ablation_table_not_matched(self):
        t = _ablation_table([["Method", "Accuracy"], ["Ours", "84.2"]])
        assert ablation.find_ablation_tables([t]) == []

    def test_removal_row_not_worse_with_universal_claim_flagged(self):
        t = _ablation_table(
            [["Variant", "Accuracy"], ["Ours", "84.2"], ["w/o ModuleA", "84.5"]]
        )
        text = (
            "Our ablation study is shown in Table 4. "
            "Each component contributes to the final performance of our model."
        )
        findings = ablation.check_ablation_consistency(text, [t])
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "ABLATION_UNSUPPORTED"
        assert f["page"] == 7
        assert "w/o ModuleA" in f["computed"]
        assert f["needs_human_review"] is True

    def test_removal_row_worse_no_finding(self):
        t = _ablation_table(
            [["Variant", "Accuracy"], ["Ours", "84.2"], ["w/o ModuleA", "80.1"]]
        )
        text = "Each component contributes to the final performance of our model."
        assert ablation.check_ablation_consistency(text, [t]) == []

    def test_no_full_model_row_skipped(self):
        t = _ablation_table(
            [["Variant", "Accuracy"], ["w/o ModuleA", "84.5"], ["w/o ModuleB", "84.9"]]
        )
        assert ablation.check_ablation_consistency("any text", [t]) == []

    def test_no_tables_returns_empty(self):
        assert ablation.check_ablation_consistency("text", []) == []


# ---------------------------------------------------------------------------
# baseline.py (P0-6)
# ---------------------------------------------------------------------------
_UNFAIR_SENTENCE = (
    "Note that the baseline model was trained with a different pretraining "
    "dataset and lower input resolution compared with our method."
)


class TestBaseline:
    def test_candidate_collection(self):
        cands = baseline._collect_candidate_sentences(_UNFAIR_SENTENCE)
        assert len(cands) == 1

    def test_no_candidate_no_llm_call(self):
        with patch.object(baseline, "_text_qwen_chat") as m:
            assert baseline.check_baseline_fairness("plain text about models.") == []
        m.assert_not_called()

    def test_llm_unfair_produces_finding(self):
        with patch.object(
            baseline,
            "_text_qwen_chat",
            return_value='{"unfair": true, "reason": "baseline 用更弱预训练"}',
        ):
            findings = baseline.check_baseline_fairness(_UNFAIR_SENTENCE)
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "BASELINE_UNFAIR"
        assert f["severity"] == "high"
        assert f["needs_human_review"] is True
        assert "更弱预训练" in f["computed"]

    def test_llm_fair_no_finding(self):
        with patch.object(
            baseline, "_text_qwen_chat", return_value='{"unfair": false, "reason": "ok"}'
        ):
            assert baseline.check_baseline_fairness(_UNFAIR_SENTENCE) == []

    def test_llm_unavailable_falls_back_to_rules(self):
        # LLM 不可用时回退到规则判定（要求更强信号），强信号句子仍产出 finding
        with patch.object(baseline, "_text_qwen_chat", return_value=""):
            findings = baseline.check_baseline_fairness(_UNFAIR_SENTENCE)
        assert len(findings) == 1
        assert findings[0]["type"] == "BASELINE_UNFAIR"
        assert "规则" in findings[0]["computed"]

    def test_llm_unavailable_weak_signal_no_finding(self):
        # LLM 不可用 + 弱信号（不同 data augmentation，非强差异关键词）→ 不产出 finding
        weak = "The baseline was trained with different data augmentation compared to ours."
        with patch.object(baseline, "_text_qwen_chat", return_value=""):
            assert baseline.check_baseline_fairness(weak) == []
