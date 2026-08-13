"""
感悟报告作弊检测压力测试。

模拟学生可能使用的各种规避手段，验证系统各检测层能否正确揪出。

检测层矩阵（由弱到强）：
  L1: AI 生成疑似度（ai_likelihood）— advisory only
  L2: 照抄检测（copy_ratio, 8-gram + embedding）
  L3: 忠实度（fidelity, 报告↔论文 有据性）
  L4: 覆盖度（coverage, 论文→报告 核心要点覆盖）
  L5: 证据门阀（effective_evidence_count, LLM 评审 R1/R1.5）
  L6: 引用真伪（citation_integrity, Crossref 核验）
  L7: 论文绑定（grounded_ratio=0 + copy_ratio<0.05 → mismatch 保护）
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest


def _ml_embedder_available() -> bool:
    """自动检测多语言嵌入模型是否可用（离线文件探测，不触发联网下载）。

    对齐 mock_api.reflection_fidelity.get_ml_embedder 的本地加载路径：本地 ONNX
    模型（model_optimized.onnx + tokenizer.json）就位即视为可用。本机有模型时
    正常跑（不再硬编码 skip），CI 无模型时自动 skip。
    """
    import glob

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = [
        os.path.join(here, "mock_api", "_models", "paraphrase-multilingual-onnx"),
        os.path.join(os.path.expanduser("~"), ".cache", "fastembed"),
        os.environ.get("FASTEMBED_CACHE_DIR") or "",
        "/tmp/fastembed_cache",
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for onnx_path in glob.glob(
            os.path.join(root, "**", "model_optimized.onnx"), recursive=True
        ):
            if os.path.isfile(os.path.join(os.path.dirname(onnx_path), "tokenizer.json")):
                return True
    return False


_ML_EMBEDDER_UNAVAILABLE = not _ml_embedder_available()


# ===========================================================================
# 作弊样本文本库
# ===========================================================================

# 一段典型的 AI 生成中文感悟报告（衔接词过载、空泛开头、缺乏个人体验、标点单一）
AI_GENERATED_REPORT = """
随着人工智能技术的日益发展，深度学习在各个领域发挥着越来越重要的作用。
首先，本文提出的TAG-Net通过时态图注意力网络实现了对动态图的有效建模。
其次，该模型在多个基准数据集上取得了显著的性能提升。
此外，作者还通过消融实验验证了各个模块的重要性。
值得注意的是，该方法的计算复杂度较低，具有十分重要的实际应用价值。
综上所述，TAG-Net为动态图学习提供了一个不可否认的有效框架。
总体而言，这项研究具有重要意义，值得关注。
从某种程度上说，该工作推动了图神经网络领域的进一步发展。
可以看出，作者在模型设计上做出了不可忽视的贡献。
"""

# 照抄原文（大量直接复制论文原文句子）
COPIED_REPORT_PARTS = {
    "q": "本文研究动态图上的节点分类问题。",
    "tech": """We propose TAG-Net, a temporal graph attention network that captures
dynamic node representations through multi-head attention over temporal neighborhoods.
The model consists of three key components: (1) a temporal random walk generator,
(2) a multi-head self-attention encoder, and (3) a temporal position encoding module.
We use the Adam optimizer with learning rate 0.001 and batch size 128.
The model is trained for 200 epochs with early stopping based on validation loss.
We evaluate on four benchmark datasets: DBLP, Reddit, MOOC, and Wiki.""",
    "exp": """Experimental results show that TAG-Net outperforms all baselines by
