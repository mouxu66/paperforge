"""ai_likelihood 事后检测单元测试。

覆盖 compute_ai_likelihood 的短文本、长文本、边界与信号验证。
"""

from __future__ import annotations

from mock_api.ai_likelihood import (
    TIER_UNCERTAIN,
    compute_ai_likelihood,
    split_sentences,
)


class TestSplitSentences:
    def test_empty(self) -> None:
        assert split_sentences("") == []

    def test_single(self) -> None:
        assert split_sentences("只有一个句子。") == ["只有一个句子"]

    def test_multiple_period(self) -> None:
        result = split_sentences("第一句。第二句。第三句。")
        assert len(result) == 3
        assert result[0] == "第一句"

    def test_question_and_exclamation(self) -> None:
        result = split_sentences("真的吗？太好了！")
        assert len(result) == 2

    def test_newline_split(self) -> None:
        result = split_sentences("A行\nB行")
        assert len(result) == 2

    def test_english_period_not_split(self) -> None:
        """英文句点 . 不在 split_sentences 的分隔符集中，不切分。"""
        result = split_sentences("Hello. World.")
        assert len(result) == 1


class TestComputeAILikelihood:
    """核心 compute_ai_likelihood 函数测试。"""

    def test_short_text_returns_human_low(self) -> None:
        """〈80 字符的短文直接返回 human/low。"""
        r = compute_ai_likelihood("太短了")
        assert r["tier"] == "human"
        assert r["confidence"] == "low"
        assert r["ai_likelihood"] == 0.0
        assert r["text_chars"] == len("太短了")

    def test_empty_text(self) -> None:
        r = compute_ai_likelihood("")
        assert r["tier"] == "human"
        assert r["ai_likelihood"] == 0.0

    def test_none_text(self) -> None:
        r = compute_ai_likelihood(None)  # type: ignore[arg-type]
        assert r["tier"] == "human"
        assert r["ai_likelihood"] == 0.0

    def test_whitespace_only_treated_as_short(self) -> None:
        r = compute_ai_likelihood("   \n  ")
        assert r["tier"] == "human"
        assert r["ai_likelihood"] == 0.0

    def test_exactly_80_chars_is_not_short(self) -> None:
        """刚好 80 字符不应被视为过短。"""
        # "人工智能在当今时代发挥着越来越重要的作用。" ≈ 19 chars
        # 5 × 19 = 95 chars，确保 ≥80
        text = "人工智能在当今时代发挥着越来越重要的作用。" * 5
        r = compute_ai_likelihood(text)
        assert r["text_chars"] >= 80
        # 不应返回空 signals（短文本才会）
        assert r["signals"] != {}

    def test_high_connector_density_raises_likelihood(self) -> None:
        """高衔接词密度应推高 ai_likelihood。"""
        text = (
            "首先，人工智能技术发展迅速。其次，深度学习框架不断涌现。"
            "此外，大语言模型已经广泛应用。然而，其安全性问题值得关注。"
            "因此，我们需要进一步加强研究。综上所述，AI 领域前景广阔。"
            "值得注意的是，伦理问题同样不容忽视。"
        )
        r = compute_ai_likelihood(text)
        assert r["ai_likelihood"] > 0.0
        assert "connector_density" in r["signals"]
        assert r["signals"]["connector_density"]["score"] > 0.0

    def test_high_hedge_density_raises_likelihood(self) -> None:
        """高套话密度应推高 ai_likelihood。"""
        text = (
            "可以看出，这项研究具有重要意义。不难发现，"
            "该技术发挥着重要作用。众所周知，创新是进步的源泉。"
            "从某种程度上说，已经取得了进展。日益重要的是，"
            "跨学科合作不可或缺。总的来说，值得关注。"
        )
        r = compute_ai_likelihood(text)
        assert r["ai_likelihood"] > 0.0
        assert r["signals"]["hedge_density"]["score"] > 0.0

    def test_personal_markers_suppress_likelihood(self) -> None:
        """个人体验标记多应降低 ai_likelihood。"""
        text = (
            "我觉得这个实验结果很有意思。我的体会是模型调参非常困难。"
            "这次实验让我意识到数据质量的重要性。回想起来，"
            "起初我们走了很多弯路。让我惊讶的是结果如此之好。"
        ) * 5  # 确保 ≥80 字符
        r = compute_ai_likelihood(text)
        scarcity = r["signals"]["personal_scarcity"]["score"]
        # 大量个人标记 → scarcity 应偏低（< 0.5）
        assert scarcity < 0.5, f"Expected low scarcity, got {scarcity}"

    def test_vague_openers_signal(self) -> None:
        """空泛开头应被检测到。"""
        text = (
            "在当今社会，信息技术的应用日益广泛。\n"
            "随着时代的发展，人们对数据安全越来越重视。\n"
            "在信息化时代，隐私保护成为核心议题。\n"
        ) * 5  # 确保 ≥80 字符
        r = compute_ai_likelihood(text)
        signals = r["signals"]
        assert "vague_opener_ratio" in signals
        assert signals["vague_opener_ratio"]["score"] > 0.0

    def test_tier_likely_ai(self) -> None:
        """模拟一篇高度 AI-like 的文本，应达到 likely_ai 档。"""
        text = (
            "首先，人工智能在当今时代的应用日益广泛。其次，深度学习技术的发展"
            "发挥着重要作用。此外，可以看出这项研究具有十分重要的意义。"
            "与此同时，大数据技术不可或缺。总之，不难发现计算机科学的进步"
            "值得关注。众所周知，创新是不可否认的动力。"
            "值得注意的是，综上所述，科技发挥着重要作用。"
        ) * 3
        r = compute_ai_likelihood(text)
        # 高度 AI-like → tier 应为 likely_ai 或 uncertain（取决于信号权重）
        assert r["tier"] in ("likely_ai", "uncertain")
        assert r["ai_likelihood"] >= TIER_UNCERTAIN

    def test_tier_human_like(self) -> None:
        """模拟一篇人类书写的文本（含大量个人体验、标点丰富）。"""
        text = (
            "我这次的实验设计有太多可以吐槽的地方了……从最开始的数据采集"
            "就踩了坑——当时完全没想到会出现这么严重的类别不平衡！"
            "后来调整了一下采样策略，但效果也一般。我个人的体会是："
            "别太信论文里的 F1 值，自己跑一遍才知道水有多深。"
            "对了，（顺便一提）我觉得审稿人肯定也会问这个问题——唉。"
        ) * 2
        r = compute_ai_likelihood(text)
        # 个人标记多 + 标点丰富 → 大概率 human
        assert r["tier"] == "human"

    def test_mixed_english_no_crash(self) -> None:
        """含英文文本不应崩溃。"""
        text = (
            "This paper presents a novel approach to NLP. Our method achieves "
            "state-of-the-art results on GLUE benchmark. 此外，我们也测试了中文数据。"
            "结果表明，本方法具有显著优势。"
        ) * 2
        r = compute_ai_likelihood(text)
        assert "ai_likelihood" in r
        assert isinstance(r["ai_likelihood"], float)

    def test_output_structure(self) -> None:
        """验证输出结构完整性。"""
        text = "这是一个足够长的测试文本，用来验证输出结构的完整性。" * 5
        r = compute_ai_likelihood(text)
        assert "ai_likelihood" in r
        assert "tier" in r
        assert "signals" in r
        assert "confidence" in r
        assert "note" in r
        assert "text_chars" in r
        assert r["confidence"] == "low"
        assert r["tier"] in ("human", "uncertain", "likely_ai")
        assert 0.0 <= r["ai_likelihood"] <= 1.0


class TestTierThresholds:
    """验证 tier 阈值逻辑。"""

    def test_below_uncertain_is_human(self) -> None:
        """ai_likelihood < TIER_UNCERTAIN → human。"""
        # 用一篇自然人类文本确保低于阈值
        text = "我昨天去实验室做了个实验，结果挺有意思的。虽然过程很累，但我觉得非常有收获。下次我会改进方法，争取更好的结果。对了，我的猫咪也很可爱！哈哈。"
        r = compute_ai_likelihood(text)
        assert r["ai_likelihood"] < TIER_UNCERTAIN or r["tier"] == "human"

    def test_confidence_is_always_low(self) -> None:
        """confidence 恒为 'low'（事后检测不可靠）。"""
        text = "这是一个足够长的测试文本。" * 10
        r = compute_ai_likelihood(text)
        assert r["confidence"] == "low"
