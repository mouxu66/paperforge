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
    "PAPERFORGE_LOCAL_TIMEOUT_CAP", 280.0
)  # 用户配置的本机超时上限（秒），可用环境变量调大以支持超长生成
_WATCHDOG_SAFETY_MARGIN = 10.0  # 需留给 JSON 解析 / 信号量交接的余量（秒）
_timeout_convergence_warned = False


def _watchdog_timeout() -> float:
    """读取并归一化 watchdog 超时，保证本地请求有可用的严格上界。"""
    try:
        value = float(os.getenv("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "300.0"))
    except (TypeError, ValueError):
        return 300.0
    return value if value > 0.001 else 300.0


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


def _validate_llm_runtime() -> None:
    """DAG / 服务启动时的 LLM 运行时自检（详见 docs/ornith_reasoning_handoff.md §3）。

    两条独立检查：

    ① 硬不变式（防孤儿级联，不依赖 reasoning 状态）：
        cap + margin < watchdog。默认 (100 + 10 = 110 < 120) 通过。
        违反则本机调用可能在 watchdog 前未释放信号量 → 孤儿连接级联 → 批量评测锁死。
        （注意：这是唯一一条安全防线，默认生产配置必须天然满足，否则新进程启动即自杀。）

    ② 功能性检查（仅 reasoning=on 时生效，锁「慢节点存活」而非安全）：
        effective = min(requested, cap, watchdog - margin) 必须 ≥ 200s，
        否则慢节点（思考 CoT ~45–125s）必死在 requests 层。
        这是 reasoning on 场景下的「三件套一起抬」约束——requested 来自
        PAPERFORGE_LLM_REQUEST_TIMEOUT（默认 120），只抬 WATCHDOG+CAP 仍被它卡死。
    """
    watchdog = _watchdog_timeout()
    cap = _LOCAL_TIMEOUT_CAP  # 与 resolve_local_timeout 同源，单一事实来源
    margin = _WATCHDOG_SAFETY_MARGIN

    # ① 硬不变式：安全不依赖 reasoning 状态，默认配置天然满足
    if cap + margin >= watchdog:
        raise RuntimeError(
            f"LLM 超时配置破坏不变式：CAP({cap}) + MARGIN({margin}) = {cap + margin} "
            f">= WATCHDOG({watchdog})。本机调用可能先于 watchdog 释放信号量，导致孤儿级联。"
            f"请调大 PAPERFORGE_LLM_WATCHDOG_TIMEOUT 或调小 PAPERFORGE_LOCAL_TIMEOUT_CAP。"
        )

    # requested 来自 factory 默认 PAPERFORGE_LLM_REQUEST_TIMEOUT（默认 120）
    requested = float(os.getenv("PAPERFORGE_LLM_REQUEST_TIMEOUT", "120"))
    effective = min(requested, cap, watchdog - margin)

    # ② 功能性检查：只在 reasoning on 时锁「慢节点存活」
    reasoning_on = os.getenv("PAPERFORGE_LLAMA_SERVER_REASONING", "off") == "on"
    if reasoning_on and effective < 200.0:
        raise RuntimeError(
            f"reasoning=on 但 effective={effective:.0f}s < 200s，慢节点（思考 CoT）必死在 "
            f"requests 层。需同时调大三件套：PAPERFORGE_LLM_REQUEST_TIMEOUT / "
            f"PAPERFORGE_LOCAL_TIMEOUT_CAP / PAPERFORGE_LLM_WATCHDOG_TIMEOUT。"
        )


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


# 推理模型思维标签：兼容裸标签与 hash 变体（如 <think:6124c78e> / </think:6124c78e>）。
# llama.cpp 在 reasoning 模式下可能注入 4–16 位十六进制 hash（Qwen 系 thinking token），
# 解析层必须一并剥离，否则 hash 变体开关 token 泄漏进 content 会污染结构化抽取（即故障 B）。
def _strip_think_open(text: str) -> str:
    """去掉字符串中所有 <think...> 开标签（含 hash 变体），保留标签间文本。"""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        j = text.find("<think", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        k = text.find(">", j)
        if k < 0:
            break
        i = k + 1
    return "".join(out)


# Qwen3.5 思考模式开标签形如 <think:hash>，正常闭合为 </think>；但模型偶尔把
# hash 残片写进答案首部（如 `</think>:abcd1234 答案`），须清掉。
_HASH_REMNANT_RE = re.compile(r"^:[0-9a-fA-F]{4,16}\b\s*")


def _strip_leading_hash_remnant(answer: str) -> str:
    """去掉答案首部可能残留的 `:hash` 思考模式残片（见 _HASH_REMNANT_RE）。"""
    return _HASH_REMNANT_RE.sub("", answer)


def _split_think(text: str) -> tuple[str, str]:
    """以最后一个 </think>（含 hash 变体）为界切分，返回 (思考文本, 最终答案文本)。

    - 有闭合标签：标签前（去 <think> 开标签）为思考，标签后为答案；
    - 无闭合但有开标签：整段视为思考，答案为空；
    - 两者皆无：思考为空，整段视为答案。

    闭合标签支持 `</think>` 与 `</think:hash>` 两种形态；答案首部若残留 `:hash`
    思考模式残片（Qwen3.5 偶发）一并清理。
    """
    if not text:
        return "", ""
    idx = text.rfind("</think")
    if idx < 0:
        if "<think" in text:
            return text.strip(), ""
        return "", text.strip()
    gt = text.find(">", idx)
    if gt < 0:
        gt = len(text) - 1
    think = _strip_think_open(text[:idx]).strip()
    answer = _strip_leading_hash_remnant(text[gt + 1:].strip())
    return think, answer


def _find_last_json_object(src: str) -> str | None:
    """从文本中抓最后一个完整可解析的 JSON 对象（模型常在思维链里草拟最终 JSON）。"""
    if not src:
        return None
    decoder = json.JSONDecoder()
    best: tuple[int, str] | None = None
    for m in re.finditer(r"\{", src):
        try:
            _obj, end = decoder.raw_decode(src, m.start())
        except Exception:
            continue
        if best is None or end > best[0]:
            best = (end, src[m.start():end])
    return best[1] if best else None


def _extract_final_answer(content: str, reasoning_content: str) -> tuple[str, str]:
    """对 S1–S4 四形态免疫，返回 (最终答案, 审计用思考文本)。

    S1 分离成功: content=答案, reasoning_content=CoT
    S2 分离失败: content=<think>CoT</think>答案
    S3 误路由:   content=<think>纯CoT（无闭合）, 答案缺失
    S4 grammar 冲突: content=""，答案在 reasoning_content
    """
    c, r = (content or "").strip(), (reasoning_content or "").strip()
    c_think, c_ans = _split_think(c)
    if c_ans:
        # 审计用思考 = content 里的 CoT，否则 reasoning_content
        return c_ans, (c_think or r)
    # S3/S4：content 为空或纯思考 → 从 reasoning_content 打捞
    _r_think, r_ans = _split_think(r)
    if r_ans:
        # 审计用思考 = reasoning_content 里的 CoT，否则 content 里的 CoT
        return r_ans, (_r_think or c_think)
    # 最后兜底：两路文本里抓最后一个可解析 JSON 对象
    for src in (r, c):
        obj = _find_last_json_object(src)
        if obj:
            return obj, (r or c_think)
    return "", (r or c_think)


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

    def build_payload(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> dict:
        """本地 llama-server 默认关闭思维链（enable_thinking=false）。

        ⚠️ 2026-08-22 实测背景：Ornith 等推理模型的 chat 模板默认开思维链，
        而调用方（analysis/depth_panel/routers.chat/papers 等）大多不传任何
        thinking 控制 → 每次调用偷偷生成 1-3k 字隐藏 CoT，单次调用耗时虚增
        5-10 倍。本默认只对【本地】base_url 注入，且显式传了
        chat_template_kwargs 的调用方（如 depth_eval_v4 思考模式）完全不受影响。
        PAPERFORGE_LOCAL_DEFAULT_THINKING=1 可关掉此默认（恢复模板自身行为）。
        """
        payload = super().build_payload(messages, temperature, max_tokens, **kwargs)
        if (
            _is_local_url(self.base_url)
            and "chat_template_kwargs" not in payload
            and os.environ.get("PAPERFORGE_LOCAL_DEFAULT_THINKING", "") != "1"
        ):
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        return payload

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
        msg = data["choices"][0]["message"]
        # 统一解析管线：对 S1–S4 四形态免疫，永远以提取出的答案为准；
        # reasoning_content（无论是否含 hash 变体）仅作审计/日志用，不进解析。
        content = msg.get("content")
        reasoning_content = msg.get("reasoning_content") or ""
        answer, think = _extract_final_answer(content, reasoning_content)
        usage = data.get("usage", {})
        return ChatResult(
            content=answer,
            model=self.model,
            provider=self.provider_name,
            usage=usage,
            reasoning=think,  # ADR-014 审计字段：仅日志用，解析永远以 answer 为准
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
