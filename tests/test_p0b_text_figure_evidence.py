"""P0-B: 纯文本层图文一致性抽取（确定性、无 LLM / 无渲染 / 无 DB）。

注意：_score_text_figure_consistency 已于 v4.2 退役（PeerRead 实测无判别力），
完整源码已归档到 mock_api/scorers/legacy.py，不再被生产路径调用。
本测试保留 extract_text_figure_evidence 的图注解析测试，涉及旧评分函数的
测试已改为验证退役行为。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mock_api.pdf_parser import extract_text_figure_evidence
import pytest
from mock_api.scorers.legacy import _score_text_figure_consistency


def test_extracts_caption_with_numbers():
    text = (
        "Abstract: intro.\n\n"
        "Figure 1: Overall architecture. We report accuracy 89.7% on ImageNet.\n\n"
        "2 Method\nWe reproduce the 89.7% top-1 accuracy in Figure 1 across seeds.\n"
    )
    ev = extract_text_figure_evidence(text)
    assert len(ev) == 1
    assert ev[0]["figure_number"] == 1
    vals = [x["value"] for x in ev[0]["caption_numbers"]]
    assert "89.7%" in vals
    # 89.7% 在正文其余部分出现 → 命中
    m = [x for x in ev[0]["body_matches"] if x["value"] == "89.7%"][0]
    assert m["found"] is True


def test_multiline_caption_not_falsely_matched():
    # 图注跨两行；数值仅出现在图注，正文无 → 应判为未命中（不误判为一致）
    text = (
        "Figure 2: Ablation of the attention mask.\n"
        "The relative gain is 12.3 points, exceeding 8.0 point baseline.\n\n"
        "3 Conclusion\nWe conclude.\n"
    )
    ev = extract_text_figure_evidence(text)
    assert len(ev) == 1
    nums = {x["value"]: x["found"] for x in ev[0]["body_matches"]}
    assert nums.get("12.3") is False
    assert nums.get("8.0") is False


def test_section_heading_not_treated_as_caption():
    # "Figure 2.\n3.3 Position-wise Feed-Forward Networks" 是章节标题，不是图注
    text = (
        "Figure 2.\n3.3 Position-wise Feed-Forward Networks\n\n"
        "Some body text without numbers in a real caption.\n"
    )
    ev = extract_text_figure_evidence(text)
    # 图注过短（仅 "Figure 2."）或无数值 → 不计入
    assert ev == []


def test_metric_keyword_captured():
    text = "Figure 3: Comparison. The F1 score reaches 0.852 while baseline gets 0.79.\n\n4 Conclusion\n"
    ev = extract_text_figure_evidence(text)
    assert len(ev) == 1
    by_val = {x["value"]: x["metric"] for x in ev[0]["caption_numbers"]}
    assert by_val.get("0.852") == "f1"


def test_no_caption_returns_empty_and_neutral_score():
    """无图注时 ev 为空，评分函数返回中性 0.5。"""
    text = "A plain paper with no figures and no numbers of interest at all."
    ev = extract_text_figure_evidence(text)
    assert ev == []
    score, reasoning, flags = _score_text_figure_consistency(ev)
    assert score == 0.5


def test_score_non_neutral_when_evidence_present():
    """有图注且正文命中时评分 > 0.5。"""
    text = (
        "Figure 1: model. accuracy 90.0% on test.\n\n"
        "2 Method\nWe achieve 90.0% accuracy on the test set as shown.\n"
    )
    ev = extract_text_figure_evidence(text)
    score, reasoning, flags = _score_text_figure_consistency(ev)
    assert score > 0.5


def test_partial_mismatch_scores_neutral_or_below():
    """仅部分数字在正文命中 → 评分 ≤ 0.5。"""
    text = (
        "Figure 1: model. accuracy 90.0% on test and 12.5% on hard and 7.2% on easy.\n\n"
        "2 Method\nWe achieve 90.0% accuracy on the test set.\n"
        # 12.5% 和 7.2% 仅出现在图注，正文无
    )
    ev = extract_text_figure_evidence(text)
    score, reasoning, flags = _score_text_figure_consistency(ev)
    assert score < 0.5
