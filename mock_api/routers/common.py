"""跨域共享工具函数（供 chat / reports / depth 等多个 router 复用）。

这些函数原本散落在 mock_api/main.py 中，抽取到此处以避免各 router
反向依赖 main.py，同时消除 writing.py 中的重复定义。
"""

from __future__ import annotations

import json


def sse_event(data: dict) -> str:
    """将 dict 打包为 SSE data: 行（含双换行）。

    供 chat（流式问答）、writing（续写/大纲生成）等 SSE 端点统一使用。
    公开名称去掉前导下划线，正式标记为可复用 API。
    """
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# 保留旧名称作为别名，向后兼容仍引用 _sse_event 的代码
_sse_event = sse_event


def safe_dict(v) -> dict:
    """安全地把 JSON 列值转成 dict。None / 非 dict → 空 dict。

    用途：兼容老 v4.1 记录（reflection_result / q2_result / final_verdict
    可能是 None / 非 dict / JSON 损坏）。统一兜底防前端解析报错白屏。
    """
    if isinstance(v, dict):
        return v
    return {}


# 向后兼容别名
_safe_dict = safe_dict


def as_dict(v) -> dict:
    """安全地把 JSON 列值归一为 dict，兼容历史字符串存储。

    与 safe_dict 的区别：若值是 JSON 字符串（老记录曾把 reflection_result
    存成字符串），会尝试 json.loads 还原为 dict。/api/stats 曾因字符串
    列直接 .get() 触发 AttributeError: 'str' object has no attribute 'get'，
    此处统一兜底，消除同类潜伏隐患。
    """
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
        except (json.JSONDecodeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


# 向后兼容别名
_as_dict = as_dict