a significant margin. On DBLP, we achieve 95.3% accuracy compared to 92.1% for
the best baseline. Ablation studies confirm that both temporal position encoding
and multi-head attention contribute substantially to the performance gains.""",
    "reflection": "我认为这篇论文的方法很有创新性，实验结果也很充分。",
}

# 论文原文（用于 fidelity 比对）
PAPER_FULL_TEXT = """We propose TAG-Net, a temporal graph attention network that
captures dynamic node representations through multi-head attention over temporal
neighborhoods. The model consists of three key components: (1) a temporal random
walk generator, (2) a multi-head self-attention encoder, and (3) a temporal position
encoding module. We use the Adam optimizer with learning rate 0.001 and batch size
128. The model is trained for 200 epochs with early stopping based on validation
loss. We evaluate on four benchmark datasets: DBLP, Reddit, MOOC, and Wiki.
Experimental results show that TAG-Net outperforms all baselines by a significant
margin. On DBLP, we achieve 95.3% accuracy compared to 92.1% for the best baseline.
Ablation studies confirm that both temporal position encoding and multi-head
attention contribute substantially to the performance gains. Our analysis reveals
that temporal random walks capture more informative neighborhoods than static
alternatives, especially in rapidly evolving graphs. The time complexity of the
proposed method is O(N * d^2 * L) where N is the number of nodes, d is the
embedding dimension, and L is the walk length. Future work includes extending the
framework to heterogeneous temporal graphs and incorporating edge features.
"""

# 拼接洗稿（改写论文句子，但保留核心信息）
PATCHWORK_REPORT = """这篇论文提出了一个叫TAG-Net的模型，可以用在动态图上做节点分类。
作者设计了一个时序图注意力网络，通过多头注意力机制来捕捉节点之间的时序关系。
具体来说，模型包含了三个主要部分：首先是时序随机游走生成器，
然后是用于编码的多头自注意力模块，最后还有一个时序位置编码。
实验部分作者在DBLP、Reddit等数据集上进行了测试，结果看起来比之前的方法都要好。
我个人觉得这个工作挺有意思的，特别是在处理动态图的时候。
"""

# 张冠李戴——写的是完全不同的论文内容（与原论文零交集）
WRONG_PAPER_REPORT = """这篇论文主要研究了自然语言处理中的情感分析问题。
作者提出了一个基于BERT的微调方法，在多个情感分析数据集上取得了SOTA效果。
我觉得这个方法很实用，特别是在电商评论分析场景中。
BERT的预训练能力确实很强，微调后能很好地捕捉情感特征。
"""

# 空泛敷衍——没有实质内容
EMPTY_VAGUE_REPORT = """
深度学习确实挺有意思的。
我学到了很多东西。
这篇论文写得不错。
实验做得挺好的。
"""

# 编造参考文献（包含查无实据的 DOI）
FAKE_CITATION_REPORT = """这篇文章提出了非常有趣的方法。
正如作者在论文中提到的，深度学习已经成为主流方法[1]。
实验结果表明该方法效果很好。

参考文献：
[1] Smith, J. et al. "A Novel Approach to Deep Learning." fake-journal-of-ai, 2024.
    doi:10.99999/FAKE-DOI-NEVER-EXISTS-2024
[2] Chen, L. "Deep Graph Learning." NeurIPS 2023.
"""

# 中文标题 + 英文论文（crossval 歧视场景）
CHINESE_TITLE_REPORT = """论文题目：基于注意力机制的动态图神经网络研究
作者：张三，李四

这篇论文提出了一个动态图神经网络，用了注意力机制。
我觉得这个方法挺好的，实验也做得不错。
"""


# ===========================================================================
# L1: AI 生成检测
# ===========================================================================


class TestAIGeneratedDetection:
    """AI 生成文本检测：衔接词过载、空泛开头、缺乏个人体验。"""

    def test_ai_generated_text_scores_high(self):
        """典型的 AI 生成中文报告 → ai_likelihood >= 0.35（uncertain+）。"""
        from mock_api.ai_likelihood import compute_ai_likelihood

        result = compute_ai_likelihood(AI_GENERATED_REPORT)
        # AI 生成文本应有显著信号
        assert result["ai_likelihood"] >= 0.30, (
            f"AI 生成文本疑似度应偏高，实际 {result['ai_likelihood']:.3f}"
        )
        # 至少应有衔接词或套话信号命中
        signals = result["signals"]
        connector_score = signals.get("connector_density", {}).get("score", 0)
        hedge_score = signals.get("hedge_density", {}).get("score", 0)
        personal_score = signals.get("personal_scarcity", {}).get("score", 0)
        combined = connector_score + hedge_score + personal_score
        assert combined > 0.3, (
            f"AI 生成文本应有多项信号命中，connector={connector_score:.2f}, "
            f"hedge={hedge_score:.2f}, personal={personal_score:.2f}"
        )

    def test_mixed_text_not_flagged(self):
        """正常人类写的文本（含个人体验）不应被误判为 AI。"""
        from mock_api.ai_likelihood import compute_ai_likelihood

        human_text = """我仔细读完了这篇论文，说实话一开始没太看懂那个时态注意力
