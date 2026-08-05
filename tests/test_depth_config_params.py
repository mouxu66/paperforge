"""Tests for DEPTH severity / Q5c configurable parameters."""

from __future__ import annotations

from typing import Any


class TestDepthSeverityConfigDefaults:
    """验证所有新 severity / Q5c 可调参数都有符合历史的默认值。"""

    def test_severity_fallback_threshold_default(self) -> None:
        from mock_api.config import DEPTH_SEVERITY_FALLBACK_THRESHOLD

        assert isinstance(DEPTH_SEVERITY_FALLBACK_THRESHOLD, float)
        assert DEPTH_SEVERITY_FALLBACK_THRESHOLD == 0.5

    def test_severity_weights_default(self) -> None:
        from mock_api.config import (
            DEPTH_SEVERITY_FATAL_WEIGHT,
            DEPTH_SEVERITY_MINOR_WEIGHT,
        )

        assert DEPTH_SEVERITY_FATAL_WEIGHT == 1.0
        assert DEPTH_SEVERITY_MINOR_WEIGHT == 0.25

    def test_q5c_claim_severity_factor_default(self) -> None:
        from mock_api.config import DEPTH_Q5C_CLAIM_SEVERITY_FACTOR

        assert DEPTH_Q5C_CLAIM_SEVERITY_FACTOR == 0.05

    def test_claim_validation_penalty_defaults(self) -> None:
        from mock_api.config import (
            DEPTH_CLAIM_VALIDATION_PENALTY_MAX,
            DEPTH_CLAIM_VALIDATION_PENALTY_PER_CLAIM,
        )

        assert DEPTH_CLAIM_VALIDATION_PENALTY_PER_CLAIM == 0.1
        assert DEPTH_CLAIM_VALIDATION_PENALTY_MAX == 0.3
