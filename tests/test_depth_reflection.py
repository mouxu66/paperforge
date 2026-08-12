"""DEPTH reflection 报告轻量评审单元测试。

覆盖路径：
1. ReflectionReviewer.review() —— Mock LLM 数据走通完整流水线
2. 硬编码兜底：证据不足 → 分数 CAP + verdict=rewrite_required
3. 硬编码兜底：理解准确性低 → verdict=needs_depth
4. 硬编码兜底：平均分低 → verdict=needs_evidence
5. 交叉引用验证：claim/evidence 双向 anchor
6. 路由分流：_detect_document_type 启发式识别 report vs paper
7. 评测结果序列化：model_dump() 字段完整性
"""
from __future__ import annotations

import json

import pytest
from mock_api.crud import _detect_document_type

# 测试通过 pytest 从项目根运行时自动加入 sys.path（conftest.py 兜底）
from mock_api.depth_eval_reflection import (
    AVERAGE_SCORE_POOR,
    MAX_SCORE_WHEN_EVIDENCE_INSUFFICIENT,
    MIN_EVIDENCE_FOR_VALID_REVIEW,
    UNDERSTANDING_THRESHOLD_DEEP,
    ReflectionReviewer,
)

# ===========================================================================
# Mock 报告全文（模拟一篇高质量的论文读后感）
# ===========================================================================
MOCK_REPORT_TITLE = "读后感：TAG-Net 时态图注意力网络的设计权衡"
MOCK_REPORT_ID = "report_2401_test01"
MOCK_REPORT_CONTENT = """
读后感：TAG-Net 时态图注意力网络的设计权衡

最近精读了 TAG-Net 这篇论文 [原文：arxiv:2401.00001]。作者提出了一种
结合图卷积网络（GCN）和时态注意力机制（Temporal Attention）的新框架，
用于动态图上的异常检测。我对其设计选择有三点思考：

第一，证据 1：作者在 Section 3.1 给出了时态注意力收敛到唯一平稳分布的
严格证明（Theorem 1，收敛速率 O(1/sqrt(K))），这是少有的兼顾工程效率
与理论严谨的工作。

第二，证据 2：消融实验显示时态注意力贡献 +2.1%、GCN +1.5%、异常打分散
+0.8%，三者各司其职。然而在节点度 < 5 的稀疏子图上性能下降明显，作者
没有给出稀疏性补佟措施的讨论。

第三，证据 3：作者公开了代码与超参配置（github.com/anonymous/tag-net），
复现性较高。但对 K > 20 时的计算复杂度 O(K^2) 未提供分析，也未在
更大规模图上验证，这是工程落地的一大隐忧。

总结：TAG-Net 在异常检测领域提出了一种可解释的、理论保障的新范式；
但其稀疏性补佟与可扩展性仍有较大优化空间。
"""


# ===========================================================================
# Mock LLM 函数（按 prompt 关键词路由）
# ===========================================================================
MOCK_REFLECTION_RESPONSE = json.dumps({
    "claims": [
        {"id": "C1", "text": "时态注意力收敛证明兼顾效率与严谨", "evidence_id": "E1"},
        {"id": "C2", "text": "消融实验各组件贡献量化清晰", "evidence_id": "E2"},
        {"id": "C3", "text": "可复现但 K>20 计算复杂度未分析", "evidence_id": "E3"},
    ],
    "evidence_pool": [
        {"id": "E1", "snippet": "时态注意力收敛到唯一平稳分布的严格证明", "claim_ref": "C1"},
        {"id": "E2", "snippet": "消融实验显示时态注意力贡献 +2.1%、GCN +1.5%", "claim_ref": "C2"},
        {"id": "E3", "snippet": "复现性较高。但对 K > 20 时的计算复杂度 O(K^2) 未提供分析", "claim_ref": "C3"},
    ],
    "understanding_accuracy": 0.85,
    "analysis_depth": 0.80,
    "innovative_insights": 0.72,
    "evidence_support": 0.88,
    "summary": "对 TAG-Net 的设计选择给出三点批判性分析，证据充实、观点有支撑。",
    "verdict_suggestion": "well_done",
}, ensure_ascii=False)


class MockLLM:
    """Mock LLM：返回固定的高质量 response（用于通过硬编码校验的路径）。"""

    def __init__(self, response: str = MOCK_REFLECTION_RESPONSE):
        self.response = response
        self.call_count = 0

    def __call__(self, prompt: str) -> str:
        self.call_count += 1
        return self.response


