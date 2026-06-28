"""智谱 AI（GLM 系列）提供商实现。

优先使用 zhipuai SDK；若未安装则降级到 OpenAI 兼容协议的 HTTP 调用
（智谱 GLM-4 等模型已支持 OpenAI 兼容接口）。

复用 OpenAIProvider 的 HTTP 调用逻辑，仅扩展 SDK 优先路径，
避免重复实现 _chat_http / _chat_stream_http。
"""
from __future__ import annotations

from typing import Iterator, List, Optional

from .base import ChatMessage, ChatResult
from .openai_provider import OpenAIProvider


class ZhipuProvider(OpenAIProvider):
    """智谱 GLM 提供商。

    继承 OpenAIProvider，复用其 chat()/chat_stream() 的 HTTP 调用逻辑作为
    SDK 不可用时的降级路径；SDK 可用时优先走 zhipuai SDK。
    """

    provider_name = "zhipu"

    def __init__(
        self,
        api_key: str,
        model: str = "glm-4",
        base_url: str = "https://open.bigmodel.cn/api/paas/v4",
        timeout: int = 120,
    ) -> None:
        super().__init__(
            api_key=api_key, model=model, base_url=base_url, timeout=timeout
        )
        self.provider_name = "zhipu"
        self._client = None
        try:
            from zhipuai import ZhipuAI  # type: ignore

            self._client = ZhipuAI(api_key=api_key, base_url=base_url)
        except ImportError:
            self._client = None

    def chat(
        self,
        messages: List[ChatMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> ChatResult:
        if self._client is not None:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[m.to_dict() for m in messages],
                temperature=temperature if temperature is not None else 0.7,
                max_tokens=max_tokens,
                stream=False,
                **kwargs,
            )
            content = resp.choices[0].message.content
            usage = getattr(resp, "usage", {})
            if hasattr(usage, "model_dump"):
                usage = usage.model_dump()
            return ChatResult(
                content=content, model=self.model, provider=self.provider_name, usage=usage or {}
            )
        # SDK 不可用时降级到 OpenAI 兼容协议（复用父类实现）
        return super().chat(messages, temperature, max_tokens, **kwargs)

    def chat_stream(
        self,
        messages: List[ChatMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Iterator[str]:
        if self._client is not None:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[m.to_dict() for m in messages],
                temperature=temperature if temperature is not None else 0.7,
                max_tokens=max_tokens,
                stream=True,
                **kwargs,
            )
            for chunk in resp:
                try:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
                except (AttributeError, IndexError):
                    continue
            return
        # SDK 不可用时降级到 OpenAI 兼容协议（复用父类实现）
        yield from super().chat_stream(messages, temperature, max_tokens, **kwargs)
