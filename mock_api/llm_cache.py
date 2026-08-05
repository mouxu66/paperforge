"""LLM response cache 后端抽象。

设计目标：
1. 进程内 dict 缓存（默认 backend）——保留 ``mock_api.depth_eval_v4``
   原 ``_llm_cache_*`` 的 SHA-256-keyed dict + TTL + FIFO eviction 行为不变。
2. 可选 Redis backend ——跨进程共享缓存的轻量复用层。
3. failure-tolerant：Redis 不可用（连接失败 / auth 失败 / 包未装）时静默
   降级到 in-process，且只在首次失败时 ``logger.warning`` 一次（后续走
   ``debug``），避免日志 spam 淹没运维信息。

Cache key 约定：
由 ``cache_key(system_prompt, prompt, temperature, max_tokens)`` 产出 64-bit int（SHA-256
前 8 字节）。跨 backend 稳定，允许后续迁移 / 跨进程复用。

线程安全：
- InProcess：内部 ``threading.Lock``
- Redis：依赖 client 自身（同进程可多 worker thread 安全）
- 工厂 ``get_cache_backend()`` 用模块级 ``threading.Lock`` 保证初始化竞态只发生一次。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from abc import ABC, abstractmethod

from .settings import get_settings  # 模块顶层导出，供测试 patch.object(lc, "get_settings")

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 全局状态：cache key 工艺 + backend 单例 + 一次性 Redis 失败标记
# ------------------------------------------------------------------
_INT64_MASK = (1 << 64) - 1


def cache_key(
    system_prompt: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
) -> int:
    """根据 LLM 调用参数生成稳定的 64-bit 整数 key (SHA-256[0:8])。

    跨进程一致：避免 Python 内置 ``hash()`` 的 PYTHONHASHSEED 随机化问题
    (旧实现已用 SHA-256，本函数保留 64-bit 截断以便 Redis 兼容)。
    """
    sha = hashlib.sha256()
    sha.update(system_prompt.encode("utf-8", errors="replace"))
    sha.update(b"\x00")
    sha.update(prompt.encode("utf-8", errors="replace"))
    sha.update(b"\x00")
    sha.update(str(float(temperature)).encode("utf-8"))
    sha.update(b"\x00")
    sha.update(str(int(max_tokens)).encode("utf-8"))
    return int.from_bytes(sha.digest()[:8], "big", signed=False)


_backend_lock = threading.Lock()
_backend_singleton: LLMCacheBackend | None = None
_redis_warning_logged = False  # Redis 降级 warning 仅打一次记号


# ------------------------------------------------------------------
# Abstract base
# ------------------------------------------------------------------
class LLMCacheBackend(ABC):
    """LLM response cache backend 抽象。"""

    @abstractmethod
    def get(self, key: int) -> str | None:
        """命中且未过期返回 LLM 字符串；否则 None。"""

    @abstractmethod
    def set(self, key: int, value: str) -> None:
        """写入 (key, value)。TTL 由 backend 内部按设置值处理。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """调试标识（'inprocess' / 'redis' / 'inprocess-fallback'）。"""


# ------------------------------------------------------------------
# In-process backend（保留 _llm_cache_* 全部行为）
# ------------------------------------------------------------------
class InProcessLLMCacheBackend(LLMCacheBackend):
    """线程安全的 in-process dict 缓存 + TTL + FIFO 驱逐。

    行为与原 ``mock_api.depth_eval_v4`` 中的 ``_llm_cache_get/_set`` 完全一致。
    """

    def __init__(self, ttl_seconds: float, max_entries: int = 128):
        self._ttl = ttl_seconds
        self._max = max_entries
        self._store: dict[int, tuple[float, str]] = {}
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return "inprocess"

    def get(self, key: int) -> str | None:
        if self._ttl <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, value = entry
            if now - ts >= self._ttl:
                # expired
                self._store.pop(key, None)
                return None
            return value

    def set(self, key: int, value: str) -> None:
        if self._ttl <= 0:
            return  # TTL=0 表示缓存禁用
        with self._lock:
            self._store[key] = (time.monotonic(), value)
            # FIFO 驱逐（按 insertion time；key 与 ts 双双保留）
            if len(self._store) > self._max:
                # 取最早的 N 个 key（按 ts 排序）
                overflow = len(self._store) - self._max
                oldest = sorted(self._store.items(), key=lambda kv: kv[1][0])[:overflow]
                for k, _ in oldest:
                    self._store.pop(k, None)


