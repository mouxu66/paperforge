"""感悟报告 4 维 LLM 分的确定性校准层（reflection 侧 per-dim 偏移）。

解决 II（innovative_insights）残余系统性偏高（+0.070，来自 41 篇人工金标 vs
Ornstein-V2 9B 模型配对拟合）。与论文侧 depth_calibration 的「金标回归 offset」同构，
但这里按 4 个维度分别拟合偏差、推理时逐维减去，而不是单一全局平移。

设计约束：
- 纯统计、零 LLM 成本：偏移是离线从 41 篇人工金标拟合的常数。
- 默认开启：PAPERFORGE_REFLECTION_DIM_CALIBRATION 未设或 =1/true/yes/on 时生效。
- 可审计/可重算：偏移落在 calib_papers/runs/reflection_dim_offsets.json，
  由 scripts/calibration/recompute_reflection_dim_offsets.py 从 CSV 重算。
- 单一事实源：偏移只存于 JSON 文件，代码内不再内置偏移副本（曾与 JSON 双写漂移）；
  JSON 缺失/损坏时校准层 fail-open（返回空 dict，恒等），绝不回退到过期常量。
- 可覆盖/可关闭：PAPERFORGE_REFLECTION_DIM_OFFSETS=JSON 逐维覆盖；
  PAPERFORGE_REFLECTION_DIM_CALIBRATION=0 关闭（向后兼容）。
- 只作用于 4 维 LLM 分，绝不触碰 fidelity/coverage（向量层独立保证）。

偏移符号约定：offset[dim] = mean(系统分 - 人工分)；推理时 新分 = clamp(旧分 - offset)。
正值表示系统系统性偏高（需减去），负值表示系统性偏低（需加上）。
"""

from __future__ import annotations

import json
import logging
import os

from .utils.math_utils import clamp_float

logger = logging.getLogger(__name__)

# 4 维 LLM 分（fidelity/coverage 由向量层独立计算，不参与本校准）
DIMENSIONS = (
    "understanding_accuracy",
    "analysis_depth",
    "innovative_insights",
    "evidence_support",
)

_OFFSETS_PATH = os.path.join(
    os.path.dirname(__file__), "..", "calib_papers", "runs", "reflection_dim_offsets.json"
)
_ENABLED_ENV = "PAPERFORGE_REFLECTION_DIM_CALIBRATION"
_OFFSETS_ENV = "PAPERFORGE_REFLECTION_DIM_OFFSETS"

# JSON 文件解析缓存（进程内只读一次；env 每次调用动态读取以支持运行时热切换）
_file_offsets_cache: dict[str, float] | None = None


def _calibration_enabled() -> bool:
    """解析开关。未设（None/''）→ 默认开启（生产生效）；显式 0/false/no/off → 关闭。"""
    raw = os.getenv(_ENABLED_ENV)
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _load_file_offsets() -> dict[str, float]:
    """从 JSON 文件读取偏移（带缓存）。JSON 是唯一事实源。

    缺失/损坏时返回空 dict（校准层 fail-open：apply_dim_offsets 恒等），
    绝不回退到过期的内嵌常量副本——那是过去双写漂移的根源。
    """
    global _file_offsets_cache
    if _file_offsets_cache is not None:
        return _file_offsets_cache
    offs: dict[str, float] = {}
    try:
        with open(_OFFSETS_PATH, encoding="utf-8") as f:
            payload = json.load(f)
        raw = payload.get("offsets", payload) if isinstance(payload, dict) else {}
        if isinstance(raw, dict):
            offs = {
                k: float(v)
                for k, v in raw.items()
                if k in DIMENSIONS and isinstance(v, (int, float, str))
            }
    except Exception as e:  # noqa: BLE001 - calibration I/O - 失败即关闭校准（fail-open）
        logger.warning(
            "reflection_dim_offsets.json 读取失败: %s —— 校准层降级为恒等（不套偏移）", e
        )
        offs = {}
    _file_offsets_cache = dict(offs)
    return _file_offsets_cache


def reset_dim_offsets() -> None:
    """清除 JSON 文件解析缓存（测试用：下次调用重新读文件）。"""
    global _file_offsets_cache
    _file_offsets_cache = None


def get_dim_offsets() -> dict[str, float]:
    """返回各维要减去的系统偏差（offset）。关闭时返回空 dict（恒等）。"""
    if not _calibration_enabled():
        return {}
    raw = os.getenv(_OFFSETS_ENV)
    if raw:
        try:
            env_offs = json.loads(raw)
            if isinstance(env_offs, dict):
                return {
                    k: float(v)
                    for k, v in env_offs.items()
                    if k in DIMENSIONS and isinstance(v, (int, float, str))
                }
        except Exception as e:  # noqa: BLE001 - calibration config - 非法 env 兜底
            logger.warning("PAPERFORGE_REFLECTION_DIM_OFFSETS 解析失败: %s，回退文件偏移", e)
    return _load_file_offsets()


def apply_dim_offsets(scores: dict) -> dict:
    """对 4 维 LLM 分逐维减去系统偏差，返回新 dict（原 dict 不动）。

    - 只触碰 DIMENSIONS 中的键；fidelity/coverage/verdict 等原样透传。
    - None 值原样保留（LLM 失败时 4 维为 None，校准不得把它变成假分数）。
    - 结果 clamp 到 [0, 1]。
    """
    if not scores:
        return dict(scores)
    offsets = get_dim_offsets()
    if not offsets:
        return dict(scores)
    out = dict(scores)
    for dim in DIMENSIONS:
        v = out.get(dim)
        if v is None:
            continue
        try:
            out[dim] = clamp_float(float(v) - offsets[dim], 0.0, 1.0)
        except (KeyError, TypeError, ValueError):  # noqa: BLE001 - 单维异常不阻断其余维度
            continue
    return out
