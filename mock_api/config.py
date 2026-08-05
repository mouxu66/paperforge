"""PaperForge 运行时配置（兼容层）—— ⚠️ DEPRECATED since v4.2。

本文件已标记为弃用。新代码请直接从 settings.py 导入：
    from .settings import get_settings
    settings = get_settings()
常量直接 from .settings import CONST_NAME，函数（如 get_compute_mode_config）
计划迁移到 settings.py 或 compute_mode.py，v5.0 将彻底移除本文件。

环境变量的单一事实来源已迁移至 settings.py（pydantic-settings）。
本文件保留向后兼容的常量导出与算力模式相关状态函数，避免一次性改动所有调用方。
新代码请优先：
    from .settings import get_settings
    settings = get_settings()
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

# ── 从 settings.py 导入单一事实来源（兼容旧 import）─────────────────────────
from .settings import get_settings

_settings = get_settings()
__all__ = [
    "MODEL_SWITCH_ENABLED",
    "IS_DEV_ENV",
    "ARXIV_AUTO_FETCH_KEYWORDS",
    "ARXIV_AUTO_FETCH_INTERVAL",
    "ARXIV_AUTO_FETCH_MAX_RESULTS",
    "V4_FINAL_SCORE_CONVERT",
    "OIM_WEIGHT",
    "OIM_FEATURE_WEIGHTS",
    "OIM_MIN_TEXT_CHARS",
    "CALIBRATION_SET_PATH",
    "CALIBRATION_SPEARMAN_THRESHOLD",
    "CONSENSUS_ENABLED",
    "CONSENSUS_SAMPLES",
    "CONSENSUS_TEMPERATURE",
    "SEMANTIC_OVERRIDE_ENABLED",
    "VERDICT_ACCEPT_THRESHOLD",
    "VERDICT_REJECT_THRESHOLD",
    "LOW_CONF_THRESHOLD",
    "ALLOW_COT",
    "HOTSPOTS_SOURCE",
    "DEPTH_FIGURE_EVIDENCE_ENABLED",
    "DEPTH_QF_NODE_ENABLED",
    "DEPTH_FIGURE_WEIGHT",
    "DEPTH_MERGED_SCORING_ENABLED",
    "DEPTH_DELTA_ADAPTIVE_ENABLED",
    "DEPTH_SEVERITY_CLASSIFIER_ENABLED",
    "DEPTH_SEVERITY_MODEL_PATH",
    "DELTA_DEFAULT_MIN",
    "DELTA_DEFAULT_MAX",
    "DEPTH_DELTA_BOUNDS_OVERRIDES",
    "DELTA_HARD_MIN",
    "DELTA_HARD_MAX",
    "DEPTH_SEVERITY_FALLBACK_THRESHOLD",
    "DEPTH_SEVERITY_FATAL_WEIGHT",
    "DEPTH_SEVERITY_MINOR_WEIGHT",
    "DEPTH_Q5C_CLAIM_SEVERITY_FACTOR",
    "DEPTH_CLAIM_VALIDATION_PENALTY_PER_CLAIM",
    "DEPTH_CLAIM_VALIDATION_PENALTY_MAX",
    "get_settings",
    "get_compute_mode_config",
    "get_delta_bounds",
    "get_dynamic_preset",
    "get_compute_mode",
    "set_compute_mode",
    "COMPUTE_MODES",
    "get_depth_severity_fallback_threshold",
    "get_depth_severity_fatal_weight",
    "get_depth_severity_minor_weight",
    "get_depth_q5c_claim_severity_factor",
    "get_depth_claim_validation_bonus_enabled",
    "get_depth_claim_validation_bonus_max",
    "get_depth_claim_validation_bonus_min_valid",
    "get_depth_claim_validation_bonus_per_claim",
    "get_depth_claim_validation_penalty_max",
    "get_depth_claim_validation_penalty_per_claim",
    "get_depth_delta_default_min",
    "get_depth_delta_default_max",
    "get_depth_delta_bounds_overrides",
]

# 是否启用模型切换功能（前端通过 /api/model/current 的 enabled 字段感知）
MODEL_SWITCH_ENABLED: bool = _settings.model_switch_enabled

# 教师版打包接口的安全开关：仅显式开发环境允许调用。
# 🛡️ P0-1 修复：翻转默认为 production（False），避免漏配 ENV 即全网开放 admin 高危端点。
IS_DEV_ENV: bool = _settings.is_dev_env

# arXiv 定时拉取配置（子任务 6）
ARXIV_AUTO_FETCH_KEYWORDS: list[str] = _settings.arxiv_keywords_list
ARXIV_AUTO_FETCH_INTERVAL: int = _settings.arxiv_auto_fetch_interval
ARXIV_AUTO_FETCH_MAX_RESULTS: int = _settings.arxiv_auto_fetch_max_results

# DEPTH v4.1 算法优化特性开关（默认全部关闭，关闭时与当前 v4.1 行为等价）
V4_FINAL_SCORE_CONVERT: bool = _settings.v4_final_score_convert
OIM_WEIGHT: float = _settings.oim_weight
OIM_FEATURE_WEIGHTS: dict[str, float] = _settings.oim_feature_weights
OIM_MIN_TEXT_CHARS: int = _settings.oim_min_text_chars
CALIBRATION_SET_PATH: str = _settings.calibration_set_path or ""
CALIBRATION_SPEARMAN_THRESHOLD: float = _settings.calibration_spearman_threshold
CONSENSUS_ENABLED: bool = _settings.consensus_enabled
CONSENSUS_SAMPLES: int = _settings.consensus_samples
CONSENSUS_TEMPERATURE: float = _settings.consensus_temperature
SEMANTIC_OVERRIDE_ENABLED: bool = _settings.semantic_override_enabled
VERDICT_ACCEPT_THRESHOLD: float = _settings.verdict_accept_threshold
VERDICT_REJECT_THRESHOLD: float = _settings.verdict_reject_threshold
LOW_CONF_THRESHOLD: float = _settings.low_conf_threshold
ALLOW_COT: bool = _settings.allow_cot
HOTSPOTS_SOURCE: str = _settings.hotspots_source

# DEPTH v4.2 算法升级开关（图表证据 / QF 节点 / 合并评分 / 自适应校准 / severity 分类器；默认开启）
DEPTH_FIGURE_EVIDENCE_ENABLED: bool = _settings.depth_figure_evidence_enabled
DEPTH_QF_NODE_ENABLED: bool = _settings.depth_qf_node_enabled
DEPTH_FIGURE_WEIGHT: float = _settings.depth_figure_weight
DEPTH_MERGED_SCORING_ENABLED: bool = _settings.depth_merged_scoring_enabled
DEPTH_DELTA_ADAPTIVE_ENABLED: bool = _settings.depth_delta_adaptive_enabled

# 可训练 severity 分类器开关与模型路径
DEPTH_SEVERITY_CLASSIFIER_ENABLED: bool = _settings.depth_severity_classifier_enabled
DEPTH_SEVERITY_MODEL_PATH: str | None = _settings.depth_severity_model_path

# severity / Q5c 调参（线上 A/B 调优）
# 可通过环境变量动态调整，例如：
#   PAPERFORGE_DEPTH_SEVERITY_FALLBACK_THRESHOLD=0.3
#   PAPERFORGE_DEPTH_SEVERITY_FATAL_WEIGHT=1.5
#   PAPERFORGE_DEPTH_SEVERITY_MINOR_WEIGHT=0.2
#   PAPERFORGE_DEPTH_Q5C_CLAIM_SEVERITY_FACTOR=0.08
#   PAPERFORGE_DEPTH_CLAIM_VALIDATION_PENALTY_PER_CLAIM=0.15
#   PAPERFORGE_DEPTH_CLAIM_VALIDATION_PENALTY_MAX=0.4

# Q5c delta 默认区间（v4.2 从 depth_eval_v4.py 上移到 settings / config）
DELTA_DEFAULT_MIN: float = _settings.depth_delta_default_min
DELTA_DEFAULT_MAX: float = _settings.depth_delta_default_max
# 按论文类型或算力模式覆盖 delta 区间（运行时动态查找）
DEPTH_DELTA_BOUNDS_OVERRIDES: dict = _settings.depth_delta_bounds_overrides

# delta 硬上限不变（算法安全护栏）
DELTA_HARD_MIN: float = -0.25
DELTA_HARD_MAX: float = 0.25

# severity / Q5c 调参常量别名（UPPER_CASE → 兼容旧 importers，v4.2 保留）
DEPTH_SEVERITY_FALLBACK_THRESHOLD: float = _settings.depth_severity_fallback_threshold
DEPTH_SEVERITY_FATAL_WEIGHT: float = _settings.depth_severity_fatal_weight
DEPTH_SEVERITY_MINOR_WEIGHT: float = _settings.depth_severity_minor_weight
DEPTH_Q5C_CLAIM_SEVERITY_FACTOR: float = _settings.depth_q5c_claim_severity_factor
DEPTH_CLAIM_VALIDATION_PENALTY_PER_CLAIM: float = _settings.depth_claim_validation_penalty_per_claim
DEPTH_CLAIM_VALIDATION_PENALTY_MAX: float = _settings.depth_claim_validation_penalty_max


# ── 运行时参数覆盖 + 算力模式 → 已迁至 compute_mode.py ────────────
# 以下函数/状态在 v4.2 已迁移到 compute_mode.py（合并了 compute_config.py），
# 此处通过显式 import 保持向后兼容（v5.0 将移除本文件）。
from .compute_mode import (  # noqa: E402
    # compute mode singleton
    COMPUTE_MODES,
    get_compute_mode,
    get_compute_mode_config,
    get_delta_bounds,
    get_depth_claim_validation_bonus_enabled,
    get_depth_claim_validation_bonus_max,
    get_depth_claim_validation_bonus_min_valid,
    get_depth_claim_validation_bonus_per_claim,
    get_depth_claim_validation_penalty_max,
    get_depth_claim_validation_penalty_per_claim,
    get_depth_delta_bounds_overrides,
    get_depth_delta_default_max,
    get_depth_delta_default_min,
    get_depth_q5c_claim_severity_factor,
    # runtime override helpers
    get_depth_severity_fallback_threshold,
    get_depth_severity_fatal_weight,
    get_depth_severity_minor_weight,
    get_dynamic_preset,
    set_compute_mode,
)


# ═══════════════════════════════════════════════════════════════════
# v4.2 deprecation redirect: explicit re-export via module-level aliases.
#
# Note: settings fields are pydantic class attributes, NOT module-level
# names.  They cannot be imported via ``from .settings import name``.
# Instead, config.py accesses them through ``_settings = get_settings()``
# and re-exports them as SCREAMING_SNAKE_CASE aliases (~40 names above).
#
# The compute_mode dynamic functions are explicitly imported below.
# Remove this entire file in v5.0 — all consumers should use
# ``from .settings import get_settings`` or ``from .compute_mode import ...``
# ═══════════════════════════════════════════════════════════════════
