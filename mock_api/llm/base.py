"""LLM Provider 抽象基类。

所有具体提供商（openai / zhipu / deepseek）均继承 BaseLLMProvider，
实现 chat() 与 chat_stream() 两个方法。上层业务代码只依赖本抽象接口。
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass
class ChatMessage:
    """统一的对话消息结构。"""

    role: str  # "system" | "user" | "assistant"
    content: str

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


@dataclass
class ChatResult:
    """非流式 chat() 返回结构。"""

    content: str
    model: str
    provider: str
    usage: dict = field(default_factory=dict)


class LocalLLMTimeout(TimeoutError):
    """本机 LLM（127.0.0.1 / localhost）读超时。

    与「远程 API 超时」在语义上必须区分开：

    - 远程超时通常是网络抖动 → 重试是对的。
    - **本机超时只说明这台机器上的单实例 llama-server 正在慢速生成**。
      此时重试救不回来，反而会继续占用容量为 1 的 ``_LLM_SEM`` 信号量，
      把后续请求全堵在队列里 → watchdog 逐个判死 → 孤儿连接级联。
      （2026-08-05 实测：41 篇批量评测因此累计 34 个孤儿，后 17 篇耗时
      锁死在 ~255s 平台期，四维分数被 R1 压成 0.3。）

    因此本异常被列入 ``retry_utils._NON_RETRYABLE_EXCEPTIONS``：
    本机超时立即放弃，把信号量交还给下一篇，不做任何重试。
    """


class BaseLLMProvider(ABC):
    """LLM 提供商抽象基类。"""

    provider_name: str = "base"

    @abstractmethod
    def chat(self, messages: list[ChatMessage], **kwargs) -> ChatResult:
        raise NotImplementedError

    @abstractmethod
    def chat_stream(self, messages: list[ChatMessage], **kwargs) -> Iterator[str]:
        raise NotImplementedError

    def build_payload(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> dict:
        payload: dict = {
            "model": getattr(self, "model", ""),
            "messages": [m.to_dict() for m in messages],
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        # 确定性开关：批量评估时强制固定 temperature，消除 run-to-run 漂移。
        # 仅当调用方未显式指定 temperature 时生效（显式值优先）。
        if temperature is None:
            _forced = os.environ.get("PAPERFORGE_LLM_TEMPERATURE")
            if _forced is not None:
                try:
                    payload["temperature"] = float(_forced)
                except ValueError:
                    pass
        payload.update(kwargs)
        return payload