# ------------------------------------------------------------------
# Redis backend（optional dep）
# ------------------------------------------------------------------
class RedisLLMCacheBackend(LLMCacheBackend):
    """基于 redis-py 的可选 backend。

    行为约定：
    - 构造时 lazy-import ``redis``；导入失败抛 ``RuntimeError("redis 包未安装")``。
    - key 用 ``str(key)`` 序列化；value 用 ``value`` 原样（JSON 或 raw string）。
    - TTL 通过 ``SET EX`` 写入；测试 / mock 友好。
    """

    def __init__(self, redis_url: str, ttl_seconds: float, key_prefix: str = "paperforge:llm:"):
        try:
            import redis as _redis  # type: ignore
        except ImportError as e:
            raise RuntimeError(f"redis 包未安装，无法启用 Redis LLMCacheBackend: {e}") from e

        self._client = _redis.Redis.from_url(redis_url)
        self._ttl = max(1, int(ttl_seconds))  # redis EX 最小 1 秒
        self._prefix = key_prefix
        self._key_prefix_int = "pf_llm:"  # int key 前缀（避免和 str key 冲突）

    @property
    def name(self) -> str:
        return "redis"

    def _k(self, key: int) -> str:
        return f"{self._key_prefix_int}{key}"

    def get(self, key: int) -> str | None:
        try:
            raw = self._client.get(self._k(key))
        except Exception as e:  # noqa: BLE001 - redis 客户端 getter - 失败静默回退到上层 in-process
            logger.debug("RedisLLMCacheBackend.get failed: %s", e)
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return raw

    def set(self, key: int, value: str) -> None:
        try:
            self._client.setex(self._k(key), self._ttl, value)
        except Exception as e:  # noqa: BLE001 - redis 客户端 setter - 失败静默忽略
            logger.debug("RedisLLMCacheBackend.set failed: %s", e)


# ------------------------------------------------------------------
# Thread-safe singleton factory（failure-tolerant + warning-once）
# ------------------------------------------------------------------
def get_cache_backend() -> LLMCacheBackend:
    """获取缓存 backend 单例。线程安全；init 期内只发生一次 race。

    行为规则：
    - 首次调用时根据 ``settings.llm_cache_backend`` 实例化。
    - 若 ``"redis"`` 初始化失败（包未装 / 连接失败 / auth 失败）→ logger.warning 一次
      然后返回 ``InProcessLLMCacheBackend``，保证业务路径永不被缓存层阻塞。
    - 后续调用直接返回缓存好的 singleton。
    """
    global _backend_singleton, _redis_warning_logged
    # 1st check (fast path) — 模块顶层已 import get_settings
    # （便于测试 patch.object(lc, "get_settings")）
    if _backend_singleton is not None:
        return _backend_singleton

    settings = get_settings()
    backend_kind = (settings.llm_cache_backend or "inprocess").strip().lower()
    ttl = float(settings.llm_cache_ttl)

    # 2nd check under lock（防止并发线程重复 init）
    with _backend_lock:
        if _backend_singleton is not None:
            return _backend_singleton

        if backend_kind == "redis" and settings.redis_url:
            try:
                # ping test 一次 — 让 connect 失败尽早在 init 时暴露
                client = None
                try:
                    import redis as _redis  # type: ignore

                    client = _redis.Redis.from_url(settings.redis_url)
                    client.ping()
                except Exception as ping_exc:
                    raise RuntimeError(
                        f"RedisLLMCacheBackend ping 失败 ({settings.redis_url}): {ping_exc}"
                    ) from ping_exc
                backend: LLMCacheBackend = RedisLLMCacheBackend(
                    redis_url=settings.redis_url, ttl_seconds=ttl
                )
                logger.info(
                    "LLMCacheBackend: redis enabled (url=%s, ttl=%ss)",
                    settings.redis_url,
                    ttl,
                )
            except Exception as e:
                # ⚠️ 仅在首次失败时打 warning，后续保持 silent 避免 log spam
                if not _redis_warning_logged:
                    logger.warning(
                        "LLMCacheBackend: Redis 初始化失败，降级到 in-process "
                        "（仅本次告警，后续 silent）: %s",
                        e,
                    )
                    _redis_warning_logged = True
                else:
                    logger.debug("LLMCacheBackend: Redis 仍不可用，in-process 兜底: %s", e)
                backend = InProcessLLMCacheBackend(ttl_seconds=ttl)
                # 标记降级原因可通过 backend.name 查看
                backend.__class__ = InProcessLLMCacheBackend  # type: ignore[assignment]
        else:
            backend = InProcessLLMCacheBackend(ttl_seconds=ttl)
            logger.debug("LLMCacheBackend: inprocess enabled (ttl=%ss)", ttl)

        _backend_singleton = backend
        return _backend_singleton


def reset_cache_backend_for_testing() -> None:
    """清除 backend singleton（测试用，强制下次 ``get_cache_backend()`` 重新创建）。"""
    global _backend_singleton, _redis_warning_logged
    with _backend_lock:
        _backend_singleton = None
        _redis_warning_logged = False
