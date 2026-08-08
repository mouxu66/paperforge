"""可复现性基座（ADR-014 · P0）。

解决 W5（同篇论文每次跑分可能不一致）：

- 默认行为不变：未设置 PAPERFORGE_EVAL_SEED 时，不向 LLM 注入种子，
  与历史行为完全一致（向后兼容）。
- 设置 PAPERFORGE_EVAL_SEED=N 后，所有评测 LLM 调用自动带上固定 ``seed=N``，
  配合固定 temperature 即可保证「同篇论文重跑分数一致」。
- ``get_llm_params_snapshot()`` 返回本次评测实际使用的模型 / 提供商 / 温度 / 种子，
  供结果存档，使任何一次评分都可被他人精确复现。

fail-open：环境变量解析失败时静默回退（不抛异常，不影响主流程）。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import BaseLLMProvider

logger = logging.getLogger(__name__)

_ENV_SEED = "PAPERFORGE_EVAL_SEED"


def get_eval_seed() -> int | None:
    """读取评测固定种子。

    Returns:
        整数种子；未设置或非法时返回 ``None``（表示沿用模型默认随机行为）。
    """
    raw = os.environ.get(_ENV_SEED)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        try:
            # 容忍 "42.0" 这类浮点写法
            return int(float(raw))
        except (TypeError, ValueError):
            logger.warning("忽略非法环境变量 %s=%r，回退到非固定种子", _ENV_SEED, raw)
            return None


def with_eval_seed(kwargs: dict[str, Any]) -> dict[str, Any]:
    """把种子注入 LLM 调用 kwargs（仅在调用方未显式给定 seed 时）。

    kwargs 会原地被修改并返回，便于链式调用：
        extra_kwargs = with_eval_seed(extra_kwargs)
    """
    if "seed" not in kwargs:
        seed = get_eval_seed()
        if seed is not None:
            kwargs["seed"] = seed
    return kwargs


def get_llm_params_snapshot(provider: BaseLLMProvider | None = None) -> dict[str, Any]:
    """返回当前评测所用 LLM 参数的快照，用于结果存档与复现。

    包含：``seed``（来自 PAPERFORGE_EVAL_SEED）、``temperature``、
    ``model``、``provider``、``base_url``。若 provider 为 None 仅返回 seed。
    """
    snap: dict[str, Any] = {}
    seed = get_eval_seed()
    if seed is not None:
        snap["seed"] = seed
    if provider is not None:
        snap["model"] = getattr(provider, "model", None)
        snap["provider"] = getattr(provider, "provider_name", None)
        snap["base_url"] = getattr(provider, "base_url", None)
        # 本地 llama.cpp provider 暴露 temperature；OpenAI 兼容 provider 也支持
        temp = getattr(provider, "temperature", None)
        if temp is not None:
            snap["temperature"] = temp
    # 算力配置里的全局默认温度（与 DEPTH 实际调用一致）
    try:
        from .. import depth_eval_v4 as _dv4  # noqa: PLC0415 - 延迟导入避免顶层循环依赖

        _t, _m = _dv4._get_llm_params()
        snap.setdefault("temperature", _t)
        snap["max_tokens"] = _m
    except Exception:  # noqa: BLE001 - 快照仅为存档，缺失不影响主流程
        pass
    return snap
