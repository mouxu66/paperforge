"""Numerical claim extraction from figure-related text.

P0-1: Extract structured numerical claims from caption / source text spans.
P1: Validate claims against axis_info ranges.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metric / label normalization map
# ---------------------------------------------------------------------------
_METRIC_LABEL_MAP: dict[str, set[str]] = {
    "accuracy": {"accuracy", "acc", "acc.", "top-1", "top1"},
    "loss": {"loss", "training loss", "val loss", "validation loss"},
    "precision": {"precision"},
    "recall": {"recall"},
    "f1": {"f1", "f1-score"},
    "error rate": {"error rate", "error"},
    "bleu": {"bleu"},
    "rouge": {"rouge"},
    "auc": {"auc", "auc-roc", "roc-auc"},
    "ap": {"ap", "average precision"},
}

# Reverse index for fast lookup
_LABEL_TO_METRIC: dict[str, str] = {}
for _metric, _labels in _METRIC_LABEL_MAP.items():
    for _label in _labels:
        _LABEL_TO_METRIC[_label] = _metric


def _normalize_metric(token: str) -> str | None:
    """Map a raw metric token/phrase to canonical metric name."""
    token = token.strip().lower()
    if not token:
        return None
    # Direct hit only; partial matches cause false positives (e.g. "a" -> accuracy)
    return _LABEL_TO_METRIC.get(token)


# ---------------------------------------------------------------------------
# Number parsing
# ---------------------------------------------------------------------------
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])"  # not preceded by alnum
    r"(?P<sign>-)?"
    r"(?P<integer>(?:(?<=\s)\d{1,3}(?:,\d{3})+|\d+))"
    r"(?:\.(?P<decimal>\d+))?"
    r"(?:[eE](?P<exp>[-+]?\d+))?"
    r"(?P<percent>\s*%)?"
    r"(?![A-Za-z0-9])"
)


def _parse_number(raw: str) -> float | None:
    """Parse a number string that may contain commas, percent signs, or scientific notation."""
    text = raw.strip().replace(",", "")
    # Remove trailing percent and remember it
    is_percent = "%" in text
    text = text.replace("%", "")
    text = text.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if is_percent and abs(value) <= 100:
        # Already in percent form (e.g. 85.2%), keep as-is
        return value
    return value


# ---------------------------------------------------------------------------
# Comparison operator mapping
# ---------------------------------------------------------------------------
_COMPARATIVE_KEYWORDS: dict[str, str] = {
    # greater
    "outperform": ">",
    "outperforms": ">",
    "outperformed": ">",
    "exceed": ">",
    "exceeds": ">",
    "exceeded": ">",
    "surpass": ">",
    "surpasses": ">",
    "better": ">",
    "higher": ">",
    "larger": ">",
    "superior": ">",
    "best": ">",
    "peak": ">",
    "top": ">",
    # lower
    "lower": "<",
    "less": "<",
    "smaller": "<",
    "worse": "<",
    "underperform": "<",
    "underperforms": "<",
    # equal / comparable
    "match": "=",
    "matches": "=",
    "comparable": "=",
    "on par with": "=",
    "similar": "=",
    "same as": "=",
}


def _detect_operator(sentence: str) -> str:
    lower = sentence.lower()
    # Check longer phrases first to avoid false partial matches
    for phrase in ["on par with", "same as", "comparable to"]:
        if phrase in lower:
            return "="
    for kw, op in _COMPARATIVE_KEYWORDS.items():
        # Use word boundary matching
        if re.search(rf"\b{re.escape(kw)}\b", lower):
            return op
    return "="


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------
def _split_sentences(text: str) -> list[str]:
    """Simple sentence splitter that keeps abbreviations like Fig./et al. intact."""
    # Normalize Figure/Fig. spacing without splitting the word "Figure"
    text = re.sub(r"\b(Fig\.|Figure)\s*", r"\1 ", text, flags=re.IGNORECASE)
    # Split on period + space, but be careful with decimals
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()]


def extract_claims_from_text(text: str) -> list[dict[str, Any]]:
    """Extract numerical claims from a caption or source text span.

    Returns a list of dicts with keys: metric, value, operator, comparator, context.
    """
    if not text:
        return []

    claims: list[dict[str, Any]] = []
    sentences = _split_sentences(text)

    for sentence in sentences:
        numbers = _NUMBER_RE.findall(sentence)
        if not numbers:
            continue

        operator = _detect_operator(sentence)
        # Try to find a comparator phrase (e.g. "outperforms baseline B")
        comparator = None
        comparator_match = re.search(
            r"\b(?:than|over|against|vs\.?|versus|compared to|with)\s+([A-Za-z][A-Za-z0-9\-\,]{0,40})",
            sentence,
            flags=re.IGNORECASE,
        )
        if comparator_match:
            comparator = comparator_match.group(1).strip().rstrip(".,;:")

        # Metric extraction: look in a window around each number
        for match in re.finditer(_NUMBER_RE, sentence):
            raw_number = match.group(0)
            value = _parse_number(raw_number)
            if value is None:
                continue

            start, end = match.start(), match.end()
            # Look at ±3 tokens around the number for metric
            window_start = max(0, sentence.rfind(" ", 0, start) - 80)
            window_end = min(len(sentence), sentence.find(" ", end) + 80)
            if window_end == -1 or window_end <= window_start:
                window_end = len(sentence)
            window = sentence[window_start:window_end]

            # Try to find metric by matching known labels
            metric = None
            # Tokenize and check bigrams/unigrams
            words = re.findall(r"[A-Za-z][A-Za-z0-9\-]*", window)
            for i in range(len(words)):
                for j in range(i + 1, min(i + 5, len(words) + 1)):
                    phrase = " ".join(words[i:j]).lower()
                    normalized = _normalize_metric(phrase)
                    if normalized:
                        metric = normalized
                        break
                if metric:
                    break

            # If no metric found in window, skip this number
            if not metric:
                continue

            claims.append(
                {
                    "metric": metric,
                    "value": value,
                    "operator": operator,
                    "comparator": comparator,
                    "context": sentence.strip(),
                }
            )

    # Deduplicate identical (metric, value, operator, comparator) claims
    seen: set[tuple[str, float, str, str | None]] = set()
    unique_claims: list[dict[str, Any]] = []
    for c in claims:
        key = (c["metric"], round(c["value"], 6), c["operator"], c.get("comparator"))
        if key not in seen:
            seen.add(key)
            unique_claims.append(c)

    return unique_claims


# ---------------------------------------------------------------------------
# Source text span extraction
# ---------------------------------------------------------------------------
def extract_source_text_span(full_text: str, figure_number: int | None) -> str | None:
    """Extract sentences from full_text that reference the given figure number.

    If figure_number is None, returns None. Collects the matching sentence plus
    one sentence of context on each side.
    """
    if not full_text or figure_number is None:
        return None

    sentences = _split_sentences(full_text)
    if not sentences:
        return None

    pattern = re.compile(
        rf"\b(?:Figure|Fig\\.?\s*)\s*{re.escape(str(figure_number))}\b",
        flags=re.IGNORECASE,
    )

    result_parts: list[str] = []
    for i, sentence in enumerate(sentences):
        if pattern.search(sentence):
            start = max(0, i - 1)
            end = min(len(sentences), i + 2)
            result_parts.append(" ".join(sentences[start:end]))

    if not result_parts:
        return None

    return " ".join(result_parts)


# ---------------------------------------------------------------------------
# Axis info helpers
# ---------------------------------------------------------------------------
def _normalize_axis_info_dict(parsed: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a raw axis_info dict into the canonical shape.

    Handles missing fields, string numbers, and list normalization.
    Returns None if the input is not a dict.
    """
    if not isinstance(parsed, dict):
        return None

    axis_info: dict[str, Any] = {
        "x_label": (parsed.get("x_label") or "").strip(),
        "y_label": (parsed.get("y_label") or "").strip(),
        "x_ticks": [],
        "y_ticks": [],
        "legend_items": [],
    }

    def to_floats(values: Any) -> list[float]:
        result: list[float] = []
        if not isinstance(values, list):
            return result
        for v in values:
            try:
                result.append(float(v))
            except (TypeError, ValueError):
                pass
        return result

    axis_info["x_ticks"] = to_floats(parsed.get("x_ticks"))
    axis_info["y_ticks"] = to_floats(parsed.get("y_ticks"))

    legend = parsed.get("legend_items")
    if isinstance(legend, list):
        axis_info["legend_items"] = [str(item).strip() for item in legend if item]

    return axis_info


