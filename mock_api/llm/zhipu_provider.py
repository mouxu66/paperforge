"""智谱 AI（GLM 系列）提供商实现。

优先使用 zhipuai SDK；若未安装则降级到 OpenAI 兼容协议的 HTTP 调用
（智谱 GLM-4 等模型已支持 OpenAI 兼容接口）。

复用 OpenAIProvider 的 HTTP 调用逻辑，仅扩展 SDK 优先路径，
避免重复实现 _chat_http / _chat_stream_http。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from threading import Lock

from .base import ChatMessage, ChatResult
from .openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)

# 模块级 flag（uvicorn --reload 时会随模块重 import 重置，但仍能避免单次启动中
# SPAM 几千条相同警告）。线程安全：被类所有实例共享，读写都在 _timeout_warn_lock 内。
_timeout_warned: bool = False
_timeout_warn_lock = Lock()


class ZhipuProvider(OpenAIProvider):
    """智谱 GLM 提供商。

    继承 OpenAIProvider，复用其 chat()/chat_stream() 的 HTTP 调用逻辑作为
    SDK 不可用时的降级路径；SDK 可用时优先走 zhipuai SDK。

    【硬超时防御】SDK 不同版本对 timeout 参数支持不一：
      - 新版 zhipuai：接受 `timeout=` 关键字，调用 SDK 自然遵守
      - 旧版 zhipuai：不识别 `timeout=`，会抛 TypeError → fallback 不传 timeout
      - 旧版 fallback 调用本身无超时上限，靠 DepthReviewer watchdog 兜底
    """

    provider_name = "zhipu"

    def __init__(
        self,
        api_key: str,
        model: str = "glm-4",
        base_url: str = "https://open.bigmodel.cn/api/paas/v4",
        timeout: int = 120,
    ) -> None:
        super().__init__(api_key=api_key, model=model, base_url=base_url, timeout=timeout)
        self.provider_name = "zhipu"
        self._client = None
        try:
            from zhipuai import ZhipuAI

            self._client = ZhipuAI(api_key=api_key, base_url=base_url)
        except ImportError:
            self._client = None

    def _invoke_zhipu_sdk(self, messages, temperature, max_tokens, stream, **caller_kwargs):
        """调用 zhipuai SDK，timeout 兼容性双路径。

        新版 SDK：直接传 timeout= 让 SDK 自身中断请求。
        旧版 SDK：抛 TypeError 后 fallback（警告：此时调用没有超时，依赖上游 watchdog）。

        【kwargs 透传】调用者（chat/chat_stream）传入的额外参数合并下发
        （top_p/stop/frequency_penalty/presence_penalty/tool_choice/user 等）。
        避免 SDK 路径静默丢弃这些参数——HTTP fallback 路径会传，要保持行为一致。

        【基参优先】merged_kwargs 中 **caller_kwargs 在前、基参在后：
        Python dict literal 后 key 覆盖前 key → 基参强制胜出。
        防 caller 误传 model/temperature 覆盖本 provider 的默认配置。
        """
        global _timeout_warned
        merged_kwargs = {
            **caller_kwargs,
            "model": self.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature if temperature is not None else 0.7,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        try:
            return self._client.chat.completions.create(timeout=self.timeout, **merged_kwargs)
        except TypeError as exc:
            with _timeout_warn_lock:
                if not _timeout_warned:
                    logger.warning(
                        "ZhipuProvider: SDK 不支持 timeout= 参数（%s），"
                        "调用实际无超时；依赖 DepthReviewer watchdog 兜底。"
                        "（此警告仅记录一次，后续调用不再重复）",
                        exc,
                    )
                    _timeout_warned = True
            return self._client.chat.completions.create(**merged_kwargs)

    def chat(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> ChatResult:
        if self._client is not None:
            resp = self._invoke_zhipu_sdk(
                messages,
                temperature,
                max_tokens,
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
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> Iterator[str]:
        if self._client is not None:
            resp = self._invoke_zhipu_sdk(
                messages,
                temperature,
                max_tokens,
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
