"""DEPTH 评估共享工具模块。

本模块集中存放 `depth_eval.py`、`depth_eval_v4.py`、`depth_eval_reflection.py`
之间重复出现的通用工具函数，减少代码重复并避免漂移。

当前包含：
- 数值/列表夹紧工具（clamp_float, clamp_list）
- 文本截断工具（truncate_text, smart_truncate）
- 通用重试封装（call_with_retry）

设计原则：
- 只放真正在多个 DEPTH 模块中重复出现的纯工具函数。
- 不涉及业务逻辑、LLM prompt 或数据库操作，保持最小依赖。
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数值 / 列表夹紧
# ---------------------------------------------------------------------------


def clamp_float(val: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    """将值转为 float 并限制在 [lo, hi] 范围内。

    转换失败时返回区间中点 (lo + hi) / 2。
    """
    try:
        f = float(val)
    except (TypeError, ValueError):
        return (lo + hi) / 2
    return max(lo, min(hi, f))


def clamp_list(val: Any, max_len: int = 5) -> list[str]:
    """将值转为字符串列表，去除空项并截断。

    非列表输入返回空列表。
    """
    if not isinstance(val, list):
        return []
    return [str(item).strip() for item in val if str(item).strip()][:max_len]


# ---------------------------------------------------------------------------
# 文本截断
# ---------------------------------------------------------------------------


def truncate_text(text: str, max_chars: int = 4000) -> str:
    """截断文本到指定字符数，尽量在句子边界截断。

    优先在句号/感叹号/问号处截断，其次逗号，最后硬截断并追加省略号。
    """
    if not text:
        return ""
    if len(text) <= max_chars:
        return text

    truncated = text[:max_chars]
    last_period = max(truncated.rfind("。"), truncated.rfind("！"), truncated.rfind("？"))
    if last_period > max_chars * 0.7:
        return truncated[: last_period + 1]

    last_comma = max(truncated.rfind("，"), truncated.rfind(","), truncated.rfind("、"))
    if last_comma > max_chars * 0.6:
        return truncated[: last_comma + 1]

    return truncated + "..."


# ---------------------------------------------------------------------------
# 通用重试封装
# ---------------------------------------------------------------------------


def call_with_retry(fn, max_retries: int = 2, delay: int = 5):
    """带重试的函数调用，失败时返回 None。

    Args:
        fn: 无参可调用对象。
        max_retries: 最大重试次数（不含首次）。
        delay: 重试间隔（秒）。
    """
    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - 工具函数 - 边界条件兜底
            last_err = e
            logger.warning("调用失败（attempt=%d/%d）：%s", attempt + 1, max_retries + 1, e)
            if attempt < max_retries:
                time.sleep(delay)
    logger.error("调用最终失败：%s", last_err)
    return None