def _try_parse_axis_info(raw: str) -> dict[str, Any] | None:
    """Best-effort parse of axis_info JSON from a model response.

    Handles markdown fences, string numbers, and missing fields.
    """
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except Exception:
        logger.debug("axis_info JSON parse failed for raw: %s", text[:200])
        return None
    return _normalize_axis_info_dict(parsed)


def parse_axis_info(raw: str | dict[str, Any]) -> dict[str, Any] | None:
    """Public entry point for parsing/normalizing axis_info.

    Accepts either a raw JSON string (possibly wrapped in markdown fences) or an
    already-parsed dict. Returns None on any failure so callers can fall back
    gracefully.
    """
    if isinstance(raw, dict):
        return _normalize_axis_info_dict(raw)
    try:
        return _try_parse_axis_info(raw)
    except Exception:
        return None


def normalize_axis_info(parsed: dict[str, Any] | None) -> dict[str, Any] | None:
    """Normalize an already-parsed axis_info dict (e.g. from a model JSON response).

    Returns None if the input is None or not a dict.
    """
    if parsed is None:
        return None
    return _normalize_axis_info_dict(parsed)


# ---------------------------------------------------------------------------
# Curve points correction
# ---------------------------------------------------------------------------
_CURVE_Y_TOLERANCE = 0.05


