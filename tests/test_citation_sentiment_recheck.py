"""被引情感云端复核（_cloud_recheck_citation_sentiment）单元测试。

覆盖：
- 云端与本地分歧 → 以云端标签覆盖本地，meta.adopted=True
- 云端与本地一致 → 不覆盖，meta.adopted=False，采用云端置信度
- 无第二 provider（find_second_provider 返回 None）→ 沿用本地，meta=None
- 开关关闭（second_opinion_enabled / sentiment_recheck_enabled=False）→ 沿用本地，meta=None

全程用 monkeypatch 替换云端 provider 与 settings，不发起任何真实 API 调用。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import mock_api.crud.analysis as analysis


class _StubSettings:
    second_opinion_enabled = True
    sentiment_recheck_enabled = True
    sentiment_recheck_low_conf = 0.6


class _FakeResp:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeProvider:
    def __init__(self, content: str) -> None:
        self._content = content

    def chat(self, messages, temperature=0.0, max_tokens=128):  # noqa: ANN001
        return _FakeResp(self._content)


def _patch(monkeypatch, *, content: str, enabled: bool = True) -> None:
    monkeypatch.setattr(analysis, "get_settings", lambda: _StubSettings())
    if enabled:
        monkeypatch.setattr(
            "mock_api.second_opinion.find_second_provider",
            lambda db: _FakeProvider(content),
        )
    else:
        monkeypatch.setattr(
            "mock_api.second_opinion.find_second_provider", lambda db: None
        )
    monkeypatch.setattr("mock_api.second_opinion.is_enabled", lambda: True)


def test_recheck_overrides_on_disagreement(monkeypatch) -> None:
    _patch(monkeypatch, content='{"intent": "support", "confidence": 0.9}')
    label, conf, meta = analysis._cloud_recheck_citation_sentiment(
        "background", 0.3, "ctx", "Cited", "Citing", db=None
    )
    assert label == "support"
    assert conf == 0.9
    assert meta is not None
    assert meta["adopted"] is True
    assert meta["local_label"] == "background"
    assert meta["cloud_label"] == "support"


def test_recheck_no_override_when_agree(monkeypatch) -> None:
    _patch(monkeypatch, content='{"intent": "background", "confidence": 0.8}')
    label, conf, meta = analysis._cloud_recheck_citation_sentiment(
        "background", 0.3, "ctx", "Cited", "Citing", db=None
    )
    assert label == "background"
    assert conf == 0.8
    assert meta is not None
    assert meta["adopted"] is False


def test_recheck_fallback_when_no_provider(monkeypatch) -> None:
    _patch(monkeypatch, content="", enabled=False)
    label, conf, meta = analysis._cloud_recheck_citation_sentiment(
        "criticize", 0.2, "ctx", "Cited", "Citing", db=None
    )
    assert label == "criticize"
    assert conf == 0.2
    assert meta is None


def test_recheck_fallback_when_disabled(monkeypatch) -> None:
    stub = _StubSettings()
    stub.second_opinion_enabled = False
    monkeypatch.setattr(analysis, "get_settings", lambda: stub)
    monkeypatch.setattr(
        "mock_api.second_opinion.find_second_provider",
        lambda db: _FakeProvider('{"intent": "support", "confidence": 0.9}'),
    )
    monkeypatch.setattr("mock_api.second_opinion.is_enabled", lambda: True)
    label, conf, meta = analysis._cloud_recheck_citation_sentiment(
        "background", 0.3, "ctx", "Cited", "Citing", db=None
    )
    assert label == "background"
    assert conf == 0.3
    assert meta is None