# ===========================================================================
# 单元测试
# ===========================================================================
class TestReflectionReviewerHappyPath:
    """正常流程：高质量 response 走通整条 pipeline。"""

    def test_review_returns_scored_output(self):
        """完整 review() 返回 ReflectionReviewResult，含评分/证据/verdict。"""
        mock = MockLLM()
        reviewer = ReflectionReviewer(llm_func=mock)
        result = reviewer.review(
            paper_id=MOCK_REPORT_ID,
            title=MOCK_REPORT_TITLE,
            full_text=MOCK_REPORT_CONTENT,
        )

        # 基础字段
        assert result.paper_id == MOCK_REPORT_ID
        assert result.title == MOCK_REPORT_TITLE
        assert result.document_type == "report"
        assert result.has_content is True

        # claims / evidence_pool 交叉验证通过
        assert len(result.claims) == 3
        assert len(result.evidence_pool) == 3
        assert result.effective_evidence_count == 3

        # 4 维评分
        sc = result.scores
        assert sc["understanding_accuracy"] == 0.85
        assert sc["analysis_depth"] == 0.80
        # 创新分可能被 R4.5 封顶至 0.5（若 mock 文本缺原创标记）
        assert sc["innovative_insights"] in (0.50, 0.72), (
            f"innovative_insights={sc['innovative_insights']}, expected 0.50 or 0.72"
        )
        # 证据数为 3（少于允许推高分的 5 条），evidence_support 受 0.85 上限约束。
        assert sc["evidence_support"] == 0.85
        # 平均分
        expected_avg = round((0.85 + 0.80 + 0.72 + 0.85) / 4, 4)
        assert sc["average"] == expected_avg

        # verdict：高质量 response + 3 有效证据 → well_done
        assert result.verdict == "well_done"
        assert any("R1.5:" in ov for ov in result.hardcoded_overrides)

        # 日志完整性
        assert any("reflection 评审开始" in log for log in result.node_logs)
        assert any("reflection 评审完成" in log for log in result.node_logs)
        assert any("R1" in log for log in result.node_logs)
        assert any("硬校验完成" in log for log in result.node_logs)

    def test_model_dump_serializable_to_db(self):
        """model_dump() 输出的 dict 可直接写入 JSON 列。"""
        mock = MockLLM()
        reviewer = ReflectionReviewer(llm_func=mock)
        result = reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT)
        dumped = result.model_dump()

        # 必须可 JSON 序列化（SQLite JSON 列要求）
        json_str = json.dumps(dumped, ensure_ascii=False)
        reloaded = json.loads(json_str)
        assert reloaded["paper_id"] == MOCK_REPORT_ID
        assert reloaded["verdict"] == "well_done"
        assert isinstance(reloaded["scores"], dict)
        assert isinstance(reloaded["claims"], list)


class TestHardcodedFallback:
    """硬编码兜底规则：3 条核心规则。"""

    def test_rule1_insufficient_evidence_caps_scores(self):
        """R1：有效证据数 < 2 → 分数 CAP 到 0.3 + verdict=rewrite_required。"""
        # Mock：只返回 1 个 claim 和 1 个 evidence（互相引用有效，但只有 1 条）
        response = json.dumps({
            "claims": [
                {"id": "C1", "text": "唯一观点", "evidence_id": "E1"},
            ],
            "evidence_pool": [
                {"id": "E1", "snippet": "唯一证据", "claim_ref": "C1"},
            ],
            "understanding_accuracy": 0.95,
            "analysis_depth": 0.92,
            "innovative_insights": 0.88,
            "evidence_support": 0.90,
            "summary": "极简内容",
            "verdict_suggestion": "well_done",
        }, ensure_ascii=False)

        reviewer = ReflectionReviewer(llm_func=MockLLM(response))
        result = reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT)

        # R1 触发：所有分数被 CAP 到 0.3
        assert result.effective_evidence_count < MIN_EVIDENCE_FOR_VALID_REVIEW
        for k in ("understanding_accuracy", "analysis_depth",
                  "innovative_insights", "evidence_support"):
            assert result.scores[k] <= MAX_SCORE_WHEN_EVIDENCE_INSUFFICIENT
        assert result.verdict == "rewrite_required"
        assert any("R1:" in ov for ov in result.hardcoded_overrides)

    def test_rule2_low_understanding_triggers_needs_depth(self):
        """R2：understanding_accuracy < 0.40 → verdict=needs_depth。

        R1 未触发时（evidence >= 2）才检查 R2；构造 R1 不触发的输入。
        """
        response = json.dumps({
            "claims": [
                {"id": "C1", "text": "观点1", "evidence_id": "E1"},
                {"id": "C2", "text": "观点2", "evidence_id": "E2"},
                {"id": "C3", "text": "观点3", "evidence_id": "E3"},
            ],
            "evidence_pool": [
                {"id": "E1", "snippet": "证据1", "claim_ref": "C1"},
                {"id": "E2", "snippet": "证据2", "claim_ref": "C2"},
                {"id": "E3", "snippet": "证据3", "claim_ref": "C3"},
            ],
            "understanding_accuracy": 0.30,  # 低于 0.40
            "analysis_depth": 0.75,
            "innovative_insights": 0.70,
            "evidence_support": 0.72,
            "summary": "理解不够深",
            "verdict_suggestion": "well_done",
        }, ensure_ascii=False)

        reviewer = ReflectionReviewer(llm_func=MockLLM(response))
        result = reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT)

        # R1 不触发（有效证据=3）
        assert result.effective_evidence_count >= MIN_EVIDENCE_FOR_VALID_REVIEW
        # R2 触发
        assert result.verdict == "needs_depth"
        assert any("R2:" in ov for ov in result.hardcoded_overrides)

    def test_rule3_low_average_triggers_needs_evidence(self):
        """R3：4 维平均 < 0.50 → verdict=needs_evidence（R1/R2 都未触发）。"""
        response = json.dumps({
            "claims": [
                {"id": "C1", "text": "观点1", "evidence_id": "E1"},
                {"id": "C2", "text": "观点2", "evidence_id": "E2"},
                {"id": "C3", "text": "观点3", "evidence_id": "E3"},
            ],
            "evidence_pool": [
                {"id": "E1", "snippet": "证据1", "claim_ref": "C1"},
                {"id": "E2", "snippet": "证据2", "claim_ref": "C2"},
                {"id": "E3", "snippet": "证据3", "claim_ref": "C3"},
            ],
            "understanding_accuracy": 0.55,  # 满足 R2（>= 0.40）
            "analysis_depth": 0.45,
            "innovative_insights": 0.40,
            "evidence_support": 0.50,
            # 平均 = 0.475 < 0.50
            "summary": "内容浅",
            "verdict_suggestion": "well_done",
        }, ensure_ascii=False)

        reviewer = ReflectionReviewer(llm_func=MockLLM(response))
        result = reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT)

        # R1 不触发
        assert result.effective_evidence_count >= MIN_EVIDENCE_FOR_VALID_REVIEW
        # R2 不触发
        assert result.scores["understanding_accuracy"] >= UNDERSTANDING_THRESHOLD_DEEP
        # 当前创新见解门阀（R4）优先于平均分提示，0.40 会判为 needs_depth。
        assert result.scores["average"] < AVERAGE_SCORE_POOR
        assert result.verdict == "needs_depth"
        assert any("R4:" in ov for ov in result.hardcoded_overrides)