def apply_curve_correction(
    claim_validation: dict[str, Any] | None,
    curve_points: list[dict[str, Any]] | None,
) -> int:
    """Flip out-of-range claims to valid when their value falls within curve y-range.

    Mutates ``claim_validation`` in place: for any validated item with
    ``valid is False`` and ``metric_matched is True``, if the claim value lies
    within the empirical y-range of ``curve_points``, the item is marked
    ``valid=True`` and ``curve_corrected=True``.

    Returns the number of corrected claims.
    """
    if not claim_validation or not isinstance(claim_validation, dict):
        return 0
    validated = claim_validation.get("validated")
    claims = claim_validation.get("claims")
    if not isinstance(validated, list):
        return 0

    y_min: float | None = None
    y_max: float | None = None
    if isinstance(curve_points, list) and curve_points:
        ys = [
            float(p["y"])
            for p in curve_points
            if isinstance(p, dict) and isinstance(p.get("y"), (int, float))
        ]
        if ys:
            y_min, y_max = min(ys), max(ys)
    if y_min is None or y_max is None:
        return 0

    corrected = 0
    tol = max(1e-9, abs(y_max - y_min) * _CURVE_Y_TOLERANCE)
    for idx, v in enumerate(validated):
        if not isinstance(v, dict):
            continue
        if v.get("valid") is not False:
            continue
        if not v.get("metric_matched"):
            continue
        claim = claims[idx] if isinstance(claims, list) and idx < len(claims) else None
        if not isinstance(claim, dict):
            continue
        value = claim.get("value")
        try:
            value_f = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if y_min - tol <= value_f <= y_max + tol:
            v["valid"] = True
            v["curve_corrected"] = True
            v["curve_y_min"] = y_min
            v["curve_y_max"] = y_max
            v["reason"] = (
                f"value {value_f} outside axis range "
                f"{v.get('axis_range', '[?, ?]')} but within curve y-range "
                f"[{y_min}, {y_max}]"
            )
            corrected += 1
    return corrected


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_claim_with_axis(
    claim: dict[str, Any],
    axis_info: dict[str, Any] | None,
) -> dict[str, Any]:
    """Check whether a numeric claim's value lies within the axis range.

    Returns a dict with:
      - valid: bool | None (None if unable to determine)
      - reason: human-readable explanation
      - metric_matched: whether the claim metric semantically matches the axis label
      - actual_value: the numeric value being validated
      - axis_min / axis_max: the min/max y-axis ticks
      - expected_range: [axis_min, axis_max]
      - axis_range: formatted string "[min, max]" for the y-axis range
    """
    result: dict[str, Any] = {
        "valid": None,
        "reason": "unable to validate",
        "metric_matched": False,
    }

    value = claim.get("value")
    if value is None:
        result["reason"] = "claim has no numeric value"
        return result

    # Always record the claim value, even if axis_info is unavailable
    try:
        result["actual_value"] = float(value)
    except (TypeError, ValueError):
        result["reason"] = "claim value is not numeric"
        return result

    if not axis_info:
        result["reason"] = "axis_info unavailable"
        return result

    y_ticks = axis_info.get("y_ticks") or []
    if not y_ticks:
        result["reason"] = "y_ticks unavailable"
        return result

    tick_min, tick_max = min(y_ticks), max(y_ticks)
    result["axis_min"] = tick_min
    result["axis_max"] = tick_max
    result["expected_range"] = [tick_min, tick_max]
    result["axis_range"] = f"[{tick_min}, {tick_max}]"

    # Metric-label matching
    claim_metric = claim.get("metric")
    y_label = (axis_info.get("y_label") or "").lower()
    if claim_metric and y_label:
        canonical = _normalize_metric(y_label)
        if (
            canonical
            and canonical == claim_metric
            or claim_metric in y_label
            or y_label in claim_metric
        ):
            result["metric_matched"] = True

    if not result["metric_matched"]:
        result["reason"] = "metric/label mismatch"
        return result

    if tick_min <= value <= tick_max:
        result["valid"] = True
        result["reason"] = f"value {value} within axis range [{tick_min}, {tick_max}]"
    else:
        result["valid"] = False
        result["reason"] = f"value {value} outside axis range [{tick_min}, {tick_max}]"

    return result
