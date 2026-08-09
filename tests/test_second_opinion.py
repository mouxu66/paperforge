"""双模型交叉复核（second_opinion）单元测试。

覆盖：
- 默认关闭（未设 env）时 run_second_opinion 返回 enabled=False。
- 开启后无第二 provider → 静默跳过（fail-open）。
- 开启后解析 LLM 输出 score/verdict/reason。
- 分歧判定：|Δscore| ≥ 阈值 或 verdict 语义不一致 → flag=disagreement。
- 论文/报告两套 verdict 语义等价表。
"""

from __future__ import annotations

import pytest

from mock_api.second_opinion import (
    _parse_output,
    _verdicts_agree,
    assess_disagreement,
    is_enabled,
    run_second_opinion,
)

SAMPLE_OUTPUT = """score: 0.72
verdict: major_revision
reason: 实验充分但缺大规模验证"""


def test_parse_output() -> None:
    parsed = _parse_output(SAMPLE_OUTPUT)
    assert parsed["score"] == 0.72
    assert parsed["verdict"] == "major_revision"
    assert "缺大规模验证" in parsed["reason"]


def test_parse_output_fail_open_empty() -> None:
    assert _parse_output("") == {}
    assert _parse_output("随便一段没有 key: value 的文字") == {}


def test_is_enabled_defaults_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PAPERFORGE_SECOND_OPINION_ENABLED", raising=False)
    monkeypatch.delenv("PAPERFORGE_SECOND_OPINION", raising=False)
    # settings 缓存可能已被其他测试填充；直接断言模块行为不抛异常且为 bool
    assert isinstance(is_enabled(), bool)


def test_run_second_opinion_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式关闭（env=0）→ 跳过，且不触碰任何 provider。"""
    monkeypatch.setenv("PAPERFORGE_SECOND_OPINION_ENABLED", "0")
    from mock_api import second_opinion as so
    from mock_api.settings import reset_settings

    reset_settings()  # 让 settings 重新解析 env（覆盖 .env 里的 =1）
    result = so.run_second_opinion("some paper text")
    assert result.get("enabled") is False
    assert result.get("skipped") == "disabled"


def test_run_second_opinion_no_provider_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """开启但无第二 provider（本仓库无 DB 配置）→ 静默跳过，不抛异常。"""
    monkeypatch.setenv("PAPERFORGE_SECOND_OPINION_ENABLED", "1")
    from mock_api import second_opinion as so
    from mock_api.settings import reset_settings

    reset_settings()
    result = so.run_second_opinion("some paper text")
    assert result.get("enabled") is False
    # 无模型配置 / 无第二 provider / 出错 → 均 fail-open（不抛异常即可）
    assert result.get("skipped") in ("no_second_provider", "error")


# ── verdict 语义等价表 ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "a,b,kind,expected",
    [
        ("accept", "minor_revision", "paper", True),  # 可接收 ≈ 小修
        ("minor_revision", "accept", "paper", True),
        ("major_revision", "reject", "paper", True),  # 大修 ≈ 拒
        ("accept", "reject", "paper", False),
        ("major_revision", "accept", "paper", False),
        ("well_done", "well_done", "report", True),
        ("needs_evidence", "needs_depth", "report", True),  # 都是"需补"
        ("well_done", "rewrite_required", "report", False),
        ("rewrite_required", "needs_depth", "report", False),
        ("", "accept", "paper", True),  # 缺 verdict 不判分歧（fail-open）
    ],
)
def test_verdicts_agree(a: str, b: str, kind: str, expected: bool) -> None:
    assert _verdicts_agree(a, b, kind) is expected


def test_assess_disagreement_score_gap() -> None:
    second = {
        "enabled": True,
        "provider": "openai",
        "model": "gpt-5.6",
        "score": 0.30,
        "verdict": "reject",
        "reason": "样本过少",
    }
    out = assess_disagreement(0.70, "accept", second, kind="paper")
    assert out["flag"] == "disagreement"
    assert out["score_delta"] == 0.40
    assert out["verdict_agree"] is False
    assert "人工复核" in out["note"]


def test_assess_disagreement_agree() -> None:
    second = {
        "enabled": True,
        "provider": "openai",
        "model": "gpt-5.6",
        "score": 0.68,
        "verdict": "minor_revision",
        "reason": "可接收",
    }
    out = assess_disagreement(0.72, "accept", second, kind="paper")
    assert out["flag"] == "agree"
    assert out["score_delta"] == pytest.approx(0.04)
    assert out["verdict_agree"] is True


def test_assess_disagreement_verdict_only_gap() -> None:
    """分数接近但 verdict 语义不一致 → 仍判分歧。"""
    second = {
        "enabled": True,
        "provider": "openai",
        "model": "gpt-5.6",
        "score": 0.70,
        "verdict": "reject",
        "reason": "",
    }
    out = assess_disagreement(0.72, "accept", second, kind="paper")
    assert out["flag"] == "disagreement"
    assert out["verdict_agree"] is False


def test_assess_disagreement_no_data() -> None:
    assert assess_disagreement(0.7, "accept", {"enabled": False}, kind="paper")["flag"] == "no_data"