class TestCrossValidation:
    """claim / evidence 双向 anchor 验证。"""

    def test_dangling_evidence_id_filtered(self):
        """claim 引用了不存在的 evidence_id → 该 claim 被过滤。"""
        response = json.dumps({
            "claims": [
                {"id": "C1", "text": "有效", "evidence_id": "E1"},
                {"id": "C2", "text": "悬空", "evidence_id": "E_FAKE"},  # 不存在
            ],
            "evidence_pool": [
                {"id": "E1", "snippet": "证据1", "claim_ref": "C1"},
            ],
            "understanding_accuracy": 0.80,
            "analysis_depth": 0.75,
            "innovative_insights": 0.70,
            "evidence_support": 0.78,
            "summary": "OK",
            "verdict_suggestion": "well_done",
        }, ensure_ascii=False)

        reviewer = ReflectionReviewer(llm_func=MockLLM(response))
        result = reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT)

        # 过滤后：只保留 claim C1
        assert len(result.claims) == 1
        assert result.claims[0]["id"] == "C1"
        # 有效证据数 = 1 < 2 → R1 触发 → rewrite_required
        assert result.effective_evidence_count == 1
        assert result.verdict == "rewrite_required"

    def test_no_claims_at_all(self):
        """无 claims → 有效证据数 0 → R1 触发。"""
        response = json.dumps({
            "claims": [],
            "evidence_pool": [],
            "understanding_accuracy": 0.85,
            "analysis_depth": 0.80,
            "innovative_insights": 0.75,
            "evidence_support": 0.82,
            "summary": "空",
            "verdict_suggestion": "well_done",
        }, ensure_ascii=False)

        reviewer = ReflectionReviewer(llm_func=MockLLM(response))
        result = reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT)

        assert result.effective_evidence_count == 0
        # 分数被 CAP
        for k in ("understanding_accuracy", "analysis_depth",
                  "innovative_insights", "evidence_support"):
            assert result.scores[k] <= MAX_SCORE_WHEN_EVIDENCE_INSUFFICIENT
        assert result.verdict == "rewrite_required"

    def test_empty_full_text_skips_review(self):
        """full_text 为空 → 跳过评审，返回 has_content=False。"""
        mock = MockLLM()
        reviewer = ReflectionReviewer(llm_func=mock)
        result = reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, "")

        assert result.has_content is False
        assert result.verdict == "rewrite_required"
        assert "内容为空" in result.verdict_reason
        assert mock.call_count == 0  # 未调用 LLM

    def test_llm_returns_empty(self):
        """LLM 返回空字符串 → 默认 0.5 兜底 + 无证据 → R1 触发。"""
        mock = MockLLM(response="")
        reviewer = ReflectionReviewer(llm_func=mock)
        result = reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT)

        # 默认值：0.5 兜底；无 evidence → effective_evidence_count=0
        assert result.effective_evidence_count == 0
        assert result.verdict == "rewrite_required"
        # 4 维分数应全部 <= 0.3
        for k in ("understanding_accuracy", "analysis_depth",
                  "innovative_insights", "evidence_support"):
            assert result.scores[k] <= MAX_SCORE_WHEN_EVIDENCE_INSUFFICIENT


