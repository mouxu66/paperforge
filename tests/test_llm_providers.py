"""测试 mock_api/llm 各 Provider 的真实 HTTP 请求路径。

覆盖：
- OpenAIProvider.chat() / chat_stream() 对 OpenAI 兼容协议的请求构造与响应解析
- ZhipuProvider 在 zhipuai SDK 不可用/可用时的降级与调用路径
- DeepSeekProvider 继承路径
- 推理模型思维标签（<thinking> / </thinking>）剥离
- HTTP 错误码传播

所有测试均 mock requests/zhipuai，不访问真实网络。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from mock_api.llm.base import ChatMessage, ChatResult
from mock_api.llm.deepseek_provider import DeepSeekProvider
from mock_api.llm.openai_provider import OpenAIProvider
from mock_api.llm.zhipu_provider import ZhipuProvider


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------
def _openai_response(content: str, model: str = "gpt-4o") -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def test_openai_provider_chat_success():
    """chat() 正确构造 payload 并解析返回。"""
    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
    messages = [ChatMessage(role="user", content="hello")]

    with patch("mock_api.llm.openai_provider.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _openai_response("Hi there")
        mock_post.return_value = mock_resp

        result = provider.chat(messages, temperature=0.5, max_tokens=100)

        assert isinstance(result, ChatResult)
        assert result.content == "Hi there"
        assert result.model == "gpt-4o"
        assert result.provider == "openai"
        assert result.usage["total_tokens"] == 15

        # 验证请求参数
        args, kwargs = mock_post.call_args
        assert kwargs["json"]["model"] == "gpt-4o"
        assert kwargs["json"]["messages"] == [{"role": "user", "content": "hello"}]
        assert kwargs["json"]["temperature"] == 0.5
        assert kwargs["json"]["max_tokens"] == 100
        assert kwargs["headers"]["Authorization"] == "Bearer sk-test"


def test_openai_provider_chat_strips_thinking_tags():
    """chat() 剥离 <think> / </think> XML 标签。"""
    provider = OpenAIProvider(api_key="sk-test")
    messages = [ChatMessage(role="user", content="hi")]

    with patch("mock_api.llm.openai_provider.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        # 标签实际匹配 XML 标签 <think> / </think>（参见 openai_provider.py 的 regex）
        mock_resp.json.return_value = _openai_response("<think> hello </think>")
        mock_post.return_value = mock_resp

        result = provider.chat(messages)
        # 标签被移除，中间内容保留；去除多余空白后比较
        assert "<think>" not in result.content
        assert "</think>" not in result.content
        assert result.content.strip() == "hello"


def test_openai_provider_thinking_regex_directly():
    """直接验证 <think> / </think> 标签剥离正则。"""
    from mock_api.llm.openai_provider import (
        _THINK_TAG_CLOSE_RE,
        _THINK_TAG_OPEN_RE,
    )

    raw = "<think> hello </think>"
    cleaned = _THINK_TAG_CLOSE_RE.sub("", _THINK_TAG_OPEN_RE.sub("", raw)).strip()
    assert cleaned == "hello"


def test_openai_provider_chat_http_error():
    """chat() 将 HTTP 错误以异常形式抛出。"""
    provider = OpenAIProvider(api_key="sk-test")
    messages = [ChatMessage(role="user", content="hi")]

    with patch("mock_api.llm.openai_provider.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.HTTPError("401 Unauthorized")
        mock_post.return_value = mock_resp

        with pytest.raises(requests.HTTPError):
            provider.chat(messages)


def test_openai_provider_chat_stream_success():
    """chat_stream() 正确解析 SSE 流。"""
    provider = OpenAIProvider(api_key="sk-test")
    messages = [ChatMessage(role="user", content="hi")]

    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "Hel"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "lo"}}]}),
        "data: [DONE]",
    ]

    with patch("mock_api.llm.openai_provider.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = lines
        mock_post.return_value.__enter__ = MagicMock(return_value=mock_resp)
        mock_post.return_value.__exit__ = MagicMock(return_value=False)

        chunks = list(provider.chat_stream(messages))
        assert chunks == ["Hel", "lo"]


def test_openai_provider_chat_stream_reasoning_content():
    """chat_stream() 支持 reasoning_content 字段。"""
    provider = OpenAIProvider(api_key="sk-test")
    messages = [ChatMessage(role="user", content="hi")]

    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"reasoning_content": "think"}}]}),
        "data: [DONE]",
    ]

    with patch("mock_api.llm.openai_provider.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = lines
        mock_post.return_value.__enter__ = MagicMock(return_value=mock_resp)
        mock_post.return_value.__exit__ = MagicMock(return_value=False)

        chunks = list(provider.chat_stream(messages))
        assert chunks == ["think"]


def test_openai_provider_chat_stream_ignores_malformed_lines():
    """chat_stream() 忽略非 data: 行和非法 JSON。"""
    provider = OpenAIProvider(api_key="sk-test")
    messages = [ChatMessage(role="user", content="hi")]

    lines = [
        ": ping",
        "data: not-json",
        "data: " + json.dumps({"choices": [{"delta": {"content": "ok"}}]}),
        "data: [DONE]",
    ]

    with patch("mock_api.llm.openai_provider.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_lines.return_value = lines
        mock_post.return_value.__enter__ = MagicMock(return_value=mock_resp)
        mock_post.return_value.__exit__ = MagicMock(return_value=False)

        chunks = list(provider.chat_stream(messages))
        assert chunks == ["ok"]


def test_openai_provider_build_payload_extra_kwargs():
    """build_payload 透传额外 kwargs。"""
    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o")
    messages = [ChatMessage(role="user", content="hi")]
    payload = provider.build_payload(messages, temperature=0.2, top_p=0.9, stop=["\n"])
    assert payload["temperature"] == 0.2
    assert payload["top_p"] == 0.9
    assert payload["stop"] == ["\n"]


# ---------------------------------------------------------------------------
# DeepSeekProvider
# ---------------------------------------------------------------------------
def test_deepseek_provider_inherits_openai():
    """DeepSeekProvider 继承 OpenAIProvider 并设置正确 base_url/provider_name。"""
    provider = DeepSeekProvider(api_key="sk-test", model="deepseek-chat")
    assert provider.provider_name == "deepseek"
    assert provider.base_url == "https://api.deepseek.com/v1"
    assert provider.model == "deepseek-chat"


# ---------------------------------------------------------------------------
# ZhipuProvider
# ---------------------------------------------------------------------------
def test_zhipu_provider_fallback_to_openai_when_sdk_missing():
    """zhipuai SDK 不可用时降级到 OpenAI 兼容 HTTP 路径。"""
    provider = ZhipuProvider(api_key="sk-test", model="glm-4")
    # 强制模拟 SDK 不可用（无论环境是否安装 zhipuai）
    provider._client = None

    messages = [ChatMessage(role="user", content="hello")]

    with patch("mock_api.llm.openai_provider.requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _openai_response("Zhipu via HTTP", model="glm-4")
        mock_post.return_value = mock_resp

        result = provider.chat(messages)
        assert result.content == "Zhipu via HTTP"
        assert result.provider == "zhipu"


def test_zhipu_provider_sdk_path_success():
    """zhipuai SDK 可用时走 SDK 路径。"""
    provider = ZhipuProvider(api_key="sk-test", model="glm-4")

    # 构造一个最小 mock SDK client
    mock_usage = MagicMock()
    mock_usage.model_dump.return_value = {"total_tokens": 7}
    mock_message = MagicMock()
    mock_message.content = "Zhipu via SDK"
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_resp = MagicMock()
    mock_resp.choices = [mock_choice]
    mock_resp.usage = mock_usage

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_resp
    provider._client = mock_client

    messages = [ChatMessage(role="user", content="hello")]
    result = provider.chat(messages, temperature=0.3, max_tokens=50)

    assert result.content == "Zhipu via SDK"
    assert result.provider == "zhipu"
    assert result.usage["total_tokens"] == 7

    # 验证 SDK 调用参数
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "glm-4"
    assert call_kwargs["temperature"] == 0.3
    assert call_kwargs["max_tokens"] == 50
    assert call_kwargs["stream"] is False


def test_zhipu_provider_sdk_timeout_fallback():
    """旧版 zhipuai SDK 不支持 timeout 时 fallback 并只警告一次。"""
    provider = ZhipuProvider(api_key="sk-test", model="glm-4")

    mock_client = MagicMock()
    # 第一次带 timeout 抛 TypeError，第二次不带 timeout 成功
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock(message=MagicMock(content="ok"))]
    mock_resp.usage = {}
    mock_client.chat.completions.create.side_effect = [
        TypeError("unexpected keyword timeout"),
        mock_resp,
    ]
    provider._client = mock_client

    messages = [ChatMessage(role="user", content="hello")]
    result = provider.chat(messages)
    assert result.content == "ok"
    assert mock_client.chat.completions.create.call_count == 2


def test_zhipu_provider_stream_sdk_path():
    """chat_stream() 走 SDK 路径时正确 yield delta。"""
    provider = ZhipuProvider(api_key="sk-test", model="glm-4")

    chunk1 = MagicMock()
    chunk1.choices = [MagicMock(delta=MagicMock(content="Hel"))]
    chunk2 = MagicMock()
    chunk2.choices = [MagicMock(delta=MagicMock(content="lo"))]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = [chunk1, chunk2]
    provider._client = mock_client

    messages = [ChatMessage(role="user", content="hi")]
    chunks = list(provider.chat_stream(messages))
    assert chunks == ["Hel", "lo"]
