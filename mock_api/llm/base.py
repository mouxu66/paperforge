"""LLM Provider 抽象基类。

所有具体提供商（openai / zhipu / deepseek）均继承 BaseLLMProvider，
实现 chat() 与 chat_stream() 两个方法。上层业务代码只依赖本抽象接口。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, List, Optional


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


class BaseLLMProvider(ABC):
    """LLM 提供商抽象基类。"""

    provider_name: str = "base"

    @abstractmethod
    def chat(self, messages: List[ChatMessage], **kwargs) -> ChatResult:
        raise NotImplementedError

    @abstractmethod
    def chat_stream(self, messages: List[ChatMessage], **kwargs) -> Iterator[str]:
        raise NotImplementedError

    def build_payload(
        self,
        messages: List[ChatMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
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
        payload.update(kwargs)
        return payload