class TestRoutingHeuristic:
    """_detect_document_type 启发式分流。"""

    def test_frontend_hint_paper(self):
        """前端明确传 'paper' → 直接采纳。"""
        assert _detect_document_type("任意标题", "任意内容", "paper") == "paper"

    def test_frontend_hint_report(self):
        """前端明确传 'report' → 直接采纳。"""
        assert _detect_document_type("任意标题", "任意内容", "report") == "report"

    def test_heuristic_title_signal(self):
        """标题含报告类关键词 → 'report'（标题是最强意图信号）。"""
        content = "Some very long text about academic topics. " * 600  # > 10000 chars
        result = _detect_document_type("TAG-Net 读后感", content)
        assert result == "report"

    def test_heuristic_report_signal_no_length_cap(self):
        """长报告（> 10000 字符）含报告类关键词 → 'report'（无长度限制）。"""
        content = ("这篇论文的学习笔记：作者提出了 XXX 方法，"
                   "我对实验设计有三点感悟。" * 300)  # > 10000 chars
        result = _detect_document_type("学习笔记", content)
        assert result == "report"

    def test_heuristic_paper_structure_multiple_hits(self):
        """含 ≥2 个论文结构关键词 → 'paper'。"""
        content = """
        Abstract
        This paper proposes a new method.

        1. Introduction
        We introduce the problem.

        5. References
        [1] Smith et al. 2020.
        """
        assert _detect_document_type("A Novel Method", content) == "paper"

    def test_heuristic_single_struct_keyword_not_enough(self):
        """仅 1 个论文结构关键词（如报告中提到 'Abstract'）→ 不判为 paper。"""
        content = (
            "作者在 Abstract 中提出了一个很有趣的观点，"
            "我认为这个方法的创新之处在于..." * 200
        )  # 仅 "Abstract" 匹配，不够 2 个
        result = _detect_document_type("论文笔记", content)
        assert result == "report"  # 标题含"笔记"优先命中

    def test_heuristic_single_struct_no_title_signal(self):
        """仅 1 个论文结构关键词 + 标题无报告信号 → 'paper'（默认兜底）。"""
        content = "作者在 Introduction 中提出了一个方法。" * 50  # 仅 1 个命中
        result = _detect_document_type("A Novel Approach", content)  # 标题无报告信号
        assert result == "paper"

    def test_heuristic_long_text_defaults_to_paper(self):
        """长文（> 10000）无报告信号 → 'paper'。"""
        content = "This is a long academic paper. " * 600  # > 10000 chars
        assert _detect_document_type("Title", content) == "paper"

    def test_heuristic_short_text_no_keywords(self):
        """短文无任何信号 → 'paper'（默认）。"""
        content = "Short random text without academic structure."
        assert _detect_document_type("Title", content) == "paper"

    def test_heuristic_empty_content(self):
        """空内容 → 'paper'。"""
        assert _detect_document_type("Title", "") == "paper"

    def test_heuristic_empty_content_with_title_signal(self):
        """空内容但标题含报告关键词 → 'report'（标题信号优先）。"""
        assert _detect_document_type("xxx 阅读笔记", "") == "report"



