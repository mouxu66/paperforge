"""DeepSeek 提供商实现。

DeepSeek 完全兼容 OpenAI Chat Completions 协议，因此直接复用
OpenAIProvider 的实现，仅固定 base_url 与 provider_name。
"""

from __future__ import annotations

from .openai_provider import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek 提供商（兼容 OpenAI 协议）。"""

    provider_name = "deepseek"

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com/v1",
        timeout: int = 120,
    ) -> None:
        super().__init__(api_key=api_key, model=model, base_url=base_url, timeout=timeout)
        self.provider_name = "deepseek"
