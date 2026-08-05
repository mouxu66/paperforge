"""
DEPTH DAG 流水线单元测试 —— DepthDAG 引擎 + review_async_dag 集成。

运行方式：
    cd mock_api && python -m pytest tests/test_depth_dag.py -v -s
或直接：
    python -m pytest tests/test_depth_dag.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mock_api.depth_eval_v4 import (
    DepthReviewer,
)
from mock_api.depth_pipeline import (
    DAGNode,
    DepthDAG,
    NodeResult,
    PipelineResult,
    build_depth_dag,
)

# 复用 test_depth_v4 的 Mock LLM 和论文数据
from tests.test_depth_v4 import (
    MOCK_ABSTRACT,
    MOCK_FULL_TEXT,
    MOCK_PAPER_ID,
    MOCK_TITLE,
    MockLLM,
)


# ===========================================================================
# 1. DAG 校验层测试（纯 DepthDAG，无 LLM 依赖）
# ===========================================================================
class TestDAGValidation:
    """测试 DAG 拓扑校验：无环检测、依赖存在性、executor 非空。"""

    def test_valid_dag_passes_validation(self):
        """有效 DAG 通过校验。"""
        dag = DepthDAG(nodes=[
            DAGNode(name="A", dependencies=[],
                    executor=async_noop),
            DAGNode(name="B", dependencies=["A"],
                    executor=async_noop),
        ])
        ok, err = dag.validate()
        assert ok, f"校验失败: {err}"

    def test_missing_dependency_fails(self):
        """依赖不存在的节点 → 校验失败。"""
        dag = DepthDAG(nodes=[
            DAGNode(name="A", dependencies=["NONEXISTENT"],
                    executor=async_noop),
        ])
        ok, err = dag.validate()
        assert not ok
        assert "NONEXISTENT" in err

    def test_cycle_detected(self):
        """循环依赖 → 校验失败。"""
        dag = DepthDAG(nodes=[
            DAGNode(name="A", dependencies=["C"],
                    executor=async_noop),
            DAGNode(name="B", dependencies=["A"],
                    executor=async_noop),
            DAGNode(name="C", dependencies=["B"],
                    executor=async_noop),
        ])
        ok, err = dag.validate()
        assert not ok
        assert "循环" in err

    def test_missing_executor_fails(self):
        """executor 为 None → 校验失败。"""
        dag = DepthDAG(nodes=[
            DAGNode(name="A", dependencies=[],
                    executor=None),
        ])
        ok, err = dag.validate()
        assert not ok
        assert "executor" in err.lower()

    def test_execute_invalid_raises(self):
        """校验失败的 DAG 调用 execute() → ValueError。"""
        dag = DepthDAG(nodes=[
            DAGNode(name="A", dependencies=["B"],
                    executor=async_noop),
        ])
        with pytest.raises(ValueError, match="DAG 校验失败"):
            asyncio.run(dag.execute())


# ===========================================================================
# 2. DAG 正常执行测试
# ===========================================================================
class TestDAGNormalExecution:
    """测试 DAG 正常执行流程：拓扑顺序、并行波次、计时、结果提取。"""

    def test_simple_dag_executes_all_nodes(self):
        """简单 DAG 所有节点正常完成。"""
        calls: list[str] = []

        def make_exec(name: str):
            async def fn(results):
                calls.append(name)
                return f"{name}_output"
            return fn

        execs = [make_exec(n) for n in ("A", "B")]
        dag = DepthDAG(nodes=[
            DAGNode(name="A", dependencies=[], executor=execs[0]),
            DAGNode(name="B", dependencies=["A"], executor=execs[1]),
        ])
        result = asyncio.run(dag.execute())

        assert result.success
        assert calls == ["A", "B"]  # A 先于 B
        assert result.nodes["A"].status == "completed"
        assert result.nodes["B"].status == "completed"
        assert result.nodes["A"].output == "A_output"
        # 快机器上 DAG 耗时可能恰好 0.0（亚毫秒），放宽下限避免误判失败。
        assert result.total_elapsed >= 0

    def test_parallel_wave_execution_order(self):
        """并行波次：同波节点并发，但下游等待上游。"""
        order: list[str] = []

        def make_exec(name: str, delay: float = 0.0):
            async def fn(results):
                if delay:
                    await asyncio.sleep(delay)
                order.append(name)
                return name
            return fn

        dag = DepthDAG(nodes=[
            DAGNode(name="Q0", dependencies=[], executor=make_exec("Q0")),
            DAGNode(name="Q1", dependencies=[], executor=make_exec("Q1", delay=0.01)),
            DAGNode(name="QE", dependencies=["Q0", "Q1"], executor=make_exec("QE")),
        ])
        result = asyncio.run(dag.execute())

        assert result.success
        # Q0 和 Q1 在同波并行，Q1 有延迟 → 顺序不保证，但 QE 必须在 Q0/Q1 之后
        q0_idx = order.index("Q0")
        q1_idx = order.index("Q1")
        qe_idx = order.index("QE")
        assert qe_idx > q0_idx
        assert qe_idx > q1_idx

    def test_default_topology_dag(self):
        """v4.2 默认拓扑（Q234 合并维度 + QF 图文节点）全部通过。"""

        def make_exec(name: str):
            async def fn(results):
                return {name: "ok"}
            return fn

        def executor_factory(node_name: str):
            return make_exec(node_name)

        dag = build_depth_dag(executor_factory, node_timeout=5)
        result = asyncio.run(dag.execute())

        assert result.success
        expected = {"Q0", "Q1", "QE", "Q234", "QF", "Q5a", "Q5b", "Q5c"}
        assert set(result.nodes.keys()) == expected
        for name in expected:
            assert result.nodes[name].status == "completed", f"{name} 未完成"
            assert result.nodes[name].elapsed >= 0

        # 计时日志存在
        timings = dag.get_timings()
        assert len(timings) == 8

    def test_legacy_nine_node_dag(self):
        """legacy 拓扑（merged_dimensions=False + include_figure_node=False）回退 v4.1 九节点。"""

        def make_exec(name: str):
            async def fn(results):
                return {name: "ok"}
            return fn

        def executor_factory(node_name: str):
            return make_exec(node_name)

        dag = build_depth_dag(
            executor_factory,
            node_timeout=5,
            merged_dimensions=False,
            include_figure_node=False,
        )
        result = asyncio.run(dag.execute())

        assert result.success
        expected = {"Q0", "Q1", "QE", "Q2", "Q3", "Q4", "Q5a", "Q5b", "Q5c"}
        assert set(result.nodes.keys()) == expected
        for name in expected:
            assert result.nodes[name].status == "completed", f"{name} 未完成"

        timings = dag.get_timings()
        assert len(timings) == 9

    def test_merged_without_figure_topology(self):
        """merged_dimensions=True + include_figure_node=False → 7 节点（无 QF）。"""

        def make_exec(name: str):
            async def fn(results):
                return {name: "ok"}
            return fn

        def executor_factory(node_name: str):
            return make_exec(node_name)

        dag = build_depth_dag(
            executor_factory,
            node_timeout=5,
            merged_dimensions=True,
            include_figure_node=False,
        )
        result = asyncio.run(dag.execute())

        assert result.success
        expected = {"Q0", "Q1", "QE", "Q234", "Q5a", "Q5b", "Q5c"}
        assert set(result.nodes.keys()) == expected

    def test_dag_executor_factory_invalid_raises(self):
        """executor_factory 返回不可调用 → ValueError。"""
        def bad_factory(name: str):
            return None  # 返回 None 非 callable

        with pytest.raises(ValueError, match="executor_factory"):
            build_depth_dag(bad_factory)


# ===========================================================================
# 3. DAG 失败传播测试
# ===========================================================================
class TestDAGFailurePropagation:
    """测试 DAG 失败传播：单点失败 → 下游级联取消。"""

    def test_single_node_failure_cascades(self):
        """Q234 失败 → Q5a/Q5b/Q5c 被取消；同波 QF 不受影响。"""
        async def failing_exec(results):
            raise RuntimeError("Q234 simulated failure")

        async def normal_exec(results):
            return "ok"

        def executor_factory(name: str):
            if name == "Q234":
                return failing_exec
            return normal_exec

        dag = build_depth_dag(executor_factory, node_timeout=5)
        result = asyncio.run(dag.execute())

        assert not result.success
        assert result.nodes["Q0"].status == "completed"
        assert result.nodes["Q1"].status == "completed"
        assert result.nodes["QE"].status == "completed"
        assert result.nodes["Q234"].status == "failed"
        assert "Q234 simulated failure" in result.nodes["Q234"].error
        # QF 仍应完成（与 Q234 同波并行，失败不阻塞同波节点）
        assert result.nodes["QF"].status == "completed"
        # 下游被取消
        assert result.nodes["Q5a"].status == "cancelled"
        assert result.nodes["Q5b"].status == "cancelled"
        assert result.nodes["Q5c"].status == "cancelled"

    def test_qf_failure_cascades_debate_nodes(self):
        """QF 失败 → Q5a/Q5b/Q5c 被取消；Q234 不受影响（反之亦然，验证 QF 也是 Q5a 依赖）。"""
        async def failing_exec(results):
            raise RuntimeError("QF simulated failure")

        async def normal_exec(results):
            return "ok"

        def executor_factory(name: str):
            if name == "QF":
                return failing_exec
            return normal_exec

        dag = build_depth_dag(executor_factory, node_timeout=5)
        result = asyncio.run(dag.execute())

        assert not result.success
        assert result.nodes["QF"].status == "failed"
        assert result.nodes["Q234"].status == "completed"
        assert result.nodes["Q5a"].status == "cancelled"
        assert result.nodes["Q5b"].status == "cancelled"
        assert result.nodes["Q5c"].status == "cancelled"

    def test_root_node_failure_cascades_everything(self):
        """Q0 失败 → 所有下游 (QE/Q234/QF/Q5a/Q5b/Q5c) 被取消。"""
        async def failing_q0(results):
            raise RuntimeError("Q0 failed at root")

        async def normal_exec(results):
            return "ok"

        def executor_factory(name: str):
            if name == "Q0":
                return failing_q0
            return normal_exec

        dag = build_depth_dag(executor_factory, node_timeout=5)
        result = asyncio.run(dag.execute())

        assert not result.success
        assert result.nodes["Q0"].status == "failed"
        # Q1 与 Q0 同波，不受影响
        assert result.nodes["Q1"].status == "completed"
        # 下游全部被取消
        assert result.nodes["QE"].status == "cancelled"
        assert result.nodes["Q234"].status == "cancelled"
        assert result.nodes["QF"].status == "cancelled"
        assert result.nodes["Q5a"].status == "cancelled"
        assert result.nodes["Q5b"].status == "cancelled"
        assert result.nodes["Q5c"].status == "cancelled"

    def test_cancel_message_propagates(self):
        """被取消节点的 error 消息指示上游失败原因。"""
        async def failing_exec(results):
            raise RuntimeError("upstream error detail")

        async def normal_exec(results):
            return "ok"

        def executor_factory(name: str):
            if name == "QE":
                return failing_exec
            return normal_exec

        dag = build_depth_dag(executor_factory, node_timeout=5)
        result = asyncio.run(dag.execute())

        assert not result.success
        assert result.nodes["QE"].status == "failed"
        # Q234 依赖 QE → 被取消
        assert result.nodes["Q234"].status == "cancelled"
        assert "上游" in result.nodes["Q234"].error

    def test_qe_failure_cascades_all_downstream(self):
        """QE 失败 → Q234/QF/Q5a/Q5b/Q5c 全部被取消。

        QE 是唯一被所有评分节点依赖的关键瓶颈，必须单独测试。
        """
        async def failing_qe(results):
            raise RuntimeError("QE evidence extraction failed")

        async def normal_exec(results):
            return "ok"

        def executor_factory(name: str):
            if name == "QE":
                return failing_qe
            return normal_exec

        dag = build_depth_dag(executor_factory, node_timeout=5)
        result = asyncio.run(dag.execute())

        assert not result.success
        assert result.nodes["Q0"].status == "completed"
        assert result.nodes["Q1"].status == "completed"
        assert result.nodes["QE"].status == "failed"
        # 所有下游被取消
        for name in ("Q234", "QF", "Q5a", "Q5b", "Q5c"):
            assert result.nodes[name].status == "cancelled", f"{name} 应被取消"

    def test_executor_factory_unknown_node_raises(self):
        """executor_factory 对未知节点名应抛出 ValueError。"""
        def bad_factory(name: str):
            raise ValueError(f"executor_factory error: unknown node '{name}'")

        with pytest.raises(ValueError, match="executor_factory"):
            build_depth_dag(bad_factory)

    def test_get_failed_nodes(self):
        """get_failed_nodes() 返回失败节点列表。

        Q0 失败 → QE（下游）被取消 → Q234（依赖 QE）被取消。
        因此仅 Q0 显式失败，Q234 是级联取消（cancelled），不是 failed。
        """
        async def failing_exec(results):
            raise RuntimeError("fail")

        async def normal_exec(results):
            return "ok"

        def executor_factory(name: str):
            if name in ("Q0", "Q234"):
                return failing_exec
            return normal_exec

        dag = build_depth_dag(executor_factory, node_timeout=5)
        asyncio.run(dag.execute())

        failed = dag.get_failed_nodes()
        # Q0 显式失败；Q234 因上游 QE 被取消而级联取消
        assert set(failed) == {"Q0"}
        assert dag._results["Q234"].status == "cancelled"


# ===========================================================================
# 4. DAG 取消清理测试
# ===========================================================================
class TestDAGCancellation:
    """测试 asyncio.CancelledError 在流水线中的传播与清理。"""

    async def _run_dag_with_cancel(self, cancel_after: float = 0.05) -> PipelineResult:
        """辅助：启动 DAG，在指定时间后取消。"""
        async def slow_exec(results):
            await asyncio.sleep(10)  # 足够慢，确保取消发生
            return "never_reached"

        def executor_factory(name: str):
            return slow_exec

        dag = build_depth_dag(executor_factory, node_timeout=30)
        dag_task = asyncio.create_task(dag.execute())
        await asyncio.sleep(cancel_after)
        dag_task.cancel()
        try:
            return await dag_task
        except asyncio.CancelledError:
            # 不应该到这里 — execute() 内部应捕获 CancelledError
            pytest.fail("execute() 应内部捕获 CancelledError 并返回 PipelineResult")

    def test_cancelled_error_returns_pipeline_result(self):
        """外部取消 → execute() 返回 PipelineResult(success=False)。

        _execute_dag 逐任务捕获 CancelledError 并标记为 cancelled，
        不会传播到 execute() 的 CancelledError handler，因此 error 可能为空。
        只需验证 success=False 且节点被标记为 cancelled。
        """
        result = asyncio.run(self._run_dag_with_cancel(0.05))
        assert not result.success
        # 至少有一个节点因取消被标记为 cancelled
        cancelled_count = sum(1 for n in result.nodes.values() if n.status == "cancelled")
        assert cancelled_count > 0, f"期望至少一个节点被取消，实际: { {k: v.status for k, v in result.nodes.items()} }"

    def test_cancelled_nodes_marked_correctly(self):
        """取消后所有节点标记为 completed/cancelled/pending。

        极短 cancel_after (0.05s) 时，Wave 2 节点可能尚未启动，
        其状态为 pending 也属合法（未被调度即收到取消信号）。
        """
        result = asyncio.run(self._run_dag_with_cancel(0.05))
        for name in ("Q0", "Q1", "QE", "Q234", "QF", "Q5a", "Q5b", "Q5c"):
            status = result.nodes[name].status
            assert status in ("completed", "cancelled", "pending"), \
                f"{name} 期望 completed/cancelled/pending，实际 {status}"

    def test_running_tasks_cleaned_in_finally(self):
        """finally 块确保 _running_tasks 被清空。"""
        async def _run():
            async def slow_exec(results):
                await asyncio.sleep(10)
                return "ok"

            def executor_factory(name: str):
                return slow_exec

            dag = build_depth_dag(executor_factory, node_timeout=30)
            task = asyncio.create_task(dag.execute())
            # 短暂等待让 Wave 1 开始
            await asyncio.sleep(0.05)
            task.cancel()
            result = await task
            # execute() 内部捕获 CancelledError 返回 PipelineResult
            assert not result.success
            # finally 已执行，_running_tasks 应为空
            assert dag._running_tasks == {}

        asyncio.run(_run())


# ===========================================================================
# 5. review_async_dag 集成测试（Mock LLM + DAG 引擎）
# ===========================================================================
class TestReviewAsyncDAG:
    """测试 DepthReviewer.review_async_dag() 端到端 DAG 执行。

    v4.2 DI 统一后（Q0/Q1/Q5a/Q5b 改走注入的 self._llm），
    全部节点由 MockLLM 驱动，无需真实 LLM 服务。
    _load_figure_items 打桩为 [] 保持 hermetic（图表场景见 test_depth_v4）。
    """

    @pytest.fixture(autouse=True)
    def _mock_resource_detection(self):
        """🛡️ AI-03：打桩 get_dynamic_preset + detect_resources，防止原生 GPU 检测触发 C 级崩溃。"""
        from mock_api.compute_mode import SystemResources
        with patch('mock_api.config.get_dynamic_preset', return_value=None), \
             patch('mock_api.compute_mode.detect_resources', return_value=SystemResources()), \
             patch.object(DepthReviewer, '_load_figure_items', return_value=[]):
            yield

    def test_full_dag_pipeline_normal(self):
        """v4.2 默认拓扑（Q234+QF）DAG 审稿，Mock LLM 数据。"""
        mock_llm = MockLLM()
        reviewer = DepthReviewer(llm_func=mock_llm)
        result = asyncio.run(reviewer.review_async_dag(
            paper_id=MOCK_PAPER_ID,
            title=MOCK_TITLE,
            full_text=MOCK_FULL_TEXT,
            abstract=MOCK_ABSTRACT,
        ))

        # 基础字段
        assert result.paper_id == MOCK_PAPER_ID
        assert result.title == MOCK_TITLE

        # Q0/Q1/QE
        assert result.has_substance is True
        assert result.paper_type == "B"
        assert len(result.evidence_pool) == 10

        # Q234（合并多维评分）
        assert result.novelty_score == 0.82
        # 消融实验证据检测触发严谨分上调（0.78 + 0.05~0.10）
        assert result.rigor_score > 0.82, f"expected rigor > 0.82, got {result.rigor_score}"
        assert result.influence_score == 0.85

        # QF（无图表 → 中性）
        assert result.figure_consistency_score == 0.5

        # Q5a/Q5b/Q5c
        assert len(result.critique_points) == 2
        assert len(result.defense_points) == 2

        # 硬编码裁决：消融上调 + 平衡者降级 fatal→minor → accept
        assert result.final_verdict == "accept", f"expected accept, got {result.final_verdict}"

        # 日志完整性
        assert any("DAG引擎" in log for log in result.node_logs)
        assert any("审稿完成" in log for log in result.node_logs)
        # DAG 计时日志
        assert any("节点" in log and "s" in log for log in result.node_logs)

    def test_dag_vs_serial_same_result(self):
        """DAG 路径与串行路径产出相同结果（相同 Mock 数据）。"""
        mock1 = MockLLM()
        mock2 = MockLLM()
        reviewer1 = DepthReviewer(llm_func=mock1)
        reviewer2 = DepthReviewer(llm_func=mock2)

        serial = reviewer1.review(
            paper_id=MOCK_PAPER_ID, title=MOCK_TITLE,
            full_text=MOCK_FULL_TEXT, abstract=MOCK_ABSTRACT,
        )
        dag = asyncio.run(reviewer2.review_async_dag(
            paper_id=MOCK_PAPER_ID, title=MOCK_TITLE,
            full_text=MOCK_FULL_TEXT, abstract=MOCK_ABSTRACT,
        ))

        # 关键字段应该一致
        assert serial.paper_type == dag.paper_type
        assert serial.novelty_score == dag.novelty_score
        assert serial.rigor_score == dag.rigor_score
        assert serial.influence_score == dag.influence_score
        assert serial.reproducibility_score == dag.reproducibility_score
        assert serial.figure_consistency_score == dag.figure_consistency_score
        assert serial.final_verdict == dag.final_verdict
        assert serial.calibrated_score == dag.calibrated_score
        assert serial.base_score == dag.base_score
        assert serial.delta == dag.delta
        assert len(serial.evidence_pool) == len(dag.evidence_pool)
        assert serial.evidence_checks == dag.evidence_checks
        assert serial.weights == dag.weights

    def test_dag_timing_logs_present(self):
        """DAG 执行产生详细的节点计日日志。"""
        mock = MockLLM()
        reviewer = DepthReviewer(llm_func=mock)
        result = asyncio.run(reviewer.review_async_dag(
            paper_id=MOCK_PAPER_ID, title=MOCK_TITLE,
            full_text=MOCK_FULL_TEXT, abstract=MOCK_ABSTRACT,
        ))

        # 检查计时日志格式（v4.2 默认拓扑 8 个节点）
        timing_logs = [log for log in result.node_logs if "节点" in log and "." in log]
        assert len(timing_logs) >= 8, f"期望 ≥8 条计时日志，实际 {len(timing_logs)}"
        # 总耗时日志
        assert any("总耗时" in log for log in result.node_logs)

    def test_dag_verdict_matches_serial(self):
        """硬编码裁决在 DAG 和串行路径一致。"""
        mock1 = MockLLM()
        mock2 = MockLLM()
        r1 = DepthReviewer(llm_func=mock1)
        r2 = DepthReviewer(llm_func=mock2)

        serial = r1.review(
            paper_id=MOCK_PAPER_ID, title=MOCK_TITLE,
            full_text=MOCK_FULL_TEXT, abstract=MOCK_ABSTRACT,
        )
        dag = asyncio.run(r2.review_async_dag(
            paper_id=MOCK_PAPER_ID, title=MOCK_TITLE,
            full_text=MOCK_FULL_TEXT, abstract=MOCK_ABSTRACT,
        ))

        assert serial.override_reason == dag.override_reason
        assert serial.has_substance == dag.has_substance

    def test_dag_with_custom_hotspots(self):
        """自定义热点词传递给 DAG。"""
        mock = MockLLM()
        reviewer = DepthReviewer(llm_func=mock)
        result = asyncio.run(reviewer.review_async_dag(
            paper_id=MOCK_PAPER_ID, title=MOCK_TITLE,
            full_text=MOCK_FULL_TEXT, abstract=MOCK_ABSTRACT,
            hotspots=["Graph Neural Network", "Self-Supervised Learning"],
        ))
        assert result.hotspot_alignment_score == 0.88


# ===========================================================================
# 6. NodeResult 与 PipelineResult 数据类测试
# ===========================================================================
class TestDataClasses:
    """测试 NodeResult / PipelineResult 数据类行为。"""

    def test_node_result_elapsed(self):
        """NodeResult.elapsed 正确计算。"""
        nr = NodeResult(name="test", started_at=10.0, finished_at=15.0)
        assert nr.elapsed == 5.0

    def test_node_result_elapsed_zero_when_not_finished(self):
        """未完成时 elapsed 为 0。"""
        nr = NodeResult(name="test")
        assert nr.elapsed == 0.0

    def test_pipeline_result_defaults(self):
        """PipelineResult 默认值。"""
        pr = PipelineResult(success=True, nodes={})
        assert pr.error == ""
        assert pr.total_elapsed == 0.0

    def test_dag_node_defaults(self):
        """DAGNode 默认值。"""
        node = DAGNode(name="test")
        assert node.dependencies == []
        assert node.executor is None
        assert node.timeout is None


# ===========================================================================
# 7. 熔断器（Circuit Breaker）单元测试
# ===========================================================================
from mock_api.circuit_breaker import (
    CircuitBreaker,
    get_circuit_breaker,
    reset_all_circuits,
)
from mock_api.depth_pipeline import _get_circuit_breaker


@pytest.mark.critical
class TestCircuitBreaker:
    """CircuitBreaker 三态机单元：CLOSED→OPEN→HALF_OPEN→CLOSED。"""

    def test_closed_allows_request(self):
        cb = CircuitBreaker("X")
        assert cb.state.value == "closed"
        assert cb.allow_request() is True

    def test_trips_after_failure_threshold(self):
        cb = CircuitBreaker("X", failure_threshold=5)
        for _ in range(4):
            cb.on_failure()
        assert cb.state.value == "closed"  # 未达阈值
        cb.on_failure()
        assert cb.state.value == "open"  # 第 5 次跳闸
        assert cb.allow_request() is False  # OPEN 拒绝

    def test_open_blocks_request(self):
        cb = CircuitBreaker("X")
        cb.force_open()
        assert cb.state.value == "open"
        assert cb.allow_request() is False

    def test_half_open_recovers_on_success(self):
        cb = CircuitBreaker("X", failure_threshold=1, recovery_timeout=0)
        cb.force_open()
        # recovery_timeout=0 → 立即可进入 HALF_OPEN
        assert cb.allow_request() is True
        assert cb.state.value == "half_open"
        cb.on_success()
        assert cb.state.value == "closed"

    def test_half_open_failure_back_to_open(self):
        cb = CircuitBreaker("X", failure_threshold=1, recovery_timeout=0)
        cb.force_open()
        assert cb.allow_request() is True  # → half_open
        cb.on_failure()
        assert cb.state.value == "open"  # 探测失败 → 回 OPEN

    def test_on_success_resets_failure_count(self):
        cb = CircuitBreaker("X", failure_threshold=5)
        cb.on_failure()
        cb.on_failure()
        assert cb._failure_count == 2
        cb.on_success()
        assert cb._failure_count == 0
        assert cb.state.value == "closed"

    def test_force_open_sets_opened_at(self):
        cb = CircuitBreaker("X")
        cb.force_open()
        assert cb.state.value == "open"
        assert cb.stats().trip_count >= 1

    def test_get_circuit_breaker_returns_breaker(self):
        cb = get_circuit_breaker("Q234")
        assert isinstance(cb, CircuitBreaker)

    def test_get_circuit_breaker_singleton(self):
        a = get_circuit_breaker("singleton_test")
        b = get_circuit_breaker("singleton_test")
        assert a is b

    def test_threshold_validation(self):
        import pytest as _pytest

        with _pytest.raises(ValueError):
            CircuitBreaker("X", failure_threshold=0)


# ===========================================================================
# 8. DAG 熔断器 fail-open 集成（跳闸 → output=None → Q5a 兜底）
# ===========================================================================
@pytest.mark.critical
class TestDAGCircuitBreaker:
    """_get_circuit_breaker + _run_node 的 fail-open 路径。

    v4.2 设计：维度评分节点(Q234/Q2/Q3/Q4)连续失败 → 熔断器跳闸 →
    节点 output=None、status=completed（非 cancelled，避免 DAG 卡死）→
    下游 Q5a 读取到 None 走 OIM 客观特征层保底。
    """

    @pytest.fixture(autouse=True)
    def _reset_circuits(self):
        reset_all_circuits()
        yield
        reset_all_circuits()

    def test_get_circuit_breaker_helper(self):
        """depth_pipeline._get_circuit_breaker 返回集成熔断器。"""
        cb = _get_circuit_breaker("Q234")
        assert isinstance(cb, CircuitBreaker)
        # 非维度节点名同样返回 CB 实例（gate 在 _run_node 内按名称过滤）
        assert isinstance(_get_circuit_breaker("Q0"), CircuitBreaker)

    def test_cb_open_skips_node_output_none(self):
        """Q234 熔断开路 → 节点 output=None、status=completed。"""
        get_circuit_breaker("Q234").force_open()

        captured: dict = {}

        async def q234_exec(results):
            return ("q2", "q3", "q4")

        async def q5a_exec(results):
            # Q5a 读取上游维度评分：熔断时为 None → OIM 兜底
            captured["q234"] = results.Q234
            if results.Q234 is None:
                return "OIM_FALLBACK"
            return "REAL"

        dag = DepthDAG(nodes=[
            DAGNode(name="Q234", dependencies=[], executor=q234_exec),
            DAGNode(name="Q5a", dependencies=["Q234"], executor=q5a_exec),
        ])
        result = asyncio.run(dag.execute())

        assert result.success
        assert result.nodes["Q234"].status == "completed"  # fail-open 不是 cancelled
        assert result.nodes["Q234"].output is None
        assert result.nodes["Q5a"].status == "completed"
        assert captured["q234"] is None
        assert result.nodes["Q5a"].output == "OIM_FALLBACK"

    def test_cb_closed_runs_normally(self):
        """Q234 熔断器闭合 → 节点正常执行，Q5a 拿到真实结果。"""
        get_circuit_breaker("Q234")  # 确保存在且 CLOSED（fixture 已 reset）

        captured: dict = {}

        async def q234_exec(results):
            return ("q2", "q3", "q4")

        async def q5a_exec(results):
            captured["q234"] = results.Q234
            return "REAL" if results.Q234 is not None else "OIM_FALLBACK"

        dag = DepthDAG(nodes=[
            DAGNode(name="Q234", dependencies=[], executor=q234_exec),
            DAGNode(name="Q5a", dependencies=["Q234"], executor=q5a_exec),
        ])
        result = asyncio.run(dag.execute())

        assert result.success
        assert result.nodes["Q234"].status == "completed"
        assert result.nodes["Q234"].output == ("q2", "q3", "q4")
        assert captured["q234"] == ("q2", "q3", "q4")
        assert result.nodes["Q5a"].output == "REAL"

    def test_cb_open_dag_success_fail_open(self):
        """熔断 fail-open：维度节点被跳过但 DAG 整体仍 success（不级联取消）。"""
        get_circuit_breaker("Q234").force_open()

        async def q234_exec(results):
            return "REAL"

        async def q5a_exec(results):
            return "x" if results.Q234 is None else "y"

        dag = DepthDAG(nodes=[
            DAGNode(name="Q234", dependencies=[], executor=q234_exec),
            DAGNode(name="Q5a", dependencies=["Q234"], executor=q5a_exec),
        ])
        result = asyncio.run(dag.execute())

        # 与「Q234 失败」路径不同：fail-open 下下游不 cancelled，DAG 成功
        assert result.success is True
        assert result.nodes["Q5a"].status == "completed"
        assert result.nodes["Q234"].status == "completed"
        assert result.nodes["Q5a"].status != "cancelled"


# ===========================================================================
# 辅助函数
# ===========================================================================
async def async_noop(results=None):
    """空操作异步 executor。"""
    return "noop"