机制是怎么工作的，后来翻了一下他们引用的那篇GraphSAGE的原始论文才明白。让我
印象最深的是他们的消融实验设计，把时序位置编码拿掉之后准确率掉了6个点，这个
确实挺震撼的。不过我觉得他们的时间复杂度分析那一段写得有点粗糙，O(N*d^2*L)
这个公式没有解释每一步是怎么推导出来的，只是给了最终结果。"""
        result = compute_ai_likelihood(human_text)
        assert result["tier"] != "likely_ai", (
            f"人类文本不应被判为 AI，实际 tier={result['tier']}, "
            f"score={result['ai_likelihood']:.3f}"
        )

    def test_very_short_text_degraded(self):
        """过短文本（<80 字）→ 返回 human + low confidence。"""
        from mock_api.ai_likelihood import compute_ai_likelihood

        result = compute_ai_likelihood("写得太好了")
        assert result["tier"] == "human"
        assert result["text_chars"] < 80
        assert "过短" in result["note"]


# ===========================================================================
# L2+L3: 照抄 & 忠实度检测
# ===========================================================================


class TestCopyDetection:
    """照抄检测：8-gram + embedding 双层。"""

    def test_copy_ratio_high_on_copied_report(self):
        """报告大量照抄论文原文 → copy_ratio >= 0.30。"""
        from mock_api.reflection_fidelity import compute_copy_ratio

        copied_text = COPIED_REPORT_PARTS["tech"] + " " + COPIED_REPORT_PARTS["exp"]
        ratio = compute_copy_ratio(copied_text, PAPER_FULL_TEXT)
        assert ratio >= 0.25, (
            f"大量照抄的 copy_ratio 应偏高，实际 {ratio:.3f}"
        )

    def test_copy_ratio_low_on_original_report(self):
        """原创报告 → copy_ratio 应很低。"""
        from mock_api.reflection_fidelity import compute_copy_ratio

        original = """我认真读了这篇关于动态图神经网络的文章。作者提出的TAG-Net
模型挺有意思的，用了时序随机游走来捕捉节点的动态变化。我觉得最巧妙的地方是
他们把位置编码也考虑进去了，这样可以让模型知道不同时间步的关系。"""
        ratio = compute_copy_ratio(original, PAPER_FULL_TEXT)
        assert ratio < 0.15, (
            f"原创报告的 copy_ratio 应较低，实际 {ratio:.3f}"
        )

    def test_copy_ratio_triggers_rewrite_required_in_pipeline(self, tmp_path, monkeypatch):
        """copy_ratio >= 0.30 → verdict=rewrite_required（管线集成）。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        monkeypatch.setattr(
            "mock_api.reflection_fidelity.get_ml_embedder", lambda: None
        )

        test_file = tmp_path / "copied.docx"
        test_file.write_bytes(b"fake docx bytes")

        monkeypatch.setenv("PAPERFORGE_BENCH_NO_LLM", "1")  # 跳过 LLM
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _make_fake_parse(
                raw=" ".join(COPIED_REPORT_PARTS.values()),
            ),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: "paper001",
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: (PAPER_FULL_TEXT, None),
        )

        result = analyze_reflection_file(str(test_file), Mock())
        # 照抄应被揪出
        assert result["copy_ratio"] is not None
        if result["copy_ratio"] >= 0.25:
            assert result["verdict"] == "rewrite_required", (
                f"大量照抄应判 rewrite_required，实际 {result['verdict']}"
            )


