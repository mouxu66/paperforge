"""熔断器（Circuit Breaker）—— DEPTH v4.2 舱壁模式核心组件。

v4.2 新增（2026-07-27）：
- ``CircuitBreaker``: 标准三态熔断器（CLOSED / OPEN / HALF_OPEN）。
- 当目标节点连续失败超过阈值（默认 5 次），自动跳闸进入 OPEN 状态，
  跳过该节点并使用 OIM 客观特征层保底，防止下游雪崩。
- HALF_OPEN 超时后发送探测请求：成功 → CLOSED，失败 → 回到 OPEN。

设计原则：
- 线程安全（内部锁）。
- 零外部依赖（不引入额外库）。
- 可序列化状态，便于 /api/admin/depth/circuit 端点展示。
- 与 ``depth_pipeline.py`` DAG 引擎原生集成。

用法::

    cb = CircuitBreaker("Q234", failure_threshold=5, recovery_timeout=60)
    if not cb.allow_request():
        return fallback_oim_score()  # 使用 OIM 保底
    try:
        result = await call_q234()
        cb.on_success()
        return result
    except Exception:
        cb.on_failure()
        raise
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum


class CircuitState(str, Enum):
    """熔断器三态。"""

    CLOSED = "closed"  # 正常：请求通过
    OPEN = "open"  # 熔断：所有请求被拒绝，直接走保底
    HALF_OPEN = "half_open"  # 探测：允许一个请求通过以测试恢复


@dataclass
class CircuitStats:
    """熔断器对外统计快照。"""

    name: str
    state: CircuitState
    failure_count: int
    total_failures: int
    total_successes: int
    opened_at: float | None
    last_failure_at: float | None
    trip_count: int  # 累计跳闸次数


class CircuitBreaker:
    """三态熔断器：连续失败 ≥ N → OPEN → 超时后 HALF_OPEN → 探测成功 → CLOSED。

    线程安全，可用于 asyncio 环境（需在正确的事件循环上下文中调用）。
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        *,
        half_open_max: int = 1,
    ) -> None:
        """初始化熔断器。

        Args:
            name: 熔断器名称（如节点名 "Q234"），用于日志和统计。
            failure_threshold: CLOSED 状态下连续失败 N 次后 OPEN。
            recovery_timeout: OPEN 后等待多少秒进入 HALF_OPEN。
            half_open_max: HALF_OPEN 状态允许通过的探测请求数（默认 1）。
        """
        if failure_threshold < 1:
            raise ValueError(f"failure_threshold 必须 ≥ 1，收到: {failure_threshold}")
        if recovery_timeout < 0:
            raise ValueError(f"recovery_timeout 必须 ≥ 0，收到: {recovery_timeout}")

        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._half_open_max = half_open_max

        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._half_open_count: int = 0
        self._total_failures: int = 0
        self._total_successes: int = 0
        self._trip_count: int = 0
        self._opened_at: float | None = None
        self._last_failure_at: float | None = None
        self._lock = threading.Lock()

    # ── 公共 API ──────────────────────────────────────────────────

    def allow_request(self) -> bool:
        """检查当前请求是否允许通过。

        Returns:
            True: 请求可以通过（CLOSED 或 HALF_OPEN 探测）。
            False: 熔断中，应走保底逻辑。
        """
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.OPEN:
                if self._should_transition_to_half_open():
                    self._transition_to(CircuitState.HALF_OPEN)
                    self._half_open_count = 0
                    return True
                return False
            # HALF_OPEN: 最多允许 half_open_max 个探测请求
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_count < self._half_open_max:
                    self._half_open_count += 1
                    return True
                return False
            return False

    def on_success(self) -> None:
        """请求成功后回调。"""
        with self._lock:
            self._total_successes += 1
            if self._state == CircuitState.HALF_OPEN:
                self._transition_to(CircuitState.CLOSED)
            # CLOSED 状态下成功：重置连续失败计数
            self._failure_count = 0

    def on_failure(self) -> None:
        """请求失败后回调。"""
        now = time.monotonic()
        with self._lock:
            self._total_failures += 1
            self._last_failure_at = now
            if self._state == CircuitState.HALF_OPEN:
                # 探测失败 → 立即回到 OPEN
                self._transition_to(CircuitState.OPEN)
                return
            if self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._failure_threshold:
                    self._transition_to(CircuitState.OPEN)

    def reset(self) -> None:
        """强制重置熔断器到 CLOSED 状态（管理接口）。"""
        with self._lock:
            self._transition_to(CircuitState.CLOSED)
            self._failure_count = 0

    def force_open(self) -> None:
        """强制跳闸（管理接口，如手动维护）。"""
        with self._lock:
            self._transition_to(CircuitState.OPEN)

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def stats(self) -> CircuitStats:
        """返回熔断器统计快照。"""
        with self._lock:
            return CircuitStats(
                name=self.name,
                state=self._state,
                failure_count=self._failure_count,
                total_failures=self._total_failures,
                total_successes=self._total_successes,
                opened_at=self._opened_at,
                last_failure_at=self._last_failure_at,
                trip_count=self._trip_count,
            )

    # ── 内部方法 ──────────────────────────────────────────────────

    def _should_transition_to_half_open(self) -> bool:
        """OPEN → HALF_OPEN 超时检测。"""
        if self._opened_at is None:
            return True
        elapsed = time.monotonic() - self._opened_at
        return elapsed >= self._recovery_timeout

    def _transition_to(self, new_state: CircuitState) -> None:
        """状态迁移（调用方已持有锁）。"""
        old = self._state  # noqa: F841
        self._state = new_state
        if new_state == CircuitState.OPEN:
            self._opened_at = time.monotonic()
            self._trip_count += 1
        elif new_state == CircuitState.CLOSED:
            self._failure_count = 0
            self._opened_at = None
        # HALF_OPEN: reset half_open_count（由 allow_request 管理）


# ── 全局熔断器注册表（按 DAG 节点名索引）─────────────────────────
_registry: dict[str, CircuitBreaker] = {}
_registry_lock = threading.Lock()


def get_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
) -> CircuitBreaker:
    """获取或创建指定名称的熔断器（单例注册表）。

    多个调用方共享同一名称的熔断器，确保全局一致性。

    Args:
        name: 熔断器名称（如 "Q234"）。
        failure_threshold: 首次创建时的阈值（已存在则忽略）。
        recovery_timeout: 首次创建时的恢复超时（已存在则忽略）。

    Returns:
        CircuitBreaker 实例。
    """
    with _registry_lock:
        if name not in _registry:
            _registry[name] = CircuitBreaker(
                name=name,
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
            )
        return _registry[name]


def get_all_circuit_stats() -> list[CircuitStats]:
    """获取所有注册熔断器的统计快照。"""
    with _registry_lock:
        return [cb.stats() for cb in _registry.values()]


def reset_all_circuits() -> None:
    """重置所有熔断器（管理接口）。"""
    with _registry_lock:
        for cb in _registry.values():
            cb.reset()