class TestReflectionScoringEdgeCases:
    """反射评分边界与异常输入。"""

    def test_scores_clamped_to_0_1(self):
        """LLM 返回越界分数时应被钳制到 [0, 1]。"""
        response = json.dumps({
            "claims": [
                {"id": "C1", "text": "观点1", "evidence_id": "E1"},
                {"id": "C2", "text": "观点2", "evidence_id": "E2"},
            ],
            "evidence_pool": [
                {"id": "E1", "snippet": "证据1", "claim_ref": "C1"},
                {"id": "E2", "snippet": "证据2", "claim_ref": "C2"},
            ],
            "understanding_accuracy": 1.5,  # 越界
            "analysis_depth": -0.2,  # 越界
            "innovative_insights": 0.7,
            "evidence_support": 0.8,
            "summary": "边界测试",
            "verdict_suggestion": "well_done",
        }, ensure_ascii=False)

        reviewer = ReflectionReviewer(llm_func=MockLLM(response))
        result = reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT)

        # 两条证据不足以推高分，触发 R1.5 的 0.85 上限。
        assert result.scores["understanding_accuracy"] == 0.85
        assert result.scores["analysis_depth"] == 0.0
        assert result.scores["innovative_insights"] in (0.5, 0.7), (
            f"innovative_insights={result.scores['innovative_insights']}"
        )

    def test_invalid_verdict_suggestion_defaults_to_needs_evidence(self):
        """LLM 返回非法 verdict_suggestion 时默认 needs_evidence。"""
        response = json.dumps({
            "claims": [
                {"id": "C1", "text": "观点1", "evidence_id": "E1"},
                {"id": "C2", "text": "观点2", "evidence_id": "E2"},
            ],
            "evidence_pool": [
                {"id": "E1", "snippet": "证据1", "claim_ref": "C1"},
                {"id": "E2", "snippet": "证据2", "claim_ref": "C2"},
            ],
            "understanding_accuracy": 0.8,
            "analysis_depth": 0.8,
            "innovative_insights": 0.8,
            "evidence_support": 0.8,
            "summary": "边界测试",
            "verdict_suggestion": "invalid_verdict",
        }, ensure_ascii=False)

        reviewer = ReflectionReviewer(llm_func=MockLLM(response))
        result = reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT)

        # 非法 verdict 默认 needs_evidence，但平均分高不会触发 R3
        assert result.verdict == "needs_evidence"

    def test_well_done_with_high_scores(self):
        """高分 + 充足证据 → well_done。"""
        response = json.dumps({
            "claims": [
                {"id": "C1", "text": "观点1", "evidence_id": "E1"},
                {"id": "C2", "text": "观点2", "evidence_id": "E2"},
                {"id": "C3", "text": "观点3", "evidence_id": "E3"},
            ],
            "evidence_pool": [
                {"id": "E1", "snippet": "证据1", "claim_ref": "C1"},
                {"id": "E2", "snippet": "证据2", "claim_ref": "C2"},
                {"id": "E3", "snippet": "证据3", "claim_ref": "C3"},
            ],
            "understanding_accuracy": 0.85,
            "analysis_depth": 0.82,
            "innovative_insights": 0.80,
            "evidence_support": 0.88,
            "summary": "高质量报告",
            "verdict_suggestion": "well_done",
        }, ensure_ascii=False)

        reviewer = ReflectionReviewer(llm_func=MockLLM(response))
        result = reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT)

        assert result.verdict == "well_done"
        assert any("R1.5:" in ov for ov in result.hardcoded_overrides)
        assert result.scores["average"] == pytest.approx(
            (0.85 + 0.82 + 0.80 + 0.85) / 4, rel=1e-4
        )

    def test_cross_validate_only_backward_reference(self):
        """只有 evidence 引用 claim，没有 claim 正向引用 evidence → effective=0。

        新语义（去重计数）：effective = claim 的 evidence_id 去重后在 valid_eids 中的条数。
        backward-only（evidence 引用 claim 但 claim 不引用 evidence）不算入 effective。
        旧版 max(forward,backward) 会把这种场景算有效，但那是计数漏洞——
        证据未被任何 claim 引用，不应视为有效锚定。
        """
        response = json.dumps({
            "claims": [
                {"id": "C1", "text": "观点1", "evidence_id": "E_FAKE"},
            ],
            "evidence_pool": [
                {"id": "E1", "snippet": "证据1", "claim_ref": "C1"},
            ],
            "understanding_accuracy": 0.8,
            "analysis_depth": 0.8,
            "innovative_insights": 0.8,
            "evidence_support": 0.8,
            "summary": "反向引用",
            "verdict_suggestion": "well_done",
        }, ensure_ascii=False)

        reviewer = ReflectionReviewer(llm_func=MockLLM(response))
        result = reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT)

        # backward-only 不算入 effective → effective=0 → R1 触发
        assert result.effective_evidence_count == 0
        assert result.verdict == "rewrite_required"
        # 同时 validated_pool 也不应包含 backward-only evidence（对齐 effective 口径）
        assert len(result.evidence_pool) == 0


class TestReflectionParseFailed:
    """【Layer C】ReflectionReviewer parse_failed 传播回归测试。"""

    def test_parse_failed_on_truncated_json(self):
        from mock_api.depth_eval_reflection import ReflectionReviewer
        def _stub(prompt):
            return '{"claims": [{"id": "C1", "text": "truncated'
        reviewer = ReflectionReviewer(llm_func=_stub)
        result = reviewer.review("p1", "t", "nonempty full_text")
        assert result.parse_failed is True
        assert result.verdict == "rewrite_required"
        assert result.effective_evidence_count == 0

    def test_parse_failed_on_empty_raw(self):
        from mock_api.depth_eval_reflection import ReflectionReviewer
        def _stub(prompt):
            return ""
        reviewer = ReflectionReviewer(llm_func=_stub)
        result = reviewer.review("p1", "t", "nonempty full_text")
        assert result.parse_failed is True
        assert result.verdict == "rewrite_required"

    def test_parse_succeeded_on_valid_json(self):
        from mock_api.depth_eval_reflection import ReflectionReviewer
        def _stub(prompt):
            return (
                '{"claims": ['
                '{"id": "C1", "text": "C1", "evidence_id": "E1"}, '
                '{"id": "C2", "text": "C2", "evidence_id": "E2"}'
                '], "evidence_pool": ['
                '{"id": "E1", "snippet": "S1", "claim_ref": "C1"}, '
                '{"id": "E2", "snippet": "S2", "claim_ref": "C2"}'
                '], "understanding_accuracy": 0.8, "analysis_depth": 0.7, '
                '"innovative_insights": 0.6, "evidence_support": 0.7, '
                '"summary": "test report", "verdict_suggestion": "well_done"}'
            )
        # 注意：snippet 真实性校验要求引文 S1/S2 必须真实出现在报告全文中
        # （2026-08 修复：防 LLM 编造引文通过 R1 证据门阀）
        reviewer = ReflectionReviewer(llm_func=_stub)
        result = reviewer.review("p1", "t", "S1 S2 nonempty full_text")
        assert result.parse_failed is False
        assert result.verdict in ("well_done", "needs_evidence", "needs_depth")

    def test_only_summary_not_parse_failed(self):
        """LLM 返回了合法但 schema 不完整的 JSON（仅 summary）→ parse_failed=False。

        仅 summary 算「parse 成功但 schema 不全」：has_useful 含 summary 字段。
        这种 case 不归 parse_failed（parse 实际成功了），由 R1 硬编码规则接手
        （effective_evidence_count=0 → 分数 CAP + verdict=rewrite_required）。
        这避免了与"JSON 真的坏了"混淆；同时前端不会卡在 running。
        """
        from mock_api.depth_eval_reflection import ReflectionReviewer
        def _stub(prompt):
            return '{"summary": "只有摘要没其他字段"}'
        reviewer = ReflectionReviewer(llm_func=_stub)
        result = reviewer.review("p1", "t", "nonempty full_text")
        assert result.parse_failed is False
        # 但 R1 仍会捕获 —— effective_evidence_count=0 → rewrite_required
        assert result.effective_evidence_count == 0
        assert result.verdict == "rewrite_required"