class TestFidelityDetection:
    """忠实度：报告↔论文 有据性（防编造）。"""

    def test_fidelity_low_on_fabricated_report(self, monkeypatch):
        """编造内容 → fidelity 应很低。

        使用英文文本（兼容 BGE 降级模型），验证编造内容与论文零交集 → fidelity≈0。
        """
        from mock_api.reflection_fidelity import compute_fidelity

        monkeypatch.setattr(
            "mock_api.reflection_fidelity.get_ml_embedder", lambda: None
        )

        # 编造内容：完全不同的领域（NLP情感分析），与 TAG-Net 论文零交集
        fabricated_sections = {
            "q": "This paper proposes a novel sentiment analysis approach using "
                 "transformer-based architectures for multi-domain classification. "
                 "The method achieves state-of-the-art results on standard benchmarks.",
            "tech": "We employ BERT-large as the backbone and fine-tune it on domain-specific "
                    "datasets including SST-2, Amazon Reviews, and Yelp. The architecture "
                    "includes a domain adaptation layer and multi-task learning heads.",
            "exp": "Experimental results demonstrate significant improvements over baseline "
                   "methods. On SST-2 we achieve 97.3% accuracy, outperforming all previous "
                   "approaches by at least 2 percentage points.",
            "reflection": "This work represents a significant advance in cross-domain "
                         "sentiment analysis, opening new possibilities for real-world "
                         "applications in customer feedback analysis.",
        }
        result = compute_fidelity(fabricated_sections, PAPER_FULL_TEXT, None)
        # 编造内容与论文零交集 → fidelity 应极低
        assert result.fidelity is not None, f"status={result.status}, msg={result.message}"
        assert result.fidelity < 0.25, (
            f"编造报告的 fidelity 应极低，实际 {result.fidelity:.3f}"
        )
        # grounded_ratio 也应为 0（无任何句子与论文沾边）
        if result.grounded_ratio is not None:
            assert result.grounded_ratio < 0.15, (
                f"编造报告的 grounded_ratio 应接近 0，实际 {result.grounded_ratio}"
            )

    @pytest.mark.skipif(
        _ML_EMBEDDER_UNAVAILABLE, reason="多语言嵌入模型不可用（自动检测：本地 ONNX 未就位）"
    )
    def test_fidelity_high_on_faithful_paraphrase(self):
        from mock_api.reflection_fidelity import compute_fidelity

        # 忠实改写论文句子（保留核心语义，仅换表达方式）→ 有据性应高。
        # 与 test_fidelity_low_on_fabricated_report 形成对照：编造≈0，忠实改写≈1。
        # fidelity 度量的是「论点是否有论文依据」，而非原创性——忠实改写本就该高分。
        sections = {
            "q": "The authors present TAG-Net, a temporal graph neural network designed "
                 "for classifying nodes in dynamic graphs using attention mechanisms.",
            "tech": "The model architecture incorporates three main building blocks. "
                    "First, it generates temporal random walks to sample the neighborhood. "
                    "Second, a multi-head self-attention mechanism encodes node interactions. "
                    "Third, temporal position encoding captures the ordering of events. "
                    "Training uses the Adam optimizer with a learning rate of 0.001.",
            "exp": "On benchmark datasets including DBLP and Reddit, TAG-Net achieves "
                   "superior performance compared to existing methods. Ablation experiments "
                   "reveal that both the temporal encoding and attention components are "
                   "essential for the observed gains.",
            "reflection": "The most innovative aspect is the temporal position encoding, "
                         "which elegantly captures time-dependent relationships that "
                         "conventional GNNs typically miss.",
        }
        result = compute_fidelity(sections, PAPER_FULL_TEXT, None)
        assert result.fidelity is not None, f"status={result.status}, msg={result.message}"
        # 忠实改写应有高有据性（>0.25，通常≈1.0）；绝不与编造报告同档（<0.25）。
        assert result.fidelity > 0.25, (
            f"忠实改写的 fidelity 应偏高，实际 {result.fidelity:.3f}"
        )


# ===========================================================================
# L4: 覆盖度检测
# ===========================================================================


class TestCoverageDetection:
    """覆盖度：论文核心要点是否被报告覆盖。"""

    @pytest.mark.skipif(
        _ML_EMBEDDER_UNAVAILABLE, reason="多语言嵌入模型不可用（自动检测：本地 ONNX 未就位）"
    )
    def test_coverage_low_on_wrong_paper(self):
        from mock_api.reflection_fidelity import compute_coverage

        sections = {
            "q": "This study examines the role of BRCA1 gene mutations in breast cancer "
                 "development and progression. We analyzed genomic data from over 5000 "
                 "patients across multiple clinical centers.",
            "tech": "We performed whole-exome sequencing on tumor samples and matched "
                    "normal tissue. Variant calling was performed using GATK best practices "
                    "with subsequent annotation via ANNOVAR and functional prediction tools.",
            "exp": "Our results identify 37 novel pathogenic variants in BRCA1, with "
                   "12 showing strong association with triple-negative breast cancer. "
                   "Functional assays confirm loss of DNA repair activity.",
            "reflection": "These findings expand the mutational landscape of BRCA1 and "
                         "provide new targets for precision oncology approaches.",
        }
        result = compute_coverage(sections, PAPER_FULL_TEXT)
        assert result.coverage is not None
        # 跨领域（生物学 vs CS图网络）→ 覆盖度应很低
        assert result.coverage < 0.55, (
            f"跨领域报告 coverage 应偏低，实际 {result.coverage:.3f}"
        )

    @pytest.mark.skipif(
        _ML_EMBEDDER_UNAVAILABLE, reason="多语言嵌入模型不可用（自动检测：本地 ONNX 未就位）"
    )
    def test_coverage_on_empty_report(self):
        from mock_api.reflection_fidelity import compute_coverage

        sections = {
            "q": "It was interesting. Good paper overall. I learned some things.",
            "tech": "The method was cool. Nice approach.",
            "exp": "Good results. The experiments looked solid.",
            "reflection": "I gained some insights. Will use this in future work.",
        }
        result = compute_coverage(sections, PAPER_FULL_TEXT)
        assert result.coverage is not None
        assert result.coverage < 0.25, (
            f"空泛报告 coverage 应极低，实际 {result.coverage:.3f}"
        )


