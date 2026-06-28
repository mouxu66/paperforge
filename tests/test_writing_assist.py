"""测试 mock_api/writing_assist.py — 智能续写 / 结构建议 / 全文改写。

覆盖场景：
- continue_writing：流式生成、LLM 不可用抛 LLMError、流式中途异常分类
- suggest_structure：正常 JSON 解析、```json 代码块包裹剥离、JSON 解析失败降级
- rewrite_text：正常改写、代码块包裹剥离、LLM 不可用抛 LLMError

外部依赖（LLM 网关）通过 patch get_factory 注入 MockProvider 模拟。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mock_api.llm import ChatResult, LLMError
from mock_api.writing_assist import (
    continue_writing,
    rewrite_text,
    suggest_structure,
)


# ---------------------------------------------------------------------------
# Mock LLM 工厂与 Provider
# ---------------------------------------------------------------------------
class _MockProvider:
    """模拟 LLM Provider：可配置 chat / chat_stream 返回值与副作用。"""

    def __init__(
        self,
        chat_result: ChatResult | str = "",
        stream_chunks: list[str] | None = None,
        chat_side_effect: Exception | None = None,
        stream_side_effect: Exception | None = None,
    ):
        self._chat_result = chat_result
        self._stream_chunks = stream_chunks or []
        self._chat_side_effect = chat_side_effect
        self._stream_side_effect = stream_side_effect

    def chat(self, messages, temperature=0.3, max_tokens=512):
        if self._chat_side_effect:
            raise self._chat_side_effect
        if isinstance(self._chat_result, ChatResult):
            return self._chat_result
        return ChatResult(
            content=self._chat_result, model="mock-model", provider="mock"
        )

    def chat_stream(self, messages, temperature=0.5, max_tokens=1024):
        if self._stream_side_effect:
            raise self._stream_side_effect
        for chunk in self._stream_chunks:
            yield chunk


def _make_factory(provider: _MockProvider, label: str = "Mock 模型"):
    """构造一个返回给定 provider 的 mock factory。"""
    factory = MagicMock()
    factory.get_provider.return_value = provider
    factory.current_label.return_value = label
    return factory


# ===========================================================================
# continue_writing（智能续写）
# ===========================================================================
@patch("mock_api.writing_assist.get_factory")
def test_continue_writing_streams_chunks(mock_get_factory):
    """正常续写：按顺序 yield 各 token 片段。"""
    provider = _MockProvider(stream_chunks=["第一段", "第二段", "第三段"])
    mock_get_factory.return_value = _make_factory(provider)

    context = {"title": "引言", "content": "已有内容" * 50}
    chunks = list(continue_writing(context, "继续讨论实验"))
    assert chunks == ["第一段", "第二段", "第三段"]


@patch("mock_api.writing_assist.get_factory")
def test_continue_writing_empty_direction(mock_get_factory):
    """续写方向为空：使用默认提示，仍能正常生成。"""
    provider = _MockProvider(stream_chunks=["续写内容"])
    mock_get_factory.return_value = _make_factory(provider)

    chunks = list(continue_writing({"title": "方法", "content": ""}, ""))
    assert chunks == ["续写内容"]


@patch("mock_api.writing_assist.get_factory")
def test_continue_writing_truncates_long_context(mock_get_factory):
    """超长内容：仅取末尾 1000 字符构造 prompt（不报错）。"""
    provider = _MockProvider(stream_chunks=["ok"])
    mock_get_factory.return_value = _make_factory(provider)

    long_content = "字" * 2000
    chunks = list(continue_writing({"title": "T", "content": long_content}, "x"))
    assert chunks == ["ok"]


@patch("mock_api.writing_assist.get_factory")
def test_continue_writing_provider_unavailable_raises_llm_error(mock_get_factory):
    """LLM 未配置：get_provider 抛异常 → 转为 LLMError。"""
    factory = MagicMock()
    factory.get_provider.side_effect = RuntimeError("no model configured")
    factory.current_label.return_value = "无"
    mock_get_factory.return_value = factory

    with pytest.raises(LLMError):
        list(continue_writing({"title": "T", "content": ""}, "dir"))


@patch("mock_api.writing_assist.get_factory")
def test_continue_writing_stream_midway_error_raises_llm_error(mock_get_factory):
    """流式中途异常 → 转为 LLMError（非原始异常）。"""
    provider = _MockProvider(stream_side_effect=ConnectionError("network down"))
    mock_get_factory.return_value = _make_factory(provider)

    with pytest.raises(LLMError):
        list(continue_writing({"title": "T", "content": ""}, "dir"))


# ===========================================================================
# suggest_structure（结构建议）
# ===========================================================================
@patch("mock_api.writing_assist.get_factory")
def test_suggest_structure_parses_clean_json(mock_get_factory):
    """正常 JSON 返回：解析为结构化建议。"""
    raw = (
        '{"suggestion": "建议移到实验设计章节", '
        '"target_chapter": "实验设计", '
        '"reason": "内容主要讨论实验配置", '
        '"confidence": 0.85}'
    )
    provider = _MockProvider(chat_result=raw)
    mock_get_factory.return_value = _make_factory(provider)

    result = suggest_structure({"title": "T", "content": "C", "outline": "O"})
    assert result["target_chapter"] == "实验设计"
    assert result["confidence"] == 0.85
    assert "实验设计" in result["suggestion"]


@patch("mock_api.writing_assist.get_factory")
def test_suggest_structure_strips_json_code_block(mock_get_factory):
    """LLM 返回 ```json ... ``` 包裹：剥离后正确解析。"""
    raw = (
        "```json\n"
        '{"suggestion": "移到方法章节", "target_chapter": "方法", '
        '"reason": "理由", "confidence": 0.6}\n'
        "```"
    )
    provider = _MockProvider(chat_result=raw)
    mock_get_factory.return_value = _make_factory(provider)

    result = suggest_structure({"title": "T", "content": "C", "outline": "O"})
    assert result["target_chapter"] == "方法"
    assert result["confidence"] == 0.6


@patch("mock_api.writing_assist.get_factory")
def test_suggest_structure_invalid_json_falls_back(mock_get_factory):
    """JSON 解析失败：降级返回原始文本 + confidence=0。"""
    provider = _MockProvider(chat_result="这不是 JSON 格式的内容")
    mock_get_factory.return_value = _make_factory(provider)

    result = suggest_structure({"title": "T", "content": "C", "outline": "O"})
    assert result["confidence"] == 0.0
    assert "无法解析" in result["reason"] or result["target_chapter"] == ""
    # 原始文本应出现在 suggestion 中
    assert "不是 JSON" in result["suggestion"]


@patch("mock_api.writing_assist.get_factory")
def test_suggest_structure_missing_fields_defaults(mock_get_factory):
    """JSON 缺少部分字段：使用默认值（confidence 默认 0.5）。"""
    provider = _MockProvider(chat_result='{"suggestion": "仅一条建议"}')
    mock_get_factory.return_value = _make_factory(provider)

    result = suggest_structure({"title": "T", "content": "C", "outline": "O"})
    assert result["suggestion"] == "仅一条建议"
    assert result["target_chapter"] == ""
    assert result["confidence"] == 0.5  # 默认值


@patch("mock_api.writing_assist.get_factory")
def test_suggest_structure_llm_error_raises(mock_get_factory):
    """LLM 调用异常 → 抛 LLMError。"""
    provider = _MockProvider(chat_side_effect=TimeoutError("llm timeout"))
    mock_get_factory.return_value = _make_factory(provider)

    with pytest.raises(LLMError):
        suggest_structure({"title": "T", "content": "C", "outline": "O"})


# ===========================================================================
# rewrite_text（全文改写）
# ===========================================================================
@patch("mock_api.writing_assist.get_factory")
def test_rewrite_text_returns_polished(mock_get_factory):
    """正常改写：返回改写后文本。"""
    provider = _MockProvider(chat_result="这是改写后的学术文本。")
    mock_get_factory.return_value = _make_factory(provider)

    result = rewrite_text({"title": "T", "content": "上下文"}, "原文")
    assert result == "这是改写后的学术文本。"


@patch("mock_api.writing_assist.get_factory")
def test_rewrite_text_strips_code_block(mock_get_factory):
    """改写结果被 ``` 包裹：剥离后返回纯文本。"""
    provider = _MockProvider(chat_result="```\n改写后的内容\n```")
    mock_get_factory.return_value = _make_factory(provider)

    result = rewrite_text({"title": "T", "content": "C"}, "原文")
    assert result == "改写后的内容"


@patch("mock_api.writing_assist.get_factory")
def test_rewrite_text_preserves_citation_markers(mock_get_factory):
    """改写文本中的 [@论文ID] 引用标记应被保留（由 prompt 约束）。"""
    provider = _MockProvider(chat_result="改写内容 [@paper_123] 结尾")
    mock_get_factory.return_value = _make_factory(provider)

    result = rewrite_text({"title": "T", "content": "C"}, "原文 [@paper_123]")
    assert "[@paper_123]" in result


@patch("mock_api.writing_assist.get_factory")
def test_rewrite_text_llm_unavailable_raises(mock_get_factory):
    """LLM 不可用 → 抛 LLMError。"""
    factory = MagicMock()
    factory.get_provider.side_effect = RuntimeError("no provider")
    factory.current_label.return_value = "无"
    mock_get_factory.return_value = factory

    with pytest.raises(LLMError):
        rewrite_text({"title": "T", "content": "C"}, "原文")
