"""
OpenAI 兼容提供商并发限流 / 超时封顶单元测试
（``mock_api/llm/openai_provider.py``）。

覆盖：
- ``_is_local_url``         localhost / 127.0.0.1 → True，外网 → False
- 本地 POST 的 timeout 封顶（<= ``_LOCAL_TIMEOUT_CAP``）
- ``_LLM_SEM`` 在并发下限制 localhost 并发（线程模拟，默认并发=1 → 串行）

标记：本文件全部用例属并发安全护栏，统一挂 ``@pytest.mark.critical``。
运行：
    python -m pytest tests/test_openai_provider_limit.py -v
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest

from mock_api.llm import openai_provider as prov
from mock_api.llm.base import LocalLLMTimeout
from mock_api.llm.openai_provider import (
    OpenAIProvider,
    _LOCAL_TIMEOUT_CAP,
    _is_local_url,
    resolve_local_timeout,
)


# ===========================================================================
# 1. _is_local_url —— 本机判定
# ===========================================================================
@pytest.mark.critical
class TestIsLocalUrl:
    def test_localhost_true(self):
        assert _is_local_url("http://localhost:8080/v1") is True

    def test_127_true(self):
        assert _is_local_url("http://127.0.0.1:8080/v1") is True

    def test_external_false(self):
        assert _is_local_url("https://api.openai.com/v1") is False

    def test_example_com_false(self):
        assert _is_local_url("http://example.com/v1") is False

    def test_empty_false(self):
        # 空 host 视为非本地，避免误伤
        assert _is_local_url("") is False

    def test_none_false(self):
        assert _is_local_url(None) is False


# ===========================================================================
# 2. 本地 POST 的 timeout 封顶
# ===========================================================================
@pytest.mark.critical
class TestLocalTimeoutCap:
    def test_cap_applied_when_timeout_exceeds_cap(self):
        """本地 URL：timeout=120 超过 _LOCAL_TIMEOUT_CAP(100) → 封顶 100。"""
        provider = OpenAIProvider(api_key="x", base_url="http://127.0.0.1:8080/v1", timeout=120)
        with patch.object(prov.requests, "post", return_value=MagicMock()) as mock_post:
            provider._post({"model": "m", "messages": []})
        called_timeout = mock_post.call_args.kwargs["timeout"]
        assert called_timeout == _LOCAL_TIMEOUT_CAP
        assert called_timeout <= _LOCAL_TIMEOUT_CAP

    def test_invalid_timeout_env_falls_back(self, monkeypatch):
        """非法本地 timeout 配置不应在模块导入/请求时崩溃。"""
        monkeypatch.setenv("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "not-a-number")
        assert resolve_local_timeout(50) == 50

    def test_tiny_watchdog_uses_safe_default(self, monkeypatch):
        """过小 watchdog 配置回退到安全默认值，避免 timeout 反超 watchdog。"""
        monkeypatch.setenv("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "0.0005")
        assert resolve_local_timeout(10) < 120

    def test_zero_watchdog_uses_safe_default(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "0")
        assert resolve_local_timeout(10) < 120

    def test_no_cap_when_under(self):
        """本地 URL：timeout=50 < cap → 使用原值 50。"""
        provider = OpenAIProvider(api_key="x", base_url="http://127.0.0.1:8080/v1", timeout=50)
        with patch.object(prov.requests, "post", return_value=MagicMock()) as mock_post:
            provider._post({"model": "m", "messages": []})
        assert mock_post.call_args.kwargs["timeout"] == 50

    def test_external_not_capped(self):
        """外网 URL：timeout 不封顶，原值（120）传入。"""
        provider = OpenAIProvider(api_key="x", base_url="https://api.openai.com/v1", timeout=120)
        with patch.object(prov.requests, "post", return_value=MagicMock()) as mock_post:
            provider._post({"model": "m", "messages": []})
        assert mock_post.call_args.kwargs["timeout"] == 120

    def test_local_url_invokes_semaphore(self):
        """本地 URL 路径会 acquire 信号量（通过计数验证串行化）。"""
        provider = OpenAIProvider(api_key="x", base_url="http://localhost:8080/v1", timeout=10)
        # 记录同时处于 requests.post 内的并发数峰值
        state = {"in_flight": 0, "peak": 0}
        lock = threading.Lock()
        release = threading.Event()

        def blocking_post(*args, **kwargs):
            with lock:
                state["in_flight"] += 1
                state["peak"] = max(state["peak"], state["in_flight"])
            release.wait()
            with lock:
                state["in_flight"] -= 1
            return MagicMock()

        threads = []
        with patch.object(prov.requests, "post", side_effect=blocking_post):
            for _ in range(3):
                t = threading.Thread(target=lambda: provider._post({"m": 1}))
                threads.append(t)
                t.start()
            # 给线程时间尝试进入（信号量限制 1，最多 1 个在 post 内）
            import time

            time.sleep(0.3)
            # 本地路径被 _LLM_SEM(value=1) 串行化 → 峰值并发必须为 1
            assert state["peak"] == 1
            release.set()
            for t in threads:
                t.join(timeout=5)


# ===========================================================================
# 3. _LLM_SEM 并发限制（本地串行 / 外网不限制）
# ===========================================================================
@pytest.mark.critical
class TestConcurrencyLimit:
    def _run_contended(self, base_url: str, n: int, timeout: float):
        """启动 n 个线程并发 POST；统计 requests.post 内并发峰值。"""
        provider = OpenAIProvider(api_key="x", base_url=base_url, timeout=timeout)
        state = {"in_flight": 0, "peak": 0}
        lock = threading.Lock()
        go = threading.Event()

        def blocking_post(*args, **kwargs):
            with lock:
                state["in_flight"] += 1
                state["peak"] = max(state["peak"], state["in_flight"])
            go.wait()
            with lock:
                state["in_flight"] -= 1
            return MagicMock()

        threads = []
        with patch.object(prov.requests, "post", side_effect=blocking_post):
            for _ in range(n):
                t = threading.Thread(target=lambda: provider._post({"m": 1}))
                threads.append(t)
                t.start()
            import time

            time.sleep(0.3)
            peak = state["peak"]
            go.set()
            for t in threads:
                t.join(timeout=5)
        return peak

    def test_local_serialized(self):
        """localhost 默认并发=1 → 并发峰值 == 1（串行）。"""
        peak = self._run_contended("http://localhost:8080/v1", n=4, timeout=10)
        assert peak == 1

    def test_external_unlimited(self):
        """外网不走信号量 → 并发峰值 == n（不被限制）。"""
        peak = self._run_contended("https://api.openai.com/v1", n=4, timeout=10)
        assert peak == 4

    def test_semaphore_object_exists(self):
        """_LLM_SEM 是线程信号量（默认容量 >=1）。"""
        assert isinstance(prov._LLM_SEM, threading.Semaphore)
        assert prov._LLM_SEM._value >= 1


# ===========================================================================
# 4. 孤儿连接级联防线（2026-08-05 事故回归）
# ===========================================================================
# 事故：watchdog(120s) 判死后，工作线程仍在跑 @llm_retry(3 次) × cap(100s)
# = 最长 303s，全程占着容量为 1 的 _LLM_SEM → 下一篇排队必超 → 自持级联。
# 41 篇批量评测累计 34 个孤儿，后 17 篇耗时锁死 ~255s 平台期、四维被 R1 压成 0.3。
#
# 两道防线必须同时成立，缺一即复发：
#   ① 本机读超时抛 LocalLLMTimeout 且不可重试（放大系数 3× → 1×）
#   ② effective timeout 自动收敛到 watchdog - 余量（人配错也不破坏不变式）
@pytest.mark.critical
class TestOrphanCascadeGuard:
    def test_local_timeout_raises_dedicated_exception(self):
        """本机 requests 超时 → LocalLLMTimeout（而非裸 requests.Timeout）。"""
        provider = OpenAIProvider(api_key="x", base_url="http://127.0.0.1:8080/v1", timeout=10)
        with patch.object(
            prov.requests, "post", side_effect=prov.requests.exceptions.ReadTimeout("boom")
        ):
            with pytest.raises(LocalLLMTimeout):
                provider._post({"model": "m", "messages": []})

    def test_local_timeout_releases_semaphore(self):
        """本机超时后信号量必须归还，否则后续请求全部饿死。"""
        provider = OpenAIProvider(api_key="x", base_url="http://127.0.0.1:8080/v1", timeout=10)
        before = prov._LLM_SEM._value
        with patch.object(
            prov.requests, "post", side_effect=prov.requests.exceptions.ReadTimeout("boom")
        ):
            with pytest.raises(LocalLLMTimeout):
                provider._post({"model": "m", "messages": []})
        assert prov._LLM_SEM._value == before

    def test_local_timeout_is_not_retryable(self):
        """LocalLLMTimeout 必须在 retry_utils 的不可重试名单里。"""
        from mock_api.retry_utils import _NON_RETRYABLE_EXCEPTIONS

        assert LocalLLMTimeout in _NON_RETRYABLE_EXCEPTIONS

    def test_llm_retry_does_not_retry_local_timeout(self):
        """端到端：被 @llm_retry 装饰的函数遇 LocalLLMTimeout 只调用 1 次。"""
        from mock_api.retry_utils import llm_retry

        calls = {"n": 0}

        @llm_retry
        def boom():
            calls["n"] += 1
            raise LocalLLMTimeout("slow local server")

        with pytest.raises(LocalLLMTimeout):
            boom()
        assert calls["n"] == 1, "本机超时被重试了 → 会重新触发孤儿级联"

    def test_remote_timeout_still_retried(self):
        """反向保护：远程超时仍应重试 3 次（不能误伤网络抖动恢复能力）。"""
        from mock_api.retry_utils import llm_retry

        calls = {"n": 0}

        @llm_retry
        def boom():
            calls["n"] += 1
            raise prov.requests.exceptions.ReadTimeout("remote flake")

        with pytest.raises(prov.requests.exceptions.ReadTimeout):
            boom()
        assert calls["n"] == 3

    def test_timeout_converges_below_watchdog(self, monkeypatch):
        """cap 配得比 watchdog 还大时，自动收敛到 watchdog - 余量。"""
        monkeypatch.setattr(prov, "_LOCAL_TIMEOUT_CAP", 400)
        monkeypatch.setenv("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "120")
        effective = resolve_local_timeout(400)
        assert effective == 120 - prov._WATCHDOG_SAFETY_MARGIN
        assert effective < 120, "单次占用必须严格小于 watchdog，否则产生孤儿"

    def test_timeout_honors_raised_watchdog(self, monkeypatch):
        """watchdog 同步调大时，cap 生效（支持长文生成）。"""
        monkeypatch.setattr(prov, "_LOCAL_TIMEOUT_CAP", 400)
        monkeypatch.setenv("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "450")
        assert resolve_local_timeout(400) == 400

    def test_invariant_holds_for_arbitrary_configs(self, monkeypatch):
        """不变式全域校验：任意 (cap, watchdog) 组合下 effective < watchdog。"""
        for cap in (10, 100, 400, 5000):
            for wd in (30, 120, 450, 900):
                monkeypatch.setattr(prov, "_LOCAL_TIMEOUT_CAP", cap)
                monkeypatch.setenv("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", str(wd))
                assert resolve_local_timeout(cap) < wd, f"cap={cap} wd={wd} 破坏不变式"