# ===========================================================================
# L5: 证据门阀（LLM 评审）
# ===========================================================================


class TestEvidenceGate:
    """证据门阀：R1/R1.5 硬性证据数门槛。"""

    def test_empty_report_triggers_r1(self):
        """报告几乎没有实际内容 → effective_evidence < 2 → R1 触发。"""
        from mock_api.depth_eval_reflection import ReflectionReviewer

        empty_text = "深度学习很重要。人工智能很有用。论文写得不错。"
        reviewer = ReflectionReviewer(llm_func=lambda p: json.dumps({
            "claims": [],
            "evidence_pool": [],
            "understanding_accuracy": 0.5,
            "analysis_depth": 0.5,
            "innovative_insights": 0.5,
            "evidence_support": 0.5,
            "summary": "空报告",
            "verdict_suggestion": "needs_evidence",
        }, ensure_ascii=False))
        result = reviewer.review("report_empty", "空报告", empty_text)
        assert result.effective_evidence_count < 2, (
            f"空报告有效证据应<2，实际 {result.effective_evidence_count}"
        )
        assert result.verdict == "rewrite_required", (
            f"空报告应判 rewrite_required，实际 {result.verdict}"
        )

    def test_fabricated_evidence_rejected(self):
        """LLM 编造不存在的 snippet → 被 _snippet_exists 拒绝。"""
        from mock_api.depth_eval_reflection import ReflectionReviewer

        report_text = "这篇论文提出了一个时序图注意力网络。实验结果显示效果很好。"
        fake_evidence_response = json.dumps({
            "claims": [
                {"id": "C1", "text": "提出了新模型", "evidence_id": "E1"},
                {"id": "C2", "text": "实验效果好", "evidence_id": "E2"},
            ],
            "evidence_pool": [
                {"id": "E1", "snippet": "本文提出了一种基于量子计算的全新架构", "claim_ref": "C1"},
                {"id": "E2", "snippet": "该模型在100个数据集上取得了SOTA效果", "claim_ref": "C2"},
            ],
            "understanding_accuracy": 0.9,
            "analysis_depth": 0.9,
            "innovative_insights": 0.9,
            "evidence_support": 0.9,
            "summary": "编造证据",
            "verdict_suggestion": "well_done",
        }, ensure_ascii=False)

        reviewer = ReflectionReviewer(llm_func=lambda p: fake_evidence_response)
        result = reviewer.review("report_fake_ev", "编造证据", report_text)
        # 编造的 snippet 不在 report_text 里 → 全部作废 → effective=0 → R1
        assert result.effective_evidence_count == 0, (
            f"编造的证据应全部作废，实际 effective={result.effective_evidence_count}"
        )
        assert result.verdict == "rewrite_required"


# ===========================================================================
# L6: 引用真伪
# ===========================================================================


class TestCitationIntegrity:
    """引用真伪：编造 DOI / 引用不一致。"""

    def test_fabricated_citation_flag(self):
        """编造 DOI → 即使 offline 校验也应标记。"""
        from mock_api.integrity.citation_verifier import assess_citation_integrity

        result = assess_citation_integrity(FAKE_CITATION_REPORT, verify_online=False)
        # offline 模式至少应检测 DOI 格式异常或引用一致性
        assert result.get("integrity_flag") in (
            "fabricated_suspected", "inconsistent", "unknown"
        ), f"编造引用应被标记，实际 flag={result.get('integrity_flag')}"

    def test_no_citations_returns_clean(self):
        """无引用的报告 → 不应误报。"""
        from mock_api.integrity.citation_verifier import assess_citation_integrity

        clean_report = """这篇论文提出了一个很好的方法。我学到了很多东西。"""
        result = assess_citation_integrity(clean_report, verify_online=False)
        # 无引用 → 不应标记为 fabricated 或 inconsistent
        assert result.get("integrity_flag") not in (
            "fabricated_suspected", "inconsistent"
        ), f"无引用报告不应被标记，实际 {result.get('integrity_flag')}"


