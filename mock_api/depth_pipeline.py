"""
DEPTH DAG 流水线引擎 —— 通用的有向无环图（DAG）并行执行框架。

设计原则：
- 节点定义与执行逻辑解耦：每个节点声明名称、依赖、执行器
- 拓扑级并行：同一"波"（wave）内的无依赖节点通过 asyncio.gather 并发
- 严格错误传播：任一节点失败立即取消所有下游节点，快速失败
- 执行计时：每个节点记录开始/结束时间与耗时，用于性能监控
- 可被 DepthReviewer 或其他流水线直接复用

依赖图（DEPTH v4.2 默认拓扑：Q234 合并维度评分 + QF 图文一致性）：
    Q0 ──┐
         ├── QE ──┬── Q234 ──┐
    Q1 ──┘        └── QF ────┼── Q5a ── Q5b ── Q5c

legacy 拓扑（merged_dimensions=False, include_figure_node=False，v4.2 九节点回退）：
    Q0 ──┐
         ├── QE ──┬── Q2 ──┐
    Q1 ──┘        ├── Q3 ──┼── Q5a ── Q5b ── Q5c
                  └── Q4 ──┘
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from .depth_models import DAGOutputs, NodeOutput

logger = logging.getLogger(__name__)


# v4.2: CircuitBreaker delay-import helper (avoids circular imports with depth_eval_v4)
def _get_circuit_breaker(name: str) -> Any | None:
    try:
        from .circuit_breaker import get_circuit_breaker

        return get_circuit_breaker(name)
    except Exception:
        return None


T = TypeVar("T")


# =============================================================================
# 数据结构
# =============================================================================


@dataclass
class DAGNode:
    """流水线中的一个节点。

    Attributes:
        name: 节点名称（如 "Q0", "QE"）。
        dependencies: 依赖的节点名称列表（空表示无依赖）。
        executor: 异步可调用函数，签名为 async def fn(results: dict[str, Any]) -> Any。
                   results 是已完成上游节点的 {name: output} 字典。
        timeout: 单个节点的超时秒数（None 表示不限）。
    """

    name: str
    dependencies: list[str] = field(default_factory=list)
    executor: Callable[..., Awaitable[Any]] | None = None
    timeout: float | None = None


@dataclass
class NodeResult(Generic[T]):
    """单个节点的执行结果（v4.2: 泛型化，output 类型由 T 约束）。"""

    name: str
    status: str = "pending"  # pending | running | completed | failed | cancelled
    output: T | None = None
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0

    @property
    def elapsed(self) -> float:
        return self.finished_at - self.started_at if self.finished_at else 0.0


@dataclass
class PipelineResult:
    """流水线执行结果（v4.2: 携带类型化的 DAGOutputs）。"""

    success: bool
    nodes: dict[str, NodeResult[Any]]
    outputs: DAGOutputs = field(default_factory=DAGOutputs)
    total_elapsed: float = 0.0
    error: str = ""


# =============================================================================
# DAG 流水线引擎
# =============================================================================


class DepthDAG:
    """DEPTH DAG 流水线执行引擎。

    用法：
        dag = DepthDAG(nodes=[...], name="DEPTH v4.2")
        result = await dag.execute()

    execute() 自动：
    1. 校验 DAG 无环、所有依赖存在
    2. 拓扑分组：同一波（wave）的并行节点通过 asyncio.gather 执行
    3. 任一节点失败 → 取消所有下游 → 快速失败
    4. 每个节点记录执行计时
    """

    def __init__(self, nodes: list[DAGNode], name: str = "DAG"):
        self._nodes = {n.name: n for n in nodes}
        self._name = name
        self._results: dict[str, NodeResult[Any]] = {}
        self._outputs: DAGOutputs = DAGOutputs()
        self._running_tasks: dict[str, asyncio.Task[Any]] = {}  # 正在运行的子任务引用

    # ------------------------------------------------------------------
    # 拓扑校验
    # ------------------------------------------------------------------
    def validate(self) -> tuple[bool, str]:
        """验证 DAG 合法性：无环、依赖存在、执行器非空。

        Returns:
            (is_valid, error_message)。
        """
        # 检查所有依赖节点存在
        for node in self._nodes.values():
            for dep in node.dependencies:
                if dep not in self._nodes:
                    return False, f"节点 '{node.name}' 依赖 '{dep}'，但 '{dep}' 未定义"
            if node.executor is None:
                return False, f"节点 '{node.name}' 未设置 executor"

        # 环检测（DFS）
        WHITE, GRAY, BLACK = 0, 1, 2
        colors: dict[str, int] = dict.fromkeys(self._nodes, WHITE)

        def _dfs(name: str) -> bool:
            colors[name] = GRAY
            for dep in self._nodes[name].dependencies:
                if colors[dep] == GRAY:
                    return False
                if colors[dep] == WHITE and not _dfs(dep):
                    return False
            colors[name] = BLACK
            return True

        for name in self._nodes:
            if colors[name] == WHITE:
                if not _dfs(name):
                    return False, "DAG 中存在循环依赖"

        return True, ""

    # ------------------------------------------------------------------
    # 主执行入口
    # ------------------------------------------------------------------
    async def execute(self) -> PipelineResult:
        """执行 DAG 流水线，返回 PipelineResult。

        Raises:
            ValueError: DAG 校验失败。
        """
        valid, error = self.validate()
        if not valid:
            raise ValueError(f"DAG 校验失败: {error}")

        self._results = {name: NodeResult(name=name) for name in self._nodes}
        self._outputs = DAGOutputs()
        self._running_tasks = {}

        started = time.monotonic()

        try:
            await self._execute_dag()
            success = all(r.status == "completed" for r in self._results.values())
            return PipelineResult(
                success=success,
                nodes=dict(self._results),
                outputs=self._outputs,
                total_elapsed=time.monotonic() - started,
            )
        except asyncio.CancelledError:
            # 流水线被外部取消：先终止所有正在运行的子任务，再标记结果
            logger.warning(
                "DAG 流水线 [%s] 被取消，终止 %d 个子任务", self._name, len(self._running_tasks)
            )
            for name, task in list(self._running_tasks.items()):
                if not task.done():
                    task.cancel()
            # 等待子任务完成取消（最多 5s）
            if self._running_tasks:
                await asyncio.wait(
                    list(self._running_tasks.values()),
                    timeout=5,
                )
            for name in self._nodes:
                if self._results[name].status in ("running", "pending"):
                    self._results[name].status = "cancelled"
                    self._results[name].error = "流水线外部取消"
            return PipelineResult(
                success=False,
                nodes=dict(self._results),
                outputs=self._outputs,
                total_elapsed=time.monotonic() - started,
                error="流水线被取消",
            )
        except Exception as e:  # noqa: BLE001 - DAG 节点 - 单节点异常需隔离不传播
            logger.exception("DAG 流水线 [%s] 执行异常", self._name)
            return PipelineResult(
                success=False,
                nodes=dict(self._results),
                outputs=self._outputs,
                total_elapsed=time.monotonic() - started,
                error=str(e),
            )
        finally:
            self._running_tasks = {}

    # ------------------------------------------------------------------
    # 核心 DAG 执行逻辑
    # ------------------------------------------------------------------
    async def _execute_dag(self) -> None:
        """按拓扑波次执行所有节点。"""
        # 计算拓扑波次：每轮找出所有依赖已满足的节点
        remaining: set[str] = set(self._nodes.keys())
        round_num = 0

        while remaining:
            round_num += 1
            # 找出本轮可执行节点：所有依赖已完成的节点
            ready = [
                name
                for name in remaining
                if all(
                    self._results[dep].status == "completed"
                    for dep in self._nodes[name].dependencies
                )
            ]

            if not ready:
                # 剩余节点全部因上游失败被取消 → 退出
                cancelled = {
                    name for name in remaining if self._results[name].status in ("cancelled",)
                }
                remaining -= cancelled
                if not remaining:
                    break
                # 仍有剩余但无法推进 → 逻辑错误，记录后退出
                logger.error(
                    "DAG [%s] 波次 %d 无法推进，剩余: %s",
                    self._name,
                    round_num,
                    remaining,
                )
                break

            logger.info(
                "DAG [%s] 波次 %d: 并行执行 %s",
                self._name,
                round_num,
                ready,
            )

            # 并行执行本轮所有就绪节点
            tasks = {
                name: asyncio.create_task(
                    self._run_node(name, self._nodes[name]),
                    name=f"{self._name}.{name}",
                )
                for name in ready
            }
            # 注册到运行任务表（用于外部取消时终止）
            self._running_tasks.update(tasks)

            # 等待所有任务完成（任一失败不阻塞其他节点完成，但触发下游 cancel）
            for name, task in tasks.items():
                try:
                    await task
                except asyncio.CancelledError:
                    self._results[name].status = "cancelled"
                    self._results[name].error = "任务被取消"
                    logger.warning("DAG [%s] 节点 %s 被取消", self._name, name)
                except Exception as e:  # noqa: BLE001 - DAG 节点 - 单节点异常需隔离不传播
                    self._results[name].status = "failed"
                    self._results[name].error = str(e)
                    self._results[name].finished_at = time.monotonic()
                    logger.error(
                        "DAG [%s] 节点 %s 执行失败: %s",
                        self._name,
                        name,
                        e,
                    )

            # 清理本轮任务引用
            for name in ready:
                self._running_tasks.pop(name, None)

            # 从剩余集合移除本轮已完成的节点
            remaining -= set(ready)

            # 检查失败传播：取消所有依赖失败节点的下游节点
            failed_this_round = {name for name in ready if self._results[name].status == "failed"}
            if failed_this_round:
                self._cancel_downstream(failed_this_round, remaining)

    async def _run_node(self, name: str, node: DAGNode) -> None:
        """执行单个节点：计时 + 异常处理 + v4.2 熔断器集成。

        v4.2: 对 Q234/Q2/Q3/Q4 维度评分节点启用熔断器。当节点连续失败
        超过阈值（默认 5 次），CircuitBreaker 自动跳闸 OPEN，跳过该节点
        并使用 OIM 客观特征层保底，防止下游雪崩。
        """
        result = self._results[name]
        result.status = "running"
        result.started_at = time.monotonic()
        logger.info("DAG [%s] 节点 %s 开始执行", self._name, name)

        assert node.executor is not None, f"节点 {name} 未设置 executor"

        # ── v4.2 熔断器检查 ────────────────────────────────────────
        cb = _get_circuit_breaker(name) if name in ("Q234", "Q2", "Q3", "Q4") else None
        if cb is not None and not cb.allow_request():
            # 设置 completed 状态（而非 cancelled）：下游 Q5a 依赖本节点，
            # cancelled 会导致 DAG 永久卡住。output=None 让 Q5a 走 OIM 客观特征保底。
            result.status = "completed"
            result.output = None
            result.error = (
                f"熔断器已开路（连续失败 ≥{cb.stats().failure_count} 次），跳过节点，使用 OIM 保底"
            )
            result.finished_at = time.monotonic()
            setattr(self._outputs, name, None)
            logger.warning(
                "DAG [%s] 节点 %s 被熔断器跳过（output=None → OIM fallback）", self._name, name
            )
            return

        try:
            if node.timeout:
                output = await asyncio.wait_for(
                    node.executor(self._outputs),
                    timeout=node.timeout,
                )
            else:
                output = await node.executor(self._outputs)

            # ── v4.3: NodeOutput-aware circuit breaking ───────────────
            # Executor may return a NodeOutput wrapper; inspect .status
            # and handle failure gracefully instead of relying on exceptions.
            if isinstance(output, NodeOutput):
                if output.status != "success":
                    result.status = "failed"
                    result.error = output.error or "Node returned non-success status"
                    result.finished_at = time.monotonic()
                    if cb is not None:
                        cb.on_failure()
                    logger.error(
                        "DAG [%s] 节点 %s 返回失败状态: %s",
                        self._name,
                        name,
                        result.error,
                    )
                    return  # Graceful — downstream cancellation by wave loop
                # Unwrap payload for downstream consumption
                output = output.data

            result.status = "completed"
            result.output = output
            setattr(self._outputs, name, output)
            result.finished_at = time.monotonic()
            if cb is not None:
                cb.on_success()
            logger.info(
                "DAG [%s] 节点 %s 完成 (%.2fs)",
                self._name,
                name,
                result.elapsed,
            )
        except asyncio.TimeoutError:
            result.status = "failed"
            result.error = f"节点超时 ({node.timeout}s)"
            result.finished_at = time.monotonic()
            if cb is not None:
                cb.on_failure()
            logger.error("DAG [%s] 节点 %s 超时", self._name, name)
            raise
        except asyncio.CancelledError:
            result.status = "cancelled"
            result.finished_at = time.monotonic()
            raise
        except Exception as e:  # noqa: BLE001 - DAG 节点 - 单节点异常需隔离不传播
            result.status = "failed"
            result.error = str(e)
            result.finished_at = time.monotonic()
            if cb is not None:
                cb.on_failure()
            logger.exception("DAG [%s] 节点 %s 异常", self._name, name)
            raise

    def _cancel_downstream(
        self,
        failed_nodes: set[str],
        remaining: set[str],
    ) -> None:
        """递归取消所有依赖失败节点的下游节点，并直接从 remaining 中移除。

        使用 BFS：从失败节点出发，沿依赖反向图扩散。
        """
        # 构建反向依赖映射：node → 直接下游节点列表
        downstream: dict[str, set[str]] = {name: set() for name in self._nodes}
        for name, node in self._nodes.items():
            for dep in node.dependencies:
                downstream[dep].add(name)

        # BFS 扩散并移除
        from collections import deque

        queue = deque(failed_nodes)
        while queue:
            fn = queue.popleft()
            for ds in downstream.get(fn, set()):
                if ds in remaining:
                    self._results[ds].status = "cancelled"
                    self._results[ds].error = "上游节点失败，当前节点被跳过"
                    remaining.discard(ds)
                    queue.append(ds)
                    logger.warning(
                        "DAG [%s] 节点 %s 因上游失败被取消",
                        self._name,
                        ds,
                    )

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------
    def get_timings(self) -> dict[str, float]:
        """获取各节点执行耗时（秒）。"""
        return {name: r.elapsed for name, r in self._results.items() if r.status == "completed"}

    def get_failed_nodes(self) -> list[str]:
        """获取失败的节点名称列表。"""
        return [name for name, r in self._results.items() if r.status == "failed"]


# =============================================================================
# DEPTH v4.2 专用流水线构建器
# =============================================================================


def build_depth_dag(
    executor_factory: Callable[[str], Callable[[DAGOutputs], Awaitable[Any]]],
    node_timeout: float | None = 300,
    merged_dimensions: bool = True,
    include_figure_node: bool = True,
) -> DepthDAG:
    """构建 DEPTH 审稿 DAG（v4.2 起拓扑可配）。

    Args:
        executor_factory: 接受节点名（"Q0"/"Q1"/"QE"/"Q234"/"QF"/"Q5a"/"Q5b"/"Q5c"
            或 legacy 的 "Q2"/"Q3"/"Q4"），返回该节点的异步 executor 函数。
            executor 函数签名为 async def fn(results: dict[str, Any]) -> Any，
            其中 results 是已完成上游节点的 {name: output} 字典。
        node_timeout: 单节点超时秒数（None = 不限）。
        merged_dimensions: True 时用单个 Q234 节点替代 Q2/Q3/Q4 三节点（v4.2 降本）；
            False 回退 v4.2 三次独立调用拓扑。
        include_figure_node: True 时新增 QF 图文一致性节点（Wave3 与维度评分并行）。

    Returns:
        配置完成的 DepthDAG 实例。

    Raises:
        ValueError: executor_factory 返回的 executor 不可调用。
    """

    def _wrap(name: str) -> Callable[[DAGOutputs], Awaitable[Any]]:
        executor = executor_factory(name)
        if not callable(executor):
            raise ValueError(
                f"executor_factory('{name}') 必须返回可调用的 async 函数，"
                f"实际返回: {type(executor).__name__}"
            )
        return executor

    nodes = [
        # Wave 1: Q0 ∥ Q1（无依赖）
        DAGNode(name="Q0", dependencies=[], executor=_wrap("Q0"), timeout=node_timeout),
        DAGNode(name="Q1", dependencies=[], executor=_wrap("Q1"), timeout=node_timeout),
        # Wave 2: QE（依赖 Q0, Q1）
        DAGNode(name="QE", dependencies=["Q0", "Q1"], executor=_wrap("QE"), timeout=node_timeout),
    ]

    # Wave 3: 维度评分（合并或独立）∥ QF 图文一致性（可选）
    if merged_dimensions:
        nodes.append(
            DAGNode(
                name="Q234",
                dependencies=["QE", "Q1"],
                executor=_wrap("Q234"),
                timeout=node_timeout,
            )
        )
        dim_deps = ["Q234"]
    else:
        nodes.extend(
            [
                DAGNode(
                    name="Q2", dependencies=["QE", "Q1"], executor=_wrap("Q2"), timeout=node_timeout
                ),
                DAGNode(
                    name="Q3", dependencies=["QE", "Q1"], executor=_wrap("Q3"), timeout=node_timeout
                ),
                DAGNode(
                    name="Q4", dependencies=["QE", "Q1"], executor=_wrap("Q4"), timeout=node_timeout
                ),
            ]
        )
        dim_deps = ["Q2", "Q3", "Q4"]

    if include_figure_node:
        nodes.append(
            DAGNode(name="QF", dependencies=["QE"], executor=_wrap("QF"), timeout=node_timeout)
        )
        dim_deps.append("QF")

    nodes.extend(
        [
            # Wave 4: Q5a（依赖维度评分 + QF）
            DAGNode(name="Q5a", dependencies=dim_deps, executor=_wrap("Q5a"), timeout=node_timeout),
            # Wave 5: Q5b（依赖 Q5a）
            DAGNode(name="Q5b", dependencies=["Q5a"], executor=_wrap("Q5b"), timeout=node_timeout),
            # Wave 6: Q5c（依赖 Q5b）
            DAGNode(name="Q5c", dependencies=["Q5b"], executor=_wrap("Q5c"), timeout=node_timeout),
        ]
    )

    return DepthDAG(nodes=nodes, name="DEPTH v4.2")
