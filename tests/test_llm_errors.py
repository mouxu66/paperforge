"""测试 mock_api/llm/errors.py 中的 classify_llm_error 与 LLMError。

覆盖场景：
- 401（API Key 无效）
- 429（触发调用限额/限流）
- 502（未配置任何大模型）
- 504（连接超时）
- 502（连接拒绝）
- 500（通用异常）
"""

from __future__ import annotations

from fastapi import HTTPException
from mock_api.llm.errors import LLMError, classify_llm_error


# ---------------------------------------------------------------------------
# 401 — API Key 无效
# ---------------------------------------------------------------------------
def test_401_invalid_api_key():
    """异常信息包含 401 → 返回 API Key 无效提示。"""
    exc = Exception("401 Unauthorized")
    msg = classify_llm_error(exc)
    assert "API Key" in msg
    assert "无效" in msg


def test_401_unauthorized_keyword():
    """异常信息包含 unauthorized → 返回 API Key 无效提示。"""
    exc = Exception("Unauthorized access")
    msg = classify_llm_error(exc)
    assert "API Key" in msg


def test_401_invalid_api_key_phrase():
    """异常信息包含 invalid api key → 返回 API Key 无效提示。"""
    exc = Exception("invalid api key provided")
    msg = classify_llm_error(exc)
    assert "API Key" in msg


# ---------------------------------------------------------------------------
# 429 — 限流
# ---------------------------------------------------------------------------
def test_429_rate_limit():
    """异常信息包含 429 → 返回限流提示。"""
    exc = Exception("429 Too Many Requests")
    msg = classify_llm_error(exc)
    assert "限流" in msg


def test_429_rate_limit_phrase():
    """异常信息包含 rate limit → 返回限流提示。"""
    exc = Exception("rate limit exceeded")
    msg = classify_llm_error(exc)
    assert "限流" in msg


# ---------------------------------------------------------------------------
# 502 — 未配置任何大模型
# ---------------------------------------------------------------------------
def test_no_model_configured():
    """RuntimeError 包含「未配置任何大模型」→ 返回未配置提示。"""
    exc = RuntimeError("未配置任何大模型，请在「模型管理」页面添加模型配置")
    msg = classify_llm_error(exc)
    assert "未配置任何大模型" in msg
    assert "模型管理" in msg


# ---------------------------------------------------------------------------
# 504 — 连接超时
# ---------------------------------------------------------------------------
def test_timeout():
    """异常信息包含 timeout → 返回超时提示。"""
    exc = Exception("Connection timeout")
    msg = classify_llm_error(exc)
    assert "超时" in msg


def test_timed_out():
    """异常信息包含 timed out → 返回超时提示。"""
    exc = Exception("The read operation timed out")
    msg = classify_llm_error(exc)
    assert "超时" in msg


# ---------------------------------------------------------------------------
# 502 — 连接拒绝
# ---------------------------------------------------------------------------
def test_connection_refused():
    """ConnectionError 实例 → 返回无法连接提示。"""
    exc = ConnectionError("Connection refused")
    msg = classify_llm_error(exc)
    assert "无法连接" in msg


def test_connection_refused_in_message():
    """异常信息包含 connection refused → 返回无法连接提示。"""
    exc = Exception("ConnectionRefusedError: connection refused")
    msg = classify_llm_error(exc)
    assert "无法连接" in msg


def test_connection_reset():
    """异常信息包含 connection reset → 返回无法连接提示。"""
    exc = Exception("connection reset by peer")
    msg = classify_llm_error(exc)
    assert "无法连接" in msg


# ---------------------------------------------------------------------------
# 500 — 通用异常
# ---------------------------------------------------------------------------
def test_generic_exception():
    """无法识别的异常 → 返回兜底提示。"""
    exc = Exception("something went wrong")
    msg = classify_llm_error(exc)
    assert "大模型调用失败" in msg
    assert "something went wrong" in msg


# ---------------------------------------------------------------------------
# current_label 参数
# ---------------------------------------------------------------------------
def test_label_in_message():
    """传入 current_label → 提示中包含模型名。"""
    exc = Exception("timeout")
    msg = classify_llm_error(exc, current_label="我的 GPT-4o")
    assert "模型：我的 GPT-4o" in msg


# ---------------------------------------------------------------------------
# LLMError 是 HTTPException
# ---------------------------------------------------------------------------
def test_llm_error_is_http_exception():
    """LLMError 继承 HTTPException，status_code=502。"""
    exc = Exception("401 Unauthorized")
    err = LLMError(exc, current_label="test-model")
    assert isinstance(err, HTTPException)
    assert err.status_code == 502
    assert "API Key" in err.detail


def test_llm_error_no_model_configured():
    """未配置模型的 LLMError → detail 包含未配置提示。"""
    exc = RuntimeError("未配置任何大模型，请在「模型管理」页面添加模型配置")
    err = LLMError(exc)
    assert err.status_code == 502
    assert "未配置任何大模型" in err.detail