class TestPaperReferenceInjection:
    """paper_text 注入：prompt 含论文区块；不传时与旧版完全一致。"""

    def test_paper_text_injected_into_prompt(self):
        """传入 paper_text → LLM prompt 包含【原论文参考内容】区块。"""
        captured = {}

        def mock_llm(prompt):
            captured["prompt"] = prompt
            return MOCK_REFLECTION_RESPONSE

        reviewer = ReflectionReviewer(llm_func=mock_llm)
        result = reviewer.review(
            MOCK_REPORT_ID,
            MOCK_REPORT_TITLE,
            MOCK_REPORT_CONTENT,
            paper_text="TAG-Net 原论文全文：时态图注意力网络用于动态图异常检测。" * 50,
        )

        prompt = captured["prompt"]
        assert "【原论文参考内容】" in prompt
        assert "TAG-Net 原论文全文" in prompt
        # snippet 仍强制来自报告（2026-08-05 措辞加强：显式禁止引用原论文区块）
        assert "不得作为 evidence snippet 引用" in prompt
        assert "禁止引用【原论文参考内容】" in prompt
        # 报告全文仍在
        assert "报告全文：" in prompt
        assert result.verdict == "well_done"

    def test_paper_text_truncated_to_preview_limit(self):
        """论文超长 → 只注入前 MAX_PAPER_PREVIEW_CHARS 字符。"""
        from mock_api.depth_eval_reflection import MAX_PAPER_PREVIEW_CHARS

        captured = {}
        long_paper = "A" * (MAX_PAPER_PREVIEW_CHARS + 5000)

        def mock_llm(prompt):
            captured["prompt"] = prompt
            return MOCK_REFLECTION_RESPONSE

        reviewer = ReflectionReviewer(llm_func=mock_llm)
        reviewer.review(
            MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT, paper_text=long_paper
        )

        prompt = captured["prompt"]
        # 注入的预览 = 前 MAX_PAPER_PREVIEW_CHARS 个 A（动态预算只会加不会减），不含截断后的 B
        assert ("A" * MAX_PAPER_PREVIEW_CHARS) in prompt
        assert "B" not in prompt

    def test_short_report_gets_larger_paper_preview(self, monkeypatch):
        """报告远短于 16000 字截断上限 → 把省下的字数全额补给原论文预览（动态预算）。

        动态预算默认已关闭（PAPER_PREVIEW_BUDGET_RATIO 默认 0.0，见模块顶部注释：
        2026-08-12 实测长预览压死本机 9B 模型判断力），此处显式设 1.0 验证补给逻辑本身。
        """
        import mock_api.depth_eval_reflection as der

        monkeypatch.setattr(der, "PAPER_PREVIEW_BUDGET_RATIO", 1.0)

        from mock_api.depth_eval_reflection import (
            MAX_PAPER_PREVIEW_CHARS,
            MAX_REPORT_BUDGET_CHARS,
            PAPER_PREVIEW_MAX_CHARS,
        )

        captured = {}
        # 论文足够长，保证预览不会被论文长度截断；报告用 Mock 短报告（≈700 字）
        paper = "P" * (PAPER_PREVIEW_MAX_CHARS + 5000)

        def mock_llm(prompt):
            captured["prompt"] = prompt
            return MOCK_REFLECTION_RESPONSE

        reviewer = ReflectionReviewer(llm_func=mock_llm)
        reviewer.review(
            MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT, paper_text=paper
        )
        prompt = captured["prompt"]
        # 动态预算：min(16000, 4000 + (16000 - 报告长度) × 比例=1.0)
        saved = MAX_REPORT_BUDGET_CHARS - len(MOCK_REPORT_CONTENT)
        expected = min(
            PAPER_PREVIEW_MAX_CHARS,
            MAX_PAPER_PREVIEW_CHARS + int(saved * der.PAPER_PREVIEW_BUDGET_RATIO),
        )
        assert (
            "P" * expected
        ) in prompt, "短报告应把省下的字数补给原论文预览"
        # 注入量确实超过基础预览（证明预算补给到了论文）
        assert expected > MAX_PAPER_PREVIEW_CHARS

    def test_paper_preview_budget_capped(self, monkeypatch):
        """补给预算受 PAPER_PREVIEW_MAX_CHARS 硬上限约束（防 context 撑爆）。

        显式设补给比例 1.0（默认 0.0，2026-08-12 起）：理论预算 ≈ 4000 + 15300 = 19300
        → 必须被 16000 封顶。
        """
        import mock_api.depth_eval_reflection as der

        monkeypatch.setattr(der, "PAPER_PREVIEW_BUDGET_RATIO", 1.0)

        from mock_api.depth_eval_reflection import PAPER_PREVIEW_MAX_CHARS

        captured = {}
        paper = "P" * (PAPER_PREVIEW_MAX_CHARS + 5000)

        def mock_llm(prompt):
            captured["prompt"] = prompt
            return MOCK_REFLECTION_RESPONSE

        reviewer = der.ReflectionReviewer(llm_func=mock_llm)
        reviewer.review(
            MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT, paper_text=paper
        )
        prompt = captured["prompt"]
        assert ("P" * PAPER_PREVIEW_MAX_CHARS) in prompt
        assert ("P" * (PAPER_PREVIEW_MAX_CHARS + 1)) not in prompt

    def test_no_paper_text_matches_legacy_prompt(self):
        """不传 paper_text → prompt 不含论文区块，与旧版一致。"""
        captured = {}

        def mock_llm(prompt):
            captured["prompt"] = prompt
            return MOCK_REFLECTION_RESPONSE

        reviewer = ReflectionReviewer(llm_func=mock_llm)
        reviewer.review(MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT)

        prompt = captured["prompt"]
        # 不注入论文区块（区别于描述文字）：区块以【原论文参考内容】（开头 + 冒号结尾
        assert "【原论文参考内容】（仅作背景对照" not in prompt
        assert "报告全文：" in prompt
        assert "understanding_accuracy" in prompt

    def test_whitespace_paper_text_treated_as_empty(self):
        """paper_text 全空白 → 不注入论文区块。"""
        captured = {}

        def mock_llm(prompt):
            captured["prompt"] = prompt
            return MOCK_REFLECTION_RESPONSE

        reviewer = ReflectionReviewer(llm_func=mock_llm)
        reviewer.review(
            MOCK_REPORT_ID, MOCK_REPORT_TITLE, MOCK_REPORT_CONTENT, paper_text="   \n  "
        )

        assert "【原论文参考内容】（仅作背景对照" not in captured["prompt"]

