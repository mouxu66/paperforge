"""测试 mock_api/workers/reviews.py 中的后台任务 worker。

覆盖：
- depth_batch_worker：v3 DEPTH 批量评分
- v4_batch_review_worker：v4.1 批量审稿

重点验证：
- 进度上报
- 异常处理
- 结果/失败统计
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# depth_batch_worker
# ---------------------------------------------------------------------------
@patch("mock_api.workers.reviews._get_batch_parallel", return_value=2)
@patch("mock_api.tasks.TaskManager")
@patch("mock_api.depth_eval.evaluate_paper")
def test_depth_batch_worker_empty_paper_ids(mock_eval, mock_tm, mock_parallel):
    """paper_ids 为空时立即标记失败。"""
    from mock_api.workers.reviews import depth_batch_worker

    depth_batch_worker("task-1", {"paperIds": []})

    mock_tm.fail.assert_called_once_with("task-1", "paperIds 为空")
    mock_tm.complete.assert_not_called()
    mock_eval.assert_not_called()


@patch("mock_api.workers.reviews._get_batch_parallel", return_value=2)
@patch("mock_api.tasks.TaskManager")
@patch("mock_api.depth_eval.evaluate_paper")
def test_depth_batch_worker_missing_paper_ids(mock_eval, mock_tm, mock_parallel):
    """params 中无 paperIds 键时按空列表处理，标记失败。"""
    from mock_api.workers.reviews import depth_batch_worker

    depth_batch_worker("task-1", {})

    mock_tm.fail.assert_called_once_with("task-1", "paperIds 为空")
    mock_tm.complete.assert_not_called()


@patch("mock_api.workers.reviews._get_batch_parallel", return_value=2)
@patch("mock_api.tasks.TaskManager")
@patch("mock_api.depth_eval.evaluate_paper")
def test_depth_batch_worker_success_and_progress(mock_eval, mock_tm, mock_parallel):
    """正常评估多篇论文，验证进度上报和结果排序。"""
    from mock_api.workers.reviews import depth_batch_worker

    def _eval(pid: str) -> dict:
        return {
            "paper_id": pid,
            "final_score": float(pid[-2:]) if pid[-2:].isdigit() else 0.5,
            "type": "test",
        }

    mock_eval.side_effect = _eval

    depth_batch_worker("task-1", {"paperIds": ["p01", "p02", "p03"]})

    # 完成时调用 complete
    mock_tm.complete.assert_called_once()
    result = mock_tm.complete.call_args[0][1]
    assert result["total"] == 3
    assert result["summary"]["completed"] == 3
    assert result["summary"]["failed"] == 0
    # 结果按 final_score 降序并带 rank
    results = result["results"]
    assert len(results) == 3
    assert results[0]["rank"] == 1
    assert results[0]["final_score"] >= results[1]["final_score"]

    # 验证进度上报：每完成一篇论文都会更新进度，最终为 100%
    progress_calls = [call.args for call in mock_tm.update_progress.call_args_list]
    progress_values = [args[1] for args in progress_calls]
    assert 100 in progress_values
    # 验证进度消息格式
    assert any("3/3" in (args[2] if len(args) > 2 else "") for args in progress_calls)


@patch("mock_api.workers.reviews._get_batch_parallel", return_value=2)
@patch("mock_api.tasks.TaskManager")
@patch("mock_api.depth_eval.evaluate_paper")
def test_depth_batch_worker_error_handling(mock_eval, mock_tm, mock_parallel):
    """部分论文评估异常时，错误被收集且不影响其他结果。"""
    from mock_api.workers.reviews import depth_batch_worker

    def _eval_or_raise(pid: str) -> dict:
        if pid == "bad":
            raise ValueError("boom")
        return {"paper_id": pid, "final_score": 0.8, "type": "test"}

    mock_eval.side_effect = _eval_or_raise

    depth_batch_worker("task-1", {"paperIds": ["good", "bad", "also_good"]})

    mock_tm.complete.assert_called_once()
    result = mock_tm.complete.call_args[0][1]
    assert result["summary"]["completed"] == 2
    assert result["summary"]["failed"] == 1
    failed = [r for r in result["results"] if "error" in r]
    assert len(failed) == 1
    assert "boom" in failed[0]["error"]


@patch("mock_api.workers.reviews._get_batch_parallel", return_value=2)
@patch("mock_api.tasks.TaskManager")
@patch("mock_api.depth_eval.evaluate_paper")
def test_depth_batch_worker_error_field_in_result(mock_eval, mock_tm, mock_parallel):
    """evaluate_paper 返回 error 字段时，计入失败列表。"""
    from mock_api.workers.reviews import depth_batch_worker

    mock_eval.side_effect = [
        {"paper_id": "ok", "final_score": 0.9, "type": "test"},
        {"paper_id": "err", "error": "LLM timeout"},
    ]

    depth_batch_worker("task-1", {"paperIds": ["ok", "err"]})

    result = mock_tm.complete.call_args[0][1]
    assert result["summary"]["completed"] == 1
    assert result["summary"]["failed"] == 1
    assert result["results"][-1]["error"] == "LLM timeout"


# ---------------------------------------------------------------------------
# v4_batch_review_worker
# ---------------------------------------------------------------------------
@patch("mock_api.tasks.TaskManager")
@patch("mock_api.database.SessionLocal")
@patch("mock_api.depth_tasks.run_depth_review_sync")
def test_v4_batch_review_worker_success(mock_run, mock_session, mock_tm):
    """所有论文审稿成功，验证进度和结果统计。"""
    from mock_api.workers.reviews import v4_batch_review_worker

    mock_run.return_value = "record-id"

    v4_batch_review_worker("task-1", {"paperIds": ["p1", "p2"]})

    assert mock_run.call_count == 2
    mock_tm.complete.assert_called_once()
    result = mock_tm.complete.call_args[0][1]
    assert result["total"] == 2
    assert result["succeeded"] == 2
    assert result["failed"] == 0
    assert result["failedPapers"] == []
    # 验证进度消息包含「审稿中」
    progress_calls = [call.args for call in mock_tm.update_progress.call_args_list]
    assert any("审稿中" in (args[2] if len(args) > 2 else "") for args in progress_calls)


@patch("mock_api.tasks.TaskManager")
@patch("mock_api.database.SessionLocal")
@patch("mock_api.depth_tasks.run_depth_review_sync")
def test_v4_batch_review_worker_empty_paper_ids(mock_run, mock_session, mock_tm):
    """paper_ids 为空时立即标记失败。"""
    from mock_api.workers.reviews import v4_batch_review_worker

    v4_batch_review_worker("task-1", {"paperIds": []})

    mock_tm.fail.assert_called_once_with("task-1", "没有待审论文")
    mock_tm.complete.assert_not_called()
    mock_run.assert_not_called()


@patch("mock_api.tasks.TaskManager")
@patch("mock_api.database.SessionLocal")
@patch("mock_api.depth_tasks.run_depth_review_sync")
def test_v4_batch_review_worker_missing_paper_ids(mock_run, mock_session, mock_tm):
    """params 中无 paperIds 键时按空列表处理，标记失败。"""
    from mock_api.workers.reviews import v4_batch_review_worker

    v4_batch_review_worker("task-1", {})

    mock_tm.fail.assert_called_once_with("task-1", "没有待审论文")
    mock_tm.complete.assert_not_called()
    mock_run.assert_not_called()


@patch("mock_api.tasks.TaskManager")
@patch("mock_api.database.SessionLocal")
@patch("mock_api.depth_tasks.run_depth_review_sync")
def test_v4_batch_review_worker_partial_failure(mock_run, mock_session, mock_tm):
    """部分论文审稿异常时，失败信息被收集。"""
    from mock_api.workers.reviews import v4_batch_review_worker

    def _run_or_raise(pid: str, **kwargs) -> str:
        if pid == "bad":
            raise RuntimeError("review failed")
        return "record-id"

    mock_run.side_effect = _run_or_raise

    v4_batch_review_worker("task-1", {"paperIds": ["good", "bad", "also_good"]})

    result = mock_tm.complete.call_args[0][1]
    assert result["total"] == 3
    assert result["succeeded"] == 2
    assert result["failed"] == 1
    assert len(result["failedPapers"]) == 1
    assert result["failedPapers"][0]["paper_id"] == "bad"
    assert "review failed" in result["failedPapers"][0]["error"]


@patch("mock_api.tasks.TaskManager")
@patch("mock_api.database.SessionLocal")
@patch("mock_api.depth_tasks.run_depth_review_sync")
def test_v4_batch_review_worker_closes_session(mock_run, mock_session, mock_tm):
    """每个 paper 处理完后都关闭 DB session。"""
    from mock_api.workers.reviews import v4_batch_review_worker

    db_mock = MagicMock()
    mock_session.return_value = db_mock
    mock_run.return_value = "record-id"

    v4_batch_review_worker("task-1", {"paperIds": ["p1", "p2"]})

    assert db_mock.close.call_count == 2
