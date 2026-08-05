"""per-key 令牌桶限流器（Layer 2：防滥用）。

设计：
- 内存令牌桶（进程级），不需要 Redis
- 每个 key_id 独立桶，容量 = rate_limit_per_min，填充速率 = capacity/60 (tokens/sec)
- 超限时返回 429 + Retry-After 头
- 超级管理员（global / loopback）不限流

局限性：
- 多 worker 部署时每个 worker 独立计数（per-worker 限流）
- 重启后桶清空（可接受：限流是短期行为）
- 若需精确分布式限流，可改用 Redis（settings.llm_cache_backend=redis 已预留）
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class _TokenBucket:
    """令牌桶（线程安全）。"""

    capacity: float  # 桶容量（= 每分钟允许的请求数）
    tokens: float = 0.0  # 当前令牌数
    last_refill: float = field(default_factory=time.monotonic)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.tokens = self.capacity  # 初始满桶

    def consume(self, tokens: float = 1.0) -> tuple[bool, float]:
        """尝试消费令牌。

        Returns:
            (allowed, retry_after_seconds)
            - allowed=True 表示放行
            - allowed=False 表示限流，retry_after_seconds 建议等待时间
        """
        with self._lock:
            now = time.monotonic()
            # 按时间填充令牌：fill_rate = capacity / 60 tokens/sec
            elapsed = now - self.last_refill
            fill_rate = self.capacity / 60.0 if self.capacity > 0 else 0
            self.tokens = min(self.capacity, self.tokens + elapsed * fill_rate)
            self.last_refill = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True, 0.0
            # 不足：计算需要等待多久才能积累 1 个令牌
            needed = tokens - self.tokens
            retry_after = needed / fill_rate if fill_rate > 0 else 1.0
            return False, retry_after


class RateLimiter:
    """进程级限流管理器（per-key 令牌桶）。

    桶在首次请求时按 key 创建，按 key 的 rate_limit_per_min 设置容量。
    系统默认速率由 settings.api_key_rate_limit_default 控制。
    """

    def __init__(self) -> None:
        self._buckets: dict[str, _TokenBucket] = {}
        self._lock = threading.Lock()

    def _get_or_create_bucket(self, key_id: str, rate_limit_per_min: int) -> _TokenBucket:
        """获取或创建某 key 的令牌桶。"""
        with self._lock:
            bucket = self._buckets.get(key_id)
            if bucket is None or bucket.capacity != rate_limit_per_min:
                # 容量变更（admin 更新了限流配置）→ 重建桶
                bucket = _TokenBucket(capacity=float(rate_limit_per_min))
                self._buckets[key_id] = bucket
            return bucket

    def check(
        self, key_id: str, rate_limit_per_min: int, is_admin: bool = False
    ) -> tuple[bool, int]:
        """检查是否允许请求。

        Args:
            key_id: 调用方标识
            rate_limit_per_min: 每分钟请求上限（<=0 时继承系统默认）
            is_admin: 超级管理员不限流

        Returns:
            (allowed, retry_after_seconds)
        """
        # 超级管理员 → 直接放行
        if is_admin:
            return True, 0

        # <=0 时继承系统默认，避免 0 被误配成无限流
        if rate_limit_per_min <= 0:
            from .settings import get_settings

            rate_limit_per_min = get_settings().api_key_rate_limit_default
            if rate_limit_per_min <= 0:
                # 兜底：默认配置异常时仍按 60 限制，并打 warning 提醒运维
                logger.warning(
                    "api_key_rate_limit_default=%s 无效，回退到 60/min",
                    rate_limit_per_min,
                )
                rate_limit_per_min = 60

        bucket = self._get_or_create_bucket(key_id, rate_limit_per_min)
        allowed, retry_after = bucket.consume()
        return allowed, int(retry_after) + 1  # 向上取整 + 1 秒余量


# ---------------------------------------------------------------------------
# 进程级单例
# ---------------------------------------------------------------------------
_limiter_instance: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    """获取进程级 RateLimiter 单例。"""
    global _limiter_instance
    if _limiter_instance is None:
        _limiter_instance = RateLimiter()
    return _limiter_instance