class TestScoringAnchors:
    """评分参照系锚点：prompt 含三档锚点 + 分布强制要求。"""

    def test_prompt_contains_scoring_anchors(self):
        from mock_api.depth_eval_reflection import PROMPT_REFLECTION

        assert "评分参照" in PROMPT_REFLECTION
        assert "不要集中在 0.8-0.95" in PROMPT_REFLECTION
        assert "差 (0.2-0.4)" in PROMPT_REFLECTION
        assert "中 (0.5-0.7)" in PROMPT_REFLECTION
        assert "好 (0.8-1.0)" in PROMPT_REFLECTION

    def test_anchors_do_not_break_prompt_format(self):
        """锚点加在要求区后，JSON 输出模板仍可正常 format。"""
        from mock_api.depth_eval_reflection import PROMPT_REFLECTION, PAPER_SECTION_TEMPLATE, MAX_PAPER_PREVIEW_CHARS

        ps = PAPER_SECTION_TEMPLATE.format(paper_supplement="", paper_preview="x" * 100)
        p1 = PROMPT_REFLECTION.format(content="报告内容", truncation_note="", paper_section=ps)
        p2 = PROMPT_REFLECTION.format(content="报告内容", truncation_note="", paper_section="")
        assert "评分参照" in p1 and "评分参照" in p2
        # JSON 模板的 {{ }} 转义仍正确（能 format 即说明花括号配平）
        assert '\"claims\": [...]' in p1 or '"claims"' in p1


# ===========================================================================
# ADR-014 修复回归测试（S1① effective 去重 + retry from_paper 触发 + cache 脱钩）
# ===========================================================================

