"""extractors/figures.py —— v4.2 从 depth_eval_v4.py 拆分的图表辅助函数。

迁移函数：
- _format_evidence_pool_text
- _format_axis_info
- _clamp_list
- _MAX_AXIS_TICKS, _MAX_LEGEND_ITEMS
"""

from __future__ import annotations

from typing import Any

_MAX_AXIS_TICKS = 10
_MAX_LEGEND_ITEMS = 6


def _format_evidence_pool_text(pool: dict[str, str]) -> str:
    """格式化证据池为 LLM 可读文本（内容已含关键词）。"""
    if not pool:
        return "（空）"
    lines = [f"  {eid}: {content}" for eid, content in pool.items()]
    return "\n".join(lines)


def _format_axis_info(axis_info: dict[str, Any] | None) -> str:
    """将 axis_info 格式化为 QF prompt 中的紧凑文本。"""
    if not axis_info:
        return ""

    def _str(val: Any) -> str:
        if isinstance(val, (list, tuple)):
            return "[" + ", ".join(str(v) for v in val) + "]"
        return str(val)

    x_label = axis_info.get("x_label") or axis_info.get("xlabel")
    y_label = axis_info.get("y_label") or axis_info.get("ylabel")
    x_ticks = axis_info.get("x_ticks") or axis_info.get("xticks")
    y_ticks = axis_info.get("y_ticks") or axis_info.get("yticks")
    legend_items = axis_info.get("legend_items") or axis_info.get("legend")

    parts: list[str] = []
    if x_label:
        parts.append(f"x_label={x_label}")
    if y_label:
        parts.append(f"y_label={y_label}")
    if x_ticks:
        parts.append(f"x_ticks={_str(x_ticks[:_MAX_AXIS_TICKS])}")
    if y_ticks:
        parts.append(f"y_ticks={_str(y_ticks[:_MAX_AXIS_TICKS])}")
    if legend_items:
        parts.append(f"legend={_str(legend_items[:_MAX_LEGEND_ITEMS])}")
    if not parts:
        return ""
    return "AxisInfo: " + ", ".join(parts)


def _clamp_list(val: Any, max_len: int = 10) -> list[Any]:
    if not isinstance(val, list):
        return []
    return val[:max_len]