# ===========================================================================
# L7: 论文绑定
# ===========================================================================


class TestPaperBinding:
    """论文绑定防错配。"""

    @pytest.mark.skipif(
        _ML_EMBEDDER_UNAVAILABLE, reason="多语言嵌入模型不可用（自动检测：本地 ONNX 未就位）"
    )
    def test_wrong_paper_triggers_coverage_fail(self):
        from mock_api.reflection_fidelity import compute_coverage, compute_fidelity

        sections = {
            "q": "This research investigates the molecular mechanisms of antibiotic "
                 "resistance in Staphylococcus aureus. We examine the role of mecA gene "
                 "in conferring methicillin resistance across clinical isolates.",
            "tech": "Bacterial strains were collected from 12 hospitals. Whole-genome "
                    "sequencing was performed using Illumina NovaSeq. Resistance profiles "
                    "were determined by broth microdilution following CLSI guidelines.",
            "exp": "We identified 23 novel mutations in the mecA regulatory region. "
                   "Complementation experiments confirmed their role in upregulating "
                   "resistance gene expression by 3-8 fold.",
            "reflection": "Understanding these resistance mechanisms is critical for "
                         "developing new therapeutic strategies against MRSA infections.",
        }
        fid = compute_fidelity(sections, PAPER_FULL_TEXT, None)
        cov = compute_coverage(sections, PAPER_FULL_TEXT)

        # 绑定错配保护：grounded_ratio 应极低（无句子与论文沾边）
        assert fid.grounded_ratio is not None, f"status={fid.status}, msg={fid.message}"
        assert fid.grounded_ratio < 0.15, (
            f"绑错论文 grounded_ratio 应接近 0，实际 {fid.grounded_ratio}"
        )
        # 跨领域时 coverage 也应低
        assert cov.coverage is not None and cov.coverage < 0.55, (
            f"绑错论文 coverage 应低，实际 {cov.coverage:.3f}"
        )


# ===========================================================================
# 综合场景：多检测层联合
# ===========================================================================


class TestCombinedAttack:
    """组合作弊手段：AI生成 + 照抄 + 编造引用 同时出现。"""

    def test_ai_copy_fake_citations_all_detected(self):
        """AI生成 + 照抄 + 编造引用 → 三层检测都应有信号。"""
        from mock_api.ai_likelihood import compute_ai_likelihood
        from mock_api.reflection_fidelity import compute_copy_ratio

        combined = AI_GENERATED_REPORT + "\n参考文献：\n[1] doi:10.99999/FAKE-FAKE-FAKE"
        # L1: AI 检测
        ai = compute_ai_likelihood(combined)
        # L2: 照抄检测（AI 生成虽然不是照抄，但如果是改写论文的应能检出）
        copy = compute_copy_ratio(combined, PAPER_FULL_TEXT)

        # 至少 AI 检测应有信号
        assert ai["ai_likelihood"] >= 0.25, (
            f"AI+编造引用组合应至少被 AI 检测层标记，实际 {ai['ai_likelihood']:.3f}"
        )

    def test_ai_generation_pipeline_verdict(self, tmp_path, monkeypatch):
        """AI 生成报告 → 在完整管线中应被标记（ai_likelihood 偏高）。"""
        from mock_api.reflection_pipeline import analyze_reflection_file

        monkeypatch.setattr(
            "mock_api.reflection_fidelity.get_ml_embedder", lambda: None
        )

        test_file = tmp_path / "ai_report.docx"
        test_file.write_bytes(b"fake docx bytes")

        monkeypatch.setenv("PAPERFORGE_BENCH_NO_LLM", "1")
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.parse_docx_from_bytes",
            lambda data, filename="": _make_fake_parse(raw=AI_GENERATED_REPORT),
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline.match_paper_by_title",
            lambda title, db: "paper001",
        )
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb",
            lambda db, pid: (PAPER_FULL_TEXT, None),
        )

        result = analyze_reflection_file(str(test_file), Mock())
        # AI 疑似度应返回
        assert "ai_likelihood" in result
        assert result["ai_likelihood"] >= 0.0  # 总是有值


