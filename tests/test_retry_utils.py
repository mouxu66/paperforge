"""Tests for mock_api.retry_utils tenacity retry decorators."""

from __future__ import annotations

import pytest
from mock_api.retry_utils import llm_retry


def test_llm_retry_retries_runtime_error():
    """RuntimeError from local model inference should be retried."""
    calls = []

    @llm_retry
    def _flaky_llm() -> str:
        calls.append(1)
        if len(calls) < 2:
            raise RuntimeError("model load failed")
        return "ok"

    assert _flaky_llm() == "ok"
    assert len(calls) == 2
