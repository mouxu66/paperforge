"""OpenAI 兼容提供商实现。

支持自定义 base_url，因此可同时用于官方 OpenAI、Azure OpenAI、
以及任何兼容 OpenAI Chat Completions 协议的服务（如本地 vLLM / Ollama）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import threading
from collections.abc import Iterator
from urllib.parse import urlparse

import requests

from .base import BaseLLMProvider, ChatMessage, ChatResult, LocalLLMTimeout

logger = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────────────────────────
# 全局并发信号量：保护单实例本地 LLM（如 127.0.0.1:8080 的 llama-server）
# 不被大量并发请求压垮。
#
# 背景：DEPTH deep 模式会把 Q2/Q3/Q4 并发打到同一实例，实测单实例 llama-server
# 在 3 路并发下请求延迟从 ~1.25s 飙到 ~46.87s（排队 + 投机解码草稿失配）。
# 串行（默认 1）可彻底消除该问题，使「几百篇批量审稿」变得可行且稳定。
#
# 仅对 localhost / 127.0.0.1 的 base_url 生效；外部托管 API（官方 OpenAI 等）
# 不受影响，可继续高并发。
#
# 可通过环境变量 PAPERFORGE_LLM_MAX_CONCURRENCY 调整（>=1）。若本地实例经测试
# 能稳定承载更高并发，可调到 2~4 换取吞吐；不确定时保持默认 1 最安全。
# ───────────────────────────────────────────────────────────────────────────
def _resolve_max_concurrency() -> int:
    raw = os.environ.get("PAPERFORGE_LLM_MAX_CONCURRENCY")
    if raw is None:
        return 1
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 1
    return value if value >= 1 else 1


_LLM_MAX_CONCURRENCY = _resolve_max_concurrency()
_LLM_SEM = threading.Semaphore(_LLM_MAX_CONCURRENCY)


# ───────────────────────────────────────────────────────────────────────────
# 本机 LLM 请求超时上限（秒）
#
# 【必须满足的不变式】单次本机调用占用 _LLM_SEM 的时长 < watchdog 超时，
# 否则 watchdog 判死后工作线程仍握着信号量 → 下一篇排队即超时 → 孤儿级联。
#
# 2026-08-05 事故：该不变式曾靠「注释提醒人去配对两个环境变量」维持，
# 结果被两件事同时打破 ——
#   1) @llm_retry 会把单次调用放大成 3 × cap + 退避（100 → 303s ≫ 120s watchdog）；
#   2) 用户为支持长文调大 cap=400 却只把 watchdog 提到 450（400×3 = 1203s ≫ 450s），
#      亏空反而扩大 4 倍。
# 后果：41 篇批量评测累计 34 个孤儿，后 17 篇耗时锁死 ~255s 平台期，四维被压 0.3。
#
# 现在改为代码强制：
#   ① 本机读超时抛 LocalLLMTimeout，被 retry_utils 列为不可重试 → 放大系数回到 1×；
#   ② effective cap 自动收敛到 watchdog - 安全余量，人配错也不会破坏不变式。
# 外部托管 API 不受影响，仍用原始 timeout 并保留重试。
# ───────────────────────────────────────────────────────────────────────────
def _env_positive_float(name: str, default: float) -> float:
    """读取正浮点环境变量；非法值回退默认值。"""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("忽略非法环境变量 %s，使用默认值 %s", name, default)
        return default
    if value <= 0:
        logger.warning("环境变量 %s 必须大于 0，使用默认值 %s", name, default)
        return default
    return value


_LOCAL_TIMEOUT_CAP = _env_positive_float(
    "PAPERFORGE_LOCAL_TIMEOUT_CAP", 100.0
)  # 用户配置的本机超时上限（秒），可用环境变量调大以支持超长生成
_WATCHDOG_SAFETY_MARGIN = 10.0  # 需留给 JSON 解析 / 信号量交接的余量（秒）
_timeout_convergence_warned = False


def _watchdog_timeout() -> float:
    """读取并归一化 watchdog 超时，保证本地请求有可用的严格上界。"""
    try:
        value = float(os.getenv("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "120.0"))
    except (TypeError, ValueError):
        return 120.0
    return value if value > 0.001 else 120.0


def resolve_local_timeout(requested: float) -> float:
    """计算本机请求的实际读超时：min(调用方要求, 用户 cap, watchdog - 余量)。

    第三项是硬约束——保证 requests 一定先于 watchdog 超时并释放信号量，
    因此即使 PAPERFORGE_LOCAL_TIMEOUT_CAP 被配得比 watchdog 还大也不会产生孤儿。
    """
    global _timeout_convergence_warned
    ceiling = _watchdog_timeout() - _WATCHDOG_SAFETY_MARGIN
    if ceiling <= 0:
        ceiling = max(_watchdog_timeout() * 0.8, 1.0)
    effective = min(float(requested), float(_LOCAL_TIMEOUT_CAP), ceiling)
    if ceiling < _LOCAL_TIMEOUT_CAP and not _timeout_convergence_warned:
        _timeout_convergence_warned = True
        logger.warning(
            "PAPERFORGE_LOCAL_TIMEOUT_CAP=%ss 超过 watchdog(%ss) - 安全余量(%ss)，"
            "已自动收敛到 %.0fs 以防孤儿连接级联；如需更长生成时间，"
            "请同时调大 PAPERFORGE_LLM_WATCHDOG_TIMEOUT。",
            _LOCAL_TIMEOUT_CAP,
            _watchdog_timeout(),
            _WATCHDOG_SAFETY_MARGIN,
            ceiling,
        )
    # watchdog 配置过小时也必须保持严格早于 watchdog；至少保留 1ms 的余量。
    return max(min(effective, max(_watchdog_timeout() - 0.001, 0.001)), 0.001)


def _is_local_url(base_url: str) -> bool:
    """判断 base_url 是否指向本机（需要施加并发限制）。"""
    try:
        host = urlparse(base_url or "").hostname or ""
    except Exception:
        return False
    if host in ("localhost", "127.0.0.1", "::1", ""):
        return host != ""  # 空 host 视为非本地，避免误伤
    try:
        return socket.gethostbyname(host) == "127.0.0.1"
    except Exception:
        return False


# 推理模型思维标签剥离（--reasoning-format none 时 <think> 出现在 content 中，可能无闭合标签）
# 仅移除 XML 标签本身，保留标签间文本供 safe_json_parse 提取 JSON
_THINK_TAG_OPEN_RE = re.compile(r"<think>\s*", re.IGNORECASE)
_THINK_TAG_CLOSE_RE = re.compile(r"</think>\s*", re.IGNORECASE)


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

    def _post(self, payload: dict) -> requests.Response:
        """POST 到 chat/completions，并按 base_url 是否本机施加全局并发限制。

        仅当目标为 localhost 时才用信号量串行化，避免单实例本地 LLM 被压垮；
        外部托管 API 走原生并发（不受限）。
        """
        url = f"{self.base_url}/chat/completions"
        if _is_local_url(self.base_url):
            # 本机：串行化 + 超时收敛（严格低于 watchdog，防止孤儿连接堆积）
            effective_timeout = resolve_local_timeout(self.timeout)
            _LLM_SEM.acquire()
            try:
                return requests.post(
                    url,
                    json=payload,
                    headers=self._headers(),
                    timeout=effective_timeout,
                )
            except requests.exceptions.Timeout as e:
                # 本机超时 → 专用异常，retry_utils 不重试（重试只会加剧排队）。
                raise LocalLLMTimeout(
                    f"本机 LLM 在 {effective_timeout:.0f}s 内未返回（{url}）；"
                    "已放弃且不重试，以免继续占用串行信号量"
                ) from e
            finally:
                _LLM_SEM.release()
        return requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout)

    def chat(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> ChatResult:
        payload = self.build_payload(messages, temperature, max_tokens, **kwargs)
        resp = self._post(payload)
        resp.raise_for_status()
        data = resp.json()
        msg = data["choices"][0]["message"]
        content = msg.get("content") or msg.get("reasoning_content") or ""
        # 剥离推理模型的 <think> / </think> XML 标签（--reasoning-format none 时出现）
        content = _THINK_TAG_CLOSE_RE.sub("", _THINK_TAG_OPEN_RE.sub("", content)).strip()
        usage = data.get("usage", {})
        return ChatResult(
            content=content, model=self.model, provider=self.provider_name, usage=usage
        )

    def chat_stream(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> Iterator[str]:
        payload = self.build_payload(messages, temperature, max_tokens, **kwargs)
        payload["stream"] = True
        with self._post(payload) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                chunk = line[len("data: ") :]
                if chunk == "[DONE]":
                    break
                try:
                    obj = json.loads(chunk)
                    d = obj["choices"][0].get("delta", {})
                    delta = d.get("content") or d.get("reasoning_content") or ""
                    if delta:
                        yield delta
                except (ValueError, KeyError, IndexError):
                    continue
