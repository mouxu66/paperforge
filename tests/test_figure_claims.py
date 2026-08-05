"""Tests for mock_api.figure_claims.

Covers:
- Metric/value claim extraction from figure captions/source text.
- Source text span extraction around figure references.
- axis_info JSON parsing with markdown fences and missing fields.
- validate_claim_with_axis range checks and metric matching.
"""

from __future__ import annotations

import pytest

from mock_api.figure_claims import (
    extract_claims_from_text,
    extract_source_text_span,
    parse_axis_info,
    validate_claim_with_axis,
)


def test_extract_claims_from_text_finds_metric_value():
    text = "Our method achieves 85.2% accuracy, which outperforms baseline B by 3.1%."
    claims = extract_claims_from_text(text)
    assert claims
    assert any(c["metric"] == "accuracy" and c["value"] == 85.2 for c in claims)


def test_extract_claims_from_text_normalizes_loss():
    text = "The training loss drops to 0.12, lower than baseline."
    claims = extract_claims_from_text(text)
    assert any(c["metric"] == "loss" and c["value"] == 0.12 for c in claims)


def test_extract_claims_from_text_comparative_operator():
    text = "Model A is on par with Model B at 90.5% precision."
    claims = extract_claims_from_text(text)
    assert any(c["metric"] == "precision" and c["operator"] == "=" for c in claims)


def test_extract_claims_from_text_no_numbers():
    assert extract_claims_from_text("No numbers here.") == []


def test_extract_source_text_span_finds_reference_context():
    full_text = (
        "We trained several models. "
        "As shown in Figure 2, our method reaches 88% accuracy. "
        "This surpasses prior work."
    )
    span = extract_source_text_span(full_text, 2)
    assert span is not None
    assert "Figure 2" in span
    assert "88%" in span


def test_extract_source_text_span_none_for_missing_number():
    assert extract_source_text_span("some text", None) is None


def test_parse_axis_info_with_markdown_fence():
    raw = "```json\n{\"x_label\": \"epoch\", \"y_label\": \"accuracy\", \"y_ticks\": [80, 85, 90]}\n```"
    info = parse_axis_info(raw)
    assert info is not None
    assert info["y_label"] == "accuracy"
    assert info["y_ticks"] == [80.0, 85.0, 90.0]


def test_parse_axis_info_missing_fields_are_empty():
    raw = '{"x_label": "time"}'
    info = parse_axis_info(raw)
    assert info is not None
    assert info["x_label"] == "time"
    assert info["y_ticks"] == []


def test_parse_axis_info_string_numbers():
    raw = '{"y_ticks": ["0.75", "0.85"]}'
    info = parse_axis_info(raw)
    assert info is not None
    assert info["y_ticks"] == [0.75, 0.85]


def test_parse_axis_info_invalid_returns_none():
    assert parse_axis_info("not json") is None


def test_validate_claim_with_axis_valid_range():
    claim = {"metric": "accuracy", "value": 88.0}
    axis = {"y_label": "Accuracy", "y_ticks": [80, 90]}
    result = validate_claim_with_axis(claim, axis)
    assert result["valid"] is True
    assert result["metric_matched"] is True
    assert result["actual_value"] == 88.0
    assert result["axis_min"] == 80.0
    assert result["axis_max"] == 90.0
    assert result["expected_range"] == [80.0, 90.0]
    assert result["axis_range"] == "[80, 90]"


def test_validate_claim_with_axis_out_of_range():
    claim = {"metric": "accuracy", "value": 95.0}
    axis = {"y_label": "Accuracy", "y_ticks": [80, 90]}
    result = validate_claim_with_axis(claim, axis)
    assert result["valid"] is False
    assert result["actual_value"] == 95.0
    assert result["expected_range"] == [80.0, 90.0]


def test_validate_claim_with_axis_mismatch():
    claim = {"metric": "loss", "value": 0.2}
    axis = {"y_label": "Accuracy", "y_ticks": [80, 90]}
    result = validate_claim_with_axis(claim, axis)
    assert result["metric_matched"] is False
    assert result["valid"] is None
    # Axis bounds should still be recorded for diagnosis
    assert result["actual_value"] == 0.2
    assert result["expected_range"] == [80.0, 90.0]


def test_validate_claim_with_axis_no_ticks():
    claim = {"metric": "accuracy", "value": 85.0}
    axis = {"y_label": "Accuracy"}
    result = validate_claim_with_axis(claim, axis)
    assert result["valid"] is None
    assert result["actual_value"] == 85.0
    assert "expected_range" not in result


def test_apply_curve_correction_flips_valid_when_value_in_curve_range():
    from mock_api.figure_claims import apply_curve_correction

    claim_validation = {
        "claims": [
            {"metric": "accuracy", "value": 0.92},
            {"metric": "accuracy", "value": 1.05},
            {"metric": "loss", "value": 0.5},
        ],
        "validated": [
            {"valid": False, "metric_matched": True, "axis_range": "[0.8, 0.9]"},
            {"valid": False, "metric_matched": True, "axis_range": "[0.8, 0.9]"},
            {"valid": True, "metric_matched": True, "axis_range": "[0, 1]"},
        ],
    }
    curve_points = [{"x": 1, "y": 0.85}, {"x": 2, "y": 0.92}, {"x": 3, "y": 0.88}]

    corrected = apply_curve_correction(claim_validation, curve_points)

    assert corrected == 1
    assert claim_validation["validated"][0]["valid"] is True
    assert claim_validation["validated"][0]["curve_corrected"] is True
    assert claim_validation["validated"][0]["curve_y_min"] == 0.85
    assert claim_validation["validated"][0]["curve_y_max"] == 0.92
    assert "within curve y-range" in claim_validation["validated"][0]["reason"]
    assert claim_validation["validated"][1]["valid"] is False
    assert claim_validation["validated"][1].get("curve_corrected") is None
    assert claim_validation["validated"][2]["valid"] is True


def test_apply_curve_correction_returns_zero_when_no_curve_points():
    from mock_api.figure_claims import apply_curve_correction

    claim_validation = {
        "claims": [{"metric": "accuracy", "value": 0.92}],
        "validated": [{"valid": False, "metric_matched": True}],
    }
    assert apply_curve_correction(claim_validation, None) == 0
    assert apply_curve_correction(claim_validation, []) == 0
    assert claim_validation["validated"][0]["valid"] is False