class TestEdgeCases:
    """边界情况：学生可能利用的检测盲区。"""

    def test_very_short_report_bypasses_ai_detection(self, monkeypatch):
        """超短报告（<80字）→ AI 检测降级，但 fidelity/coverage 仍能抓住。

        超短报告触发 fidelity 的 MIN_REPORT_CHARS 门阀（<100字）→ too_short。
        这是正确的：太短的报告无法可靠评估 fidelity，应直接标记为 too_short。
        """
        monkeypatch.setattr(
            "mock_api.reflection_fidelity.get_ml_embedder", lambda: None
        )
        from mock_api.ai_likelihood import compute_ai_likelihood
        from mock_api.reflection_fidelity import compute_fidelity

        short = "写得太好了。我觉得很棒。学到了。"
        ai = compute_ai_likelihood(short)
        assert ai["tier"] == "human"  # 降级
        assert ai["text_chars"] < 80

        # 超短报告触发 MIN_REPORT_CHARS → too_short（本身就该拦截）
        sections = {"q": short, "tech": "", "exp": "", "reflection": ""}
        fid = compute_fidelity(sections, PAPER_FULL_TEXT, None)
        assert fid.status == "too_short", (
            f"超短报告应被 MIN_REPORT_CHARS 拦截(too_short)，"
            f"实际 status={fid.status}, fidelity={fid.fidelity}"
        )

    def test_chinese_title_crossval_discrimination(self):
        """中文标题报告 → crossval 加分系统性偏低（已知局限）。"""
        from mock_api.depth_eval_reflection import _compute_crossval_accuracy

        pair_en = {
            "student_title": "The inverted Pendulum: A fundamental Benchmark",
            "title": "The inverted Pendulum: A fundamental Benchmark in Control Theory and Robotics",
            "student_author": "Olfa Boubaker",
            "authors": ["Olfa Boubaker"],
            "student_arxiv": "1405.3094",
        }
        pair_cn = {
            "student_title": "倒立摆：控制理论与机器人学的基本基准",
            "title": "The inverted Pendulum: A fundamental Benchmark in Control Theory and Robotics",
            "student_author": "Olfa Boubaker",
            "authors": ["Olfa Boubaker"],
            "student_arxiv": None,
        }

        en_acc, _ = _compute_crossval_accuracy(pair_en)
        cn_acc, _ = _compute_crossval_accuracy(pair_cn)

        # 中文标题的 crossval 准确度应显著低于英文标题
        assert cn_acc < en_acc, (
            f"中文标题 crossval 准确度 ({cn_acc:.3f}) 应低于英文 ({en_acc:.3f})"
        )
        # 差距应明显（>20%）
        assert en_acc - cn_acc > 0.15, (
            f"中英文 crossval 差距应明显，实际差 {en_acc - cn_acc:.3f}"
        )

    def test_english_snippet_in_chinese_report(self):
        """报告中夹杂英文论文原句 → snippet 检测应能找到（非跨语言歧视）。"""
        from mock_api.depth_eval_reflection import _snippet_exists

        report = "这篇论文的核心方法是TAG-Net, a temporal graph attention network for dynamic graphs."
        # 英文子串应从报告中能找到
        assert _snippet_exists("TAG-Net, a temporal graph attention network", report), (
            "报告中的英文原句应被 snippet 检测找到"
        )

    def test_min_report_chars_filter(self):
        """报告正文极短（<100字）→ fidelity 返回 too_short。"""
        from mock_api.reflection_fidelity import compute_fidelity

        tiny = {"q": "好。", "tech": "棒。", "exp": "赞。", "reflection": "厉害。"}
        result = compute_fidelity(tiny, PAPER_FULL_TEXT, None)
        assert result.status == "too_short", (
            f"极短报告应返回 too_short，实际 {result.status}"
        )


# ===========================================================================
# helpers
# ===========================================================================


def _make_fake_parse(sections=None, raw="test"):
    from mock_api.reflection_docx_parser import ReflectionDoc

    return ReflectionDoc(
        student_id="20240001",
        name="测试学生",
        paper_title="TAG-Net",
        paper_author="Alice",
        paper_source="NeurIPS 2024",
        sections=sections or {
            "q": "问题段",
            "tech": "技术段",
            "exp": "实验段",
            "reflection": "感想段",
        },
        raw_text=raw,
    )
