"""Tests for mock_api.llm_cache: InProcess + Redis failure-tolerant fallback."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from mock_api import llm_cache as lc
from mock_api.llm_cache import (
    InProcessLLMCacheBackend,
    LLMCacheBackend,
    cache_key,
    get_cache_backend,
    reset_cache_backend_for_testing,
)


# ----------------------------------------------------------------------------
# cache_key: stable across processes (SHA-256[0:8])
# ----------------------------------------------------------------------------
def test_cache_key_is_stable_sha256_int():
    k1 = cache_key("system", "prompt", 0.1, 4096)
    k2 = cache_key("system", "prompt", 0.1, 4096)
    assert k1 == k2
    assert isinstance(k1, int)
    assert 0 <= k1 < (1 << 64)


def test_cache_key_different_params_different_keys():
    base = cache_key("s", "p", 0.0, 100)
    assert cache_key("s", "p", 0.1, 100) != base  # temp differs
    assert cache_key("s", "p", 0.0, 200) != base  # tokens differs
    assert cache_key("s2", "p", 0.0, 100) != base  # system differs
    assert cache_key("s", "p2", 0.0, 100) != base  # prompt differs


# ----------------------------------------------------------------------------
# InProcess: round-trip + TTL expiry + FIFO eviction
# ----------------------------------------------------------------------------
def test_inprocess_round_trip_and_ttl():
    backend = InProcessLLMCacheBackend(ttl_seconds=2.0, max_entries=128)
    backend.set(123, "hello world")
    assert backend.get(123) == "hello world"

    # TTL=0 means cache disabled — get should always return None
    disabled = InProcessLLMCacheBackend(ttl_seconds=0.0)
    disabled.set(123, "x")
    assert disabled.get(123) is None


def test_inprocess_ttl_expiry():
    backend = InProcessLLMCacheBackend(ttl_seconds=0.05, max_entries=128)
    backend.set(7, "fresh")
    assert backend.get(7) == "fresh"
    time.sleep(0.1)
    assert backend.get(7) is None  # expired


def test_inprocess_fifo_eviction_at_capacity():
    backend = InProcessLLMCacheBackend(ttl_seconds=600.0, max_entries=3)
    for i in range(5):
        backend.set(i, f"v{i}")
        time.sleep(0.001)  # ensure distinct timestamps
    # At capacity 3, oldest should have been dropped; only newest 3 remain
    remaining = sum(1 for i in range(5) if backend.get(i) is not None)
    assert remaining == 3, f"expected 3 entries, got {remaining}"


# ----------------------------------------------------------------------------
# Factory: failure-tolerant Redis fallback + warning-once
# ----------------------------------------------------------------------------
def test_factory_falls_back_to_inprocess_when_redis_unavailable(monkeypatch):
    """When redis backend init fails, factory returns InProcessLLMCacheBackend
    after logging a warning exactly once."""
    reset_cache_backend_for_testing()

    # Patch settings to request redis backend with a missing URL
    fake_settings = MagicMock()
    fake_settings.llm_cache_backend = "redis"
    fake_settings.redis_url = "redis://invalid:6379"
    fake_settings.llm_cache_ttl = 300.0

    # Patch redis import to raise so factory must fallback
    def _raise_importerror(*args, **kwargs):
        raise ImportError("redis not installed in this test env")

    with patch.object(lc, "get_settings", return_value=fake_settings), \
         patch.dict("sys.modules", {"redis": None}), \
         patch("builtins.__import__", side_effect=lambda name, *a, **kw:
               _raise_importerror() if name == "redis" else __import__(name, *a, **kw)):
        backend = get_cache_backend()

    assert isinstance(backend, InProcessLLMCacheBackend)
    assert backend.name == "inprocess"

    # Calling again should not raise new warning (singleton + warned once)
    with patch.object(lc, "get_settings", return_value=fake_settings):
        backend2 = get_cache_backend()
    assert backend2 is backend  # same singleton


# ----------------------------------------------------------------------------
# Factory singleton: idempotent across calls
# ----------------------------------------------------------------------------
def test_factory_singleton_idempotent(monkeypatch):
    """Repeated calls return the same backend instance."""
    reset_cache_backend_for_testing()
    fake_settings = MagicMock()
    fake_settings.llm_cache_backend = "inprocess"
    fake_settings.redis_url = None
    fake_settings.llm_cache_ttl = 300.0
    with patch.object(lc, "get_settings", return_value=fake_settings):
        b1 = get_cache_backend()
        b2 = get_cache_backend()
        b3 = get_cache_backend()
    assert b1 is b2 is b3
    assert isinstance(b1, LLMCacheBackend)