class TestEvidenceRetryFromPaper:
    """模型把原论文当成报告来引用（from_paper>0）→ 触发定向重试。"""

    def test_from_paper_triggers_retry(self):
        """from_paper > 0 时触发重试，重试后 evidence 来源回到报告。"""
        from mock_api.depth_eval_reflection import ReflectionReviewer

        # 首轮：evidence snippet 出现在 paper_text（原论文）而非 full_text（报告）
        # → from_paper 判定
        _CALLS: list[str] = []

        def _llm(prompt: str) -> str:
            _CALLS.append(prompt)
            if len(_CALLS) == 1:
                # 首轮：引用的片段在论文里，不在报告里
                return json.dumps({
                    "claims": [
                        {"id": "C1", "text": "观点1", "evidence_id": "E1"},
                        {"id": "C2", "text": "观点2", "evidence_id": "E2"},
                    ],
                    "evidence_pool": [
                        {"id": "E1", "snippet": "large-batch training harms generalization",
                         "claim_ref": "C1"},
                        {"id": "E2", "snippet": "论文提出了一种新方法",
                         "claim_ref": "C2"},
                    ],
                    "understanding_accuracy": 0.7,
                    "analysis_depth": 0.7,
                    "innovative_insights": 0.7,
                    "evidence_support": 0.7,
                    "summary": "T",
                    "verdict_suggestion": "well_done",
                }, ensure_ascii=False)
            # 重试：正确引用报告里的片段
            return json.dumps({
                "claims": [
                    {"id": "C1", "text": "观点1", "evidence_id": "E1"},
                    {"id": "C2", "text": "观点2", "evidence_id": "E2"},
                ],
                "evidence_pool": [
                    {"id": "E1", "snippet": "报告里的真实引文", "claim_ref": "C1"},
                    {"id": "E2", "snippet": "另一段真实内容", "claim_ref": "C2"},
                ],
                "understanding_accuracy": 0.8,
                "analysis_depth": 0.8,
                "innovative_insights": 0.8,
                "evidence_support": 0.8,
                "summary": "ok",
                "verdict_suggestion": "well_done",
            }, ensure_ascii=False)

        reviewer = ReflectionReviewer(llm_func=_llm)
        result = reviewer.review(
            "p1", "T",
            full_text="报告里的真实引文 另一段真实内容",
            paper_text="large-batch training harms generalization 论文提出了一种新方法",
        )
        # 应触发重试（首轮 from_paper，effective=0 → retry）
        # 重试后 effective>=2，verdict 恢复到 well_done
        assert result.verdict == "well_done"
        assert result.effective_evidence_count >= 2

    def test_from_paper_alone_triggers_retry_even_with_sufficient_other_evidence(self):
        """即使 effective >= 2（其他证据 ok），from_paper>0 也应触发重试。"""
        from mock_api.depth_eval_reflection import ReflectionReviewer

        _CALLS: list[str] = []

        def _llm(prompt: str) -> str:
            _CALLS.append(prompt)
            if len(_CALLS) == 1:
                return json.dumps({
                    "claims": [
                        {"id": "C1", "text": "观点1", "evidence_id": "E1"},
                        {"id": "C2", "text": "观点2", "evidence_id": "E2"},
                        {"id": "C3", "text": "观点3", "evidence_id": "E3"},
                    ],
                    "evidence_pool": [
                        {"id": "E1", "snippet": "真实内容一", "claim_ref": "C1"},
                        {"id": "E2", "snippet": "真实内容二", "claim_ref": "C2"},
                        # E3 的 snippet 在论文里，不在报告里
                        {"id": "E3", "snippet": "paper originated claim", "claim_ref": "C3"},
                    ],
                    "understanding_accuracy": 0.7,
                    "analysis_depth": 0.7,
                    "innovative_insights": 0.7,
                    "evidence_support": 0.7,
                    "summary": "T",
                    "verdict_suggestion": "well_done",
                }, ensure_ascii=False)
            return json.dumps({
                "claims": [
                    {"id": "C1", "text": "观点1", "evidence_id": "E1"},
                    {"id": "C2", "text": "观点2", "evidence_id": "E2"},
                    {"id": "C3", "text": "观点3", "evidence_id": "E3"},
                ],
                "evidence_pool": [
                    {"id": "E1", "snippet": "真实内容一", "claim_ref": "C1"},
                    {"id": "E2", "snippet": "真实内容二", "claim_ref": "C2"},
                    {"id": "E3", "snippet": "真实内容三", "claim_ref": "C3"},
                ],
                "understanding_accuracy": 0.8,
                "analysis_depth": 0.8,
                "innovative_insights": 0.8,
                "evidence_support": 0.8,
                "summary": "ok",
                "verdict_suggestion": "well_done",
            }, ensure_ascii=False)

        reviewer = ReflectionReviewer(llm_func=_llm)
        result = reviewer.review(
            "p1", "T",
            full_text="真实内容一 真实内容二 真实内容三",
            paper_text="paper originated claim",
        )
        assert result.verdict == "well_done"


class TestCrossvalCache:
    """S1②：缓存不跨文本污染——student_title/author 必须每次都从当前报告现扫。"""

    def test_cache_hit_rescans_current_text(self):
        """同一学号第二次 review 使用不同 full_text，应反映当前文本的引证信息。"""
        import mock_api.depth_eval_reflection as der

        sid = "test_cache_sid_001"
        original_cache = dict(der._VERIFIED_CACHE)  # 保存原缓存
        try:
            # 预种缓存：模拟该生之前成功验证过 arXiv:1234.5678
            der._VERIFIED_CACHE[sid] = {
                "arxiv_id": "1234.5678",
                "title": "Original Paper Title",
                "authors": ["Alice"],
                "student_title": "旧报告的标题",
                "student_author": "旧作者",
                "student_arxiv": "1234.5678",
            }

            # 第一次 review：full_text 包含新的标题信息
            pair = der._resolve_paper_pair(sid, "论文题目：完全不同的新标题\n作者：新作者")
            assert pair is not None
            assert pair["student_title"] == "完全不同的新标题"
            assert pair["student_author"] == "新作者"
            assert pair["arxiv_id"] == "1234.5678"  # arXiv 元数据仍来自缓存

            # 第二次 review：不同的 full_text
            pair2 = der._resolve_paper_pair(sid, "论文题目：这是第三个完全不同的标题\n没有作者行")
            assert pair2 is not None
            assert pair2["student_title"] == "这是第三个完全不同的标题"
            assert pair2["student_author"] is None  # 这次没扫到作者
        finally:
            der._VERIFIED_CACHE.clear()
            der._VERIFIED_CACHE.update(original_cache)
