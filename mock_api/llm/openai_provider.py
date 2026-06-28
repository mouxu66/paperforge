"""OpenAI 兼容提供商实现。

支持自定义 base_url，因此可同时用于官方 OpenAI、Azure OpenAI、
以及任何兼容 OpenAI Chat Completions 协议的服务（如本地 vLLM / Ollama）。
"""
from __future__ import annotations

import json
from typing import Iterator, List, Optional

import requests

from .base import BaseLLMProvider, ChatMessage, ChatResult


class OpenAIProvider(BaseLLMProvider):
    """OpenAI 官方 / 兼容服务提供商。"""

    provider_name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o",
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 120,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(
        self,
        messages: List[ChatMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> ChatResult:
        payload = self.build_payload(messages, temperature, max_tokens, **kwargs)
        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return ChatResult(
            content=content, model=self.model, provider=self.provider_name, usage=usage
        )

    def chat_stream(
        self,
        messages: List[ChatMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs,
    ) -> Iterator[str]:
        payload = self.build_payload(messages, temperature, max_tokens, **kwargs)
        payload["stream"] = True
        with requests.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
            stream=True,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                chunk = line[len("data: "):]
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                    delta = obj["choices"][0].get("delta", {}).get("content")
                    if delta:
                        yield delta
                except (ValueError, KeyError, IndexError):
                    continue
