"""云端兜底（本地主评审解析失败 → 云端第二评审直接评分）单元测试。

仅测 depth_tasks._try_cloud_rescue_reflection 的落地逻辑（不触网）：
- 云端成功 rescued → 返回带 rescued_by_cloud 标记的最小 reflection_result。
- 云端未 rescued → 返回 None（交回调用方原失败逻辑）。
- second_opinion 开关关闭 → 返回 None（fail-open）。
"""

from __future__ import annotations

import mock_api.second_opinion as so
from mock_api import depth_tasks


class _FakePaper:
    full_text = "这是一份反思报告正文……用于云端第二评审的输入。"


def test_rescue_helper_returns_rescued(monkeypatch) -> None:
    monkeypatch.setattr(so, "is_enabled", lambda: True)
    monkeypatch.setattr(
        so,
        "run_second_opinion",
        lambda *a, **k: {
            "rescued": True,
            "resolved_score": 0.6,
            "resolved_verdict": "needs_depth",
            "score": 0.6,
            "verdict": "needs_depth",
            "provider": "glm",
        },
    )
    out = depth_tasks._try_cloud_rescue_reflection(_FakePaper(), db=None)
    assert out is not None
    assert out["rescued_by_cloud"] is True
    assert out["local_parse_failed"] is True
    assert out["verdict"] == "needs_depth"
    assert out["average"] == 0.6
    assert out["analysis_v2"]["average"] == 0.6
    assert "cross_check" in out
    assert "rescue_note" in out


def test_rescue_helper_none_when_no_rescue(monkeypatch) -> None:
    monkeypatch.setattr(so, "is_enabled", lambda: True)
    monkeypatch.setattr(
        so, "run_second_opinion",
        lambda *a, **k: {"enabled": False, "skipped": "no_second_provider"},
    )
    assert depth_tasks._try_cloud_rescue_reflection(_FakePaper(), db=None) is None


def test_rescue_helper_none_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(so, "is_enabled", lambda: False)
    assert depth_tasks._try_cloud_rescue_reflection(_FakePaper(), db=None) is None
