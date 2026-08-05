"""LLM 错误分类与统一异常类型。

将各类底层异常（连接超时、鉴权失败、RuntimeError 等）翻译为用户友好的
中文提示，避免把原始堆栈直接暴露给前端。
"""

from __future__ import annotations

from fastapi import HTTPException


def classify_llm_error(exc: Exception, current_label: str = "") -> str:
    """把底层异常翻译成中文用户提示。

    Args:
        exc: LLM 调用过程中抛出的任意异常。
        current_label: 当前模型显示名（用于在提示中指明是哪个模型出错）。
    """
    s = str(exc)
    label = f"（模型：{current_label}）" if current_label else ""

    # 0. 未配置任何模型（factory.get_provider 抛 RuntimeError，llm_configs 表为空）
    if isinstance(exc, RuntimeError) and "未配置任何大模型" in s:
        return "未配置任何大模型，请在「模型管理」页面添加并启用模型配置"

    # 1. 连接类错误：本地服务未启动 / 远程地址不通
    if isinstance(exc, ConnectionError):
        return f"无法连接到大模型服务，请检查 API 地址或本地服务是否启动{label}"
    lower = s.lower()
    if "connection" in lower and ("refused" in lower or "reset" in lower or "closed" in lower):
        return f"无法连接到大模型服务，请检查 API 地址或本地服务是否启动{label}"
    if "name or service not known" in lower or "getaddrinfo" in lower:
        return f"无法解析 API 地址，请检查配置是否正确{label}"

    # 2. 超时
    if "timeout" in lower or "timed out" in lower:
        return f"模型响应超时，请稍后重试或检查网络{label}"

    # 3. 鉴权失败
    if "401" in s or "unauthorized" in lower or "invalid api key" in lower:
        return f"API Key 无效或未授权，请在「模型管理」页面检查配置{label}"

    # 4. 限流
    if "429" in s or "rate limit" in lower:
        return f"请求过于频繁被限流，请稍后重试{label}"

    # 5. 模型不存在
    if "model" in lower and ("not found" in lower or "does not exist" in lower):
        return f"模型 ID 不存在，请检查模型配置{label}"

    # 6. 兜底
    return f"大模型调用失败：{s}{label}"


class LLMError(HTTPException):
    """LLM 调用异常 → HTTP 502，detail 为中文友好提示。"""

    def __init__(self, exc: Exception, current_label: str = "") -> None:
        msg = classify_llm_error(exc, current_label)
        super().__init__(status_code=502, detail=msg)
