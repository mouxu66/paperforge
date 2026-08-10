"""Tests for the figure understanding background worker and DEPTH trigger."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from mock_api.workers.figure_understanding import (
    _figure_understanding_worker,
    submit_figure_understanding_task,
)


def test_submit_figure_understanding_task_returns_task_id():
    with patch("mock_api.workers.figure_understanding.TaskManager") as mock_tm:
        mock_tm.submit.return_value = "task-123"
        task_id = submit_figure_understanding_task("paper-1")
        assert task_id == "task-123"
        mock_tm.submit.assert_called_once()
        args = mock_tm.submit.call_args
        assert args.kwargs["params"] == {"paper_id": "paper-1"}


def test_submit_figure_understanding_task_submits_multiple_distinct_papers():
    with patch("mock_api.workers.figure_understanding.TaskManager") as mock_tm:
        mock_tm.submit.return_value = "task-123"
        submit_figure_understanding_task("paper-1")
        submit_figure_understanding_task("paper-2")
        assert mock_tm.submit.call_count == 2


def test_figure_understanding_worker_runs_pipeline(tmp_path, db_session):
    """Worker should read PDF bytes, call run_pipeline, and complete the task."""
    from mock_api.models import Paper

    paper = Paper(
        id="paper-abc",
        title="Test Paper",
        authors=[],
        abstract="",
        year=2024,
        source="test",
    )
    db_session.add(paper)
    db_session.commit()

    pdf_path = tmp_path / "paper-abc.pdf"
    pdf_path.write_bytes(b"\x25\x50\x44\x46 fake pdf")

    with patch("mock_api.workers.figure_understanding._get_uploads_dir") as mock_uploads, patch(
        "mock_api.workers.figure_understanding.run_pipeline"
    ) as mock_run, patch("mock_api.workers.figure_understanding.TaskManager") as mock_tm:
        mock_uploads.return_value = tmp_path
        mock_run.return_value = 0

        _figure_understanding_worker("task-1", {"paper_id": "paper-abc"})

        mock_run.assert_called_once()
        args = mock_run.call_args.kwargs
        assert args["paper_id"] == "paper-abc"
        assert args["pdf_bytes"] == b"\x25\x50\x44\x46 fake pdf"
        mock_tm.complete.assert_called_once()


def test_figure_understanding_worker_fails_when_pdf_missing():
    with patch("mock_api.workers.figure_understanding._get_uploads_dir") as mock_uploads, patch(
        "mock_api.workers.figure_understanding.TaskManager"
    ) as mock_tm:
        fake_path = MagicMock()
        fake_path.exists.return_value = False
        mock_uploads.return_value.__truediv__ = MagicMock(return_value=fake_path)

        _figure_understanding_worker("task-1", {"paper_id": "paper-xyz"})

        mock_tm.fail.assert_called_once()


def test_submit_deduplicates_active_task_for_same_paper():
    """submit_figure_understanding_task should reuse an active task for the same paper_id."""
    with patch("mock_api.workers.figure_understanding.TaskManager") as mock_tm:
        mock_tm.submit.return_value = "task-new"
        mock_tm.list.return_value = [
            {
                "id": "task-existing",
                "type": "figure_understanding",
                "status": "running",
                "params": {"paper_id": "paper-1"},
            }
        ]

        task_id = submit_figure_understanding_task("paper-1")

        assert task_id == "task-existing"
        mock_tm.submit.assert_not_called()


def test_submit_allows_new_task_after_previous_completed():
    """submit_figure_understanding_task should create a new task if the previous one is finished."""
    with patch("mock_api.workers.figure_understanding.TaskManager") as mock_tm:
        mock_tm.submit.return_value = "task-new"
        mock_tm.list.return_value = [
            {
                "id": "task-old",
                "type": "figure_understanding",
                "status": "completed",
                "params": {"paper_id": "paper-1"},
            }
        ]

        task_id = submit_figure_understanding_task("paper-1")

        assert task_id == "task-new"
        mock_tm.submit.assert_called_once()


def test_worker_waits_for_vram_when_busy(tmp_path, db_session):
    """Worker should wait when VRAM is busy with OCR and run once idle."""
    from mock_api.models import Paper
    from mock_api.vram_scheduler import VRAMState

    paper = Paper(
        id="paper-vram",
        title="VRAM Test",
        authors=[],
        abstract="",
        year=2024,
        source="test",
    )
    db_session.add(paper)
    db_session.commit()

    pdf_path = tmp_path / "paper-vram.pdf"
    pdf_path.write_bytes(b"\x25\x50\x44\x46 fake pdf")

    call_count = {"n": 0}

    def fake_state():
        # Transition from OCR_ACTIVE to IDLE after the first couple of checks
        # so the worker can proceed without waiting the full timeout.
        call_count["n"] += 1
        return VRAMState.OCR_ACTIVE if call_count["n"] <= 2 else VRAMState.IDLE

    with patch("mock_api.workers.figure_understanding._get_uploads_dir") as mock_uploads, patch(
        "mock_api.workers.figure_understanding.run_pipeline"
    ) as mock_run, patch("mock_api.workers.figure_understanding.TaskManager") as mock_tm, patch(
        "mock_api.workers.figure_understanding.get_vram_scheduler"
    ) as mock_scheduler, patch(
        "mock_api.workers.figure_understanding.time.sleep"
    ) as mock_sleep:
        mock_uploads.return_value = tmp_path
        mock_run.return_value = 0
        mock_scheduler.return_value.state = fake_state
        mock_tm.get_cancel_event.return_value.is_set.return_value = False

        _figure_understanding_worker("task-1", {"paper_id": "paper-vram"})

        # The wait loop should have been entered.
        assert mock_sleep.call_count >= 1
        # First progress call should indicate waiting/queued.
        progress_calls = [c for c in mock_tm.update_progress.call_args_list if "排队" in str(c)]
        assert len(progress_calls) >= 1
        # After OCR finishes the pipeline runs.
        mock_run.assert_called_once()
        mock_tm.complete.assert_called_once()


def test_worker_fails_when_vram_busy_timeout(tmp_path, db_session):
    """Worker should fail if VRAM stays busy and waiting returns False."""
    from mock_api.models import Paper

    paper = Paper(
        id="paper-vram-timeout",
        title="VRAM Timeout Test",
        authors=[],
        abstract="",
        year=2024,
        source="test",
    )
    db_session.add(paper)
    db_session.commit()

    pdf_path = tmp_path / "paper-vram-timeout.pdf"
    pdf_path.write_bytes(b"\x25\x50\x44\x46 fake pdf")

    with patch("mock_api.workers.figure_understanding._get_uploads_dir") as mock_uploads, patch(
        "mock_api.workers.figure_understanding.run_pipeline"
    ) as mock_run, patch("mock_api.workers.figure_understanding.TaskManager") as mock_tm, patch(
        "mock_api.workers.figure_understanding._wait_for_vram_available"
    ) as mock_wait:
        mock_uploads.return_value = tmp_path
        mock_run.return_value = 0
        mock_wait.return_value = False

        _figure_understanding_worker("task-1", {"paper_id": "paper-vram-timeout"})

        mock_wait.assert_called_once()
        mock_run.assert_not_called()
        mock_tm.fail.assert_called_once()
        args = mock_tm.fail.call_args
        assert "VRAM" in str(args) or "超时" in str(args) or "空闲" in str(args)


def test_wait_for_vram_available_times_out():
    """_wait_for_vram_available should return False when VRAM stays busy."""
    from unittest.mock import MagicMock

    from mock_api.vram_scheduler import VRAMState
    from mock_api.workers.figure_understanding import _wait_for_vram_available

    scheduler = MagicMock()
    scheduler.state.return_value = VRAMState.OCR_ACTIVE

    with patch("mock_api.workers.figure_understanding.get_vram_scheduler") as mock_get_scheduler, patch(
        "mock_api.workers.figure_understanding.TaskManager"
    ) as mock_tm, patch("mock_api.workers.figure_understanding.time.sleep") as mock_sleep:
        mock_get_scheduler.return_value = scheduler
        mock_tm.get_cancel_event.return_value.is_set.return_value = False

        result = _wait_for_vram_available("task-1", timeout=1, poll_interval=0.05)

        assert result is False
        assert mock_sleep.call_count >= 1


def test_wait_for_vram_available_respects_cancel():
    """_wait_for_vram_available should return False when the cancel event is set."""
    from unittest.mock import MagicMock

    from mock_api.vram_scheduler import VRAMState
    from mock_api.workers.figure_understanding import _wait_for_vram_available

    scheduler = MagicMock()
    scheduler.state.return_value = VRAMState.OCR_ACTIVE

    cancel_event = MagicMock()
    cancel_event.is_set.return_value = True
    with patch("mock_api.workers.figure_understanding.get_vram_scheduler") as mock_get_scheduler, patch(
        "mock_api.workers.figure_understanding.get_cancel_event"
    ) as mock_get_cancel, patch("mock_api.workers.figure_understanding.time.sleep") as mock_sleep:
        mock_get_scheduler.return_value = scheduler
        mock_get_cancel.return_value = cancel_event

        result = _wait_for_vram_available("task-1", timeout=300, poll_interval=0.05)

        assert result is False
        mock_sleep.assert_not_called()


def test_depth_tasks_schedules_figure_understanding_when_missing(monkeypatch, db_session):
    """DEPTH review with figure_coverage='missing' should schedule figure understanding."""
    import mock_api.depth_tasks as depth_tasks

    fake_result = MagicMock()
    fake_result.model_dump.return_value = {
        "paper_id": "p1",
        "title": "t",
        "has_substance": True,
        "expectation": 0.5,
        "paper_type": "B",
        "secondary_type": "none",
        "confidence": 0.5,
        "novelty_score": 0.5,
        "hotspot_alignment_score": 0.5,
        "core_contribution": "",
        "rigor_score": 0.5,
        "missing_items": [],
        "influence_score": 0.5,
        "reproducibility_score": 0.5,
        "critique_points": [{"point": "test", "severity": "minor"}],
        "defense_points": [],
        "figure_consistency_score": None,
        "figure_flags": [],
        "figure_evidence_count": 0,
        "figure_coverage": "missing",
        "qf_reasoning": "",
        "calibrated_score": 0.5,
        "delta": 0.0,
        "delta_missing": False,
        "chair_reasoning": "",
        "llm_verdict": "major_revision",
        "final_verdict": "major_revision",
        "override_reason": "",
        "evidence_pool": [{"id": "E1", "content": "test evidence"}],
        "evidence_checks": {},
        "evidence_ids": {},
        "base_score": 0.5,
        "weights": {},
        "node_score_stds": {},
        "node_logs": [],
        "evaluated_at": "",
        "q0_reasoning": "",
        "q0_evidence": "",
        "q1_reasoning": "",
        "q2_reasoning": "",
        "q3_reasoning": "",
        "q4_reasoning": "",
    }

    class FakeReviewer:
        def __init__(self, *args, **kwargs):
            pass

        async def review_async_dag(self, **kwargs):
            return fake_result

        def review(self, **kwargs):
            return fake_result

    monkeypatch.setattr("mock_api.depth_eval_v4.DepthReviewer", FakeReviewer)

    scheduled = []

    def fake_submit(paper_id):
        scheduled.append(paper_id)
        return "task-fig-1"

    monkeypatch.setattr(
        "mock_api.workers.figure_understanding.submit_figure_understanding_task",
        fake_submit,
    )

    from mock_api.models import Paper

    paper = Paper(
        id="p1",
        title="Test",
        authors=[],
        abstract="",
        year=2024,
        source="test",
        full_text="some text " * 50,
    )
    db_session.add(paper)
    db_session.commit()

    depth_tasks.run_depth_review_sync("p1")

    assert "p1" in scheduled
