"""PaperForge 集中式配置（ADR-003）。

使用 pydantic-settings BaseSettings 统一管理所有环境变量配置，
替代散落在 config.py / auth.py / tasks.py / depth_tasks.py / database.py 等
模块中的 os.getenv 调用。

设计原则：
- 所有配置项以 PAPERFORGE_ 前缀（除 ENV 保持兼容）
- 安全默认值优先（IS_DEV_ENV 默认 False，AUTH_ENABLED 默认 True）
- 启动时 fail-fast：非法配置立即报错而非运行时崩溃
- 单一事实来源：其他模块从 get_settings() 获取配置，不再直接读 env

用法：
    from .settings import get_settings
    settings = get_settings()
    if settings.is_dev_env:
        ...
"""

from __future__ import annotations

import functools
import json
import logging
import os
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# .env 文件路径：优先项目根目录，回退 CWD 相对路径
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_ENV_FILE = str(_ENV_PATH) if _ENV_PATH.exists() else ".env"


class Settings(BaseSettings):
    """PaperForge 全局配置（单一事实来源）。

    所有字段从环境变量读取，支持 .env 文件。
    环境变量名 = PAPERFORGE_ + 字段名大写（如 PAPERFORGE_DB_PATH）。
    """

    model_config = SettingsConfigDict(
        env_prefix="PAPERFORGE_",
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── 安全 / 环境 ──────────────────────────────────────────────
    # ENV 保持无前缀兼容（原 IS_DEV_ENV = os.getenv("ENV", "production") == "development"）
    # 向后兼容：ENV=development（无 PAPERFORGE_ 前缀）仍可触发开发态
    is_dev_env: bool = Field(
        default=False,
        validation_alias=AliasChoices("PAPERFORGE_IS_DEV_ENV", "ENV"),
        description=(
            "开发模式开关（默认 False=生产安全态）。"
            "支持 ENV=development / PAPERFORGE_IS_DEV_ENV=1（或 true/yes/on/development）启用。"
        ),
    )

    @field_validator("is_dev_env", mode="before")
    @classmethod
    def _validate_is_dev_env(cls, v):
        """兼容 ENV=development/production 字符串以及常规布尔值 / 开关量。

        历史坑：早期文档与旧别名暗示 PAPERFORGE_IS_DEV_ENV=1 可开启开发态，
        但原判定仅认字符串 'development'，导致 =1 静默失效（admin 端点 403）。
        现放宽容忍集：'1' / 'true' / 'yes' / 'on' / 'development'（不区分大小写）均视为 True。
        """
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on", "development")
        return bool(v)

    auth_enabled: bool = Field(
        default=True,
        description="鉴权开关（默认 True）。设 PAPERFORGE_AUTH_ENABLED=0 可禁用（仅调试用）。",
    )
    api_token: str | None = Field(
        default=None,
        description="API Token，非空时所有写操作需带 X-PaperForge-Token 头。",
    )
    allow_remote: bool = Field(
        default=False,
        description="放宽 loopback 限制，允许远端访问（仅受信任内网使用）。",
    )
    build_allowed: bool = Field(
        default=False,
        description="admin 打包端点开关（默认 False）。仅在开发态 + 显式开启时可用。",
    )
    trusted_proxies: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("PAPERFORGE_TRUSTED_PROXIES", "TRUSTED_PROXIES"),
        description=(
            '可信反向代理 IP 列表（JSON 数组格式，如 ["127.0.0.1","10.0.0.5"]）。'
            "非空且请求直连来源在此列表内时，才采信 X-Forwarded-For 头首项作为客户端"
            "IP（open 模式按 IP 限流用）；为空（默认）一律不采信 XFF，以直连 socket "
            "地址为准，防止客户端伪造 XFF 绕过限流。"
        ),
    )

    # ── 数据库 ────────────────────────────────────────────────────
    db_path: str | None = Field(
        default=None,
        description="数据库文件路径。未设置时按 frozen/开发模式自动解析。",
    )

    # ── 任务系统 ──────────────────────────────────────────────────
    task_workers: int = Field(
        default=0,
        description="后台任务线程池大小。0 = 自动 min(4, cpu_count)。",
    )
    task_timeout: float = Field(
        default=600.0,
        description="单任务最大执行时长（秒），超时自动标记失败。",
        gt=0,
    )

    # ── LLM ───────────────────────────────────────────────────────
    llm_cache_ttl: float = Field(
        default=300.0,
        description="LLM 响应缓存 TTL（秒），0=禁用。",
        ge=0,
    )
    llm_cache_backend: str = Field(  # [P2-3 SETTINGS_CACHE_BACKEND]
        default="inprocess",
        description="LLM 缓存 backend: inprocess / redis（redis 不可用时降级到 inprocess）。",
    )
    redis_url: str | None = Field(
        default=None,
        description="Redis 连接 URL，仅在 llm_cache_backend=redis 时生效。",
    )
    provider_timeout: int = Field(
        default=120,
        description="LLM HTTP 请求超时秒数。",
        gt=0,
    )
    reflection_max_tokens: int = Field(
        default=5000,
        description=(
            "Reflection 评审 max_tokens 下限（长 prompt + 16000 字论文预览下，完整"
            "JSON claims+evidence+4 维+评语需 3-5K tokens；低于 3000 会被截断降级）。"
        ),
        gt=0,
    )
    reflection_ii_samples: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "PAPERFORGE_REFLECTION_II_SAMPLES", "REFLECTION_II_SAMPLES",
        ),
        description=(
            "II（创新洞察）维中位数采样次数：>1 时对 II 维额外采样取中位数以压"
            "9B 措辞噪声，代价是每篇多 (n-1) 次 LLM 调用。1=关闭采样（单次调用，"
            "2026-08-22 提速默认）。"
        ),
        ge=1,
    )
    depth_reasoning_nodes: str = Field(
        default="Q5a,Q5b,Q5c",
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_REASONING_NODES", "DEPTH_REASONING_NODES",
        ),
        description=(
            "DEPTH 开启思考模式（reasoning CoT）的节点列表（逗号分隔）。"
            "默认 Q5a,Q5b,Q5c（判断类节点受益于深度思考，抽取类节点不需要）。"
            "设为空字符串可禁用所有节点的思考模式。"
        ),
    )
    depth_reasoning_budget: int = Field(
        default=2048,
        ge=0,
        validation_alias=AliasChoices(
            "PAPERFORGE_REASONING_BUDGET", "DEPTH_REASONING_BUDGET",
        ),
        description=(
            "思考模式 CoT 长度上限（tokens）。"
            "限制 CoT 长度可将单节点耗时从 ~125s 压至 ~45s。"
            "设为 0 不限制（由模型自行决定 CoT 长度）。"
        ),
    )
    depth_grammar_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_GRAMMAR_ENABLED",
            "PAPERFORGE_DEPTH_GRAMMAR",
            "DEPTH_GRAMMAR_ENABLED",
        ),
        description=(
            "是否对 DEPTH 评分节点(Q0-Q4/Q5c)启用 GBNF 语法约束。"
            "仅当后端为本地 llama.cpp / llama-server（OpenAI 兼容）时可开启："
            "强制评分为[0,1]浮点、evidence_id 从证据池枚举中选、type/verdict 枚举，"
            "从根源杜绝越界分数/文本分数/无效证据ID重试。传给真实 OpenAI/Zhipu/"
            "DeepSeek 端点会 400，故默认关闭。"
        ),
    )

    # ── 批量任务 ──────────────────────────────────────────────────
    batch_parallel: int = Field(
        default=0,
        description="DEPTH 批量评估并发数。0 = 自动 min(4, cpu_count)。",
    )

    # ── arXiv ─────────────────────────────────────────────────────
    arxiv_auto_fetch_enabled: bool = Field(
        default=True,
        description="arXiv 自动拉取开关。",
    )
    arxiv_auto_fetch_keywords: str = Field(
        default="",
        description="arXiv 关键词列表（逗号分隔）。为空则不启动定时拉取。",
    )
    arxiv_auto_fetch_interval: int = Field(
        default=24,
        description="arXiv 拉取间隔（小时），最小 1。",
        ge=1,
    )
    arxiv_auto_fetch_max_results: int = Field(
        default=5,
        description="每个关键词每次拉取的最新论文数量。",
        ge=1,
    )

    # ── 算力模式 / 资源检测 ───────────────────────────────────────
    compute_mode: str = Field(
        default="deep",
        description="默认算力模式: speed / deep。",
    )
    compute_presets_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERFORGE_COMPUTE_PRESETS_PATH", "COMPUTE_PRESETS_PATH"),
        description="算力预设 JSON 路径。",
    )
    enable_resource_detect: bool = Field(
        default=False,
        description="动态资源检测开关（Windows 默认禁用，防 C 级崩溃）。",
    )
    disable_resource_detect: bool = Field(
        default=False,
        description="禁用资源检测（优先级高于 enable_resource_detect）。",
    )
    disable_gpu_detect: bool = Field(
        default=False,
        description="禁用 GPU 检测（Windows 安全兜底）。",
    )

    # ── 降级开关 ──────────────────────────────────────────────────
    disable_invalid_gate: bool = Field(
        default=False,
        description="DepthReviewInvalidError 降级开关。设为 1 时回退旧逻辑（不抛异常）。",
    )

    # ── 构建 / 分发 ───────────────────────────────────────────────
    no_auto_rebuild: bool = Field(
        default=False,
        description="跳过前端自动构建（设为 1 禁止启动时自动 npm install + npm run build）。",
    )

    # ── 模型切换 ──────────────────────────────────────────────────
    # 向后兼容：MODEL_SWITCH_ENABLED（无 PAPERFORGE_ 前缀）仍可控制
    model_switch_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("PAPERFORGE_MODEL_SWITCH_ENABLED", "MODEL_SWITCH_ENABLED"),
        description="前端模型切换功能开关。",
    )

    # ── DEPTH v4.1 算法特性开关（config.py → settings.py 收敛）────────
    v4_final_score_convert: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PAPERFORGE_V4_FINAL_SCORE_CONVERT", "V4_FINAL_SCORE_CONVERT"
        ),
        description="v4.1 结果 → DepthScore 口径换算开关。",
    )
    oim_weight: float = Field(
        default=0.0,
        validation_alias=AliasChoices("PAPERFORGE_OIM_WEIGHT", "OIM_WEIGHT"),
        description="客观指标层 OIM 整体权重。",
    )
    oim_feature_weights: dict = Field(
        default={
            "ablation": 0.2,
            "repro_signal": 0.25,
            "citation_density": 0.2,
            "structure": 0.2,
            "formalism": 0.15,
        },
        validation_alias=AliasChoices("PAPERFORGE_OIM_FEATURE_WEIGHTS", "OIM_FEATURE_WEIGHTS"),
        description="OIM 各特征权重（JSON）。",
    )
    oim_min_text_chars: int = Field(
        default=2000,
        validation_alias=AliasChoices("PAPERFORGE_OIM_MIN_TEXT_CHARS", "OIM_MIN_TEXT_CHARS"),
        description="OIM 最低文本字符数。",
    )
    calibration_set_path: str | None = Field(
        default="",
        validation_alias=AliasChoices("PAPERFORGE_CALIBRATION_SET_PATH", "CALIBRATION_SET_PATH"),
        description="专家校准集文件路径。",
    )
    calibration_spearman_threshold: float = Field(
        default=0.6,
        validation_alias=AliasChoices(
            "PAPERFORGE_CALIBRATION_SPEARMAN_THRESHOLD", "CALIBRATION_SPEARMAN_THRESHOLD"
        ),
        description="校准集 Spearman 阈值。",
    )
    consensus_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("PAPERFORGE_CONSENSUS_ENABLED", "CONSENSUS_ENABLED"),
        description="自洽采样开关。",
    )
    consensus_samples: int = Field(
        default=3,
        validation_alias=AliasChoices("PAPERFORGE_CONSENSUS_SAMPLES", "CONSENSUS_SAMPLES"),
        description="自洽采样次数。",
    )
    consensus_temperature: float = Field(
        default=0.0,
        validation_alias=AliasChoices("PAPERFORGE_CONSENSUS_TEMPERATURE", "CONSENSUS_TEMPERATURE"),
        description="自洽采样温度。",
    )
    semantic_override_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PAPERFORGE_SEMANTIC_OVERRIDE_ENABLED", "SEMANTIC_OVERRIDE_ENABLED"
        ),
        description="verdict 语义脱耦开关。",
    )
    verdict_accept_threshold: float = Field(
        default=0.77,
        validation_alias=AliasChoices(
            "PAPERFORGE_VERDICT_ACCEPT_THRESHOLD", "VERDICT_ACCEPT_THRESHOLD"
        ),
        description=(
            "verdict accept 阈值。全量 669 篇校准：85th percentile = 0.765，"
            "accept ≥ 0.77 → ~15% accept 率。"
            "加载期必须 ≥ VERDICT_ACCEPT_FLOOR(0.75)：配低了直接报错，"
            "不再运行时静默 clamp（2026-09-16 由「静默夹紧」改为「失败响亮」）。"
        ),
    )
    verdict_reject_threshold: float = Field(
        default=0.48,  # Ornith: 从 0.5 降到 0.48（金字塔分布）
        validation_alias=AliasChoices(
            "PAPERFORGE_VERDICT_REJECT_THRESHOLD", "VERDICT_REJECT_THRESHOLD"
        ),
        description="verdict reject 阈值。",
    )
    low_conf_threshold: float = Field(
        default=0.15,
        validation_alias=AliasChoices("PAPERFORGE_LOW_CONF_THRESHOLD", "LOW_CONF_THRESHOLD"),
        description="低置信度阈值。",
    )
    allow_cot: bool = Field(
        default=False,
        validation_alias=AliasChoices("PAPERFORGE_ALLOW_COT", "ALLOW_COT"),
        description="弱模型思维链开关。",
    )
    hotspots_source: str = Field(
        default="default",
        validation_alias=AliasChoices("PAPERFORGE_HOTSPOTS_SOURCE", "HOTSPOTS_SOURCE"),
        description="热点词来源：default / paper / db。",
    )

    # ── 引用真值校验（ADR-014 P4）──
    # citation_verifier 此前直接读 os.environ，导致 .env 里的 PAPERFORGE_CITATION_VERIFY
    # 不生效；这里统一收口到 Settings（OS env 优先，.env 兜底）。
    citation_verify: str = Field(
        default="",
        validation_alias=AliasChoices("PAPERFORGE_CITATION_VERIFY", "CITATION_VERIFY"),
        description=(
            "引用真值校验开关（三态，由 resolve_citation_verify_mode 统一解析）："
            "1/true/yes/on=在线 Crossref 核验真伪；offline（或其他非空串）=仅本地抽取 + 引用"
            "一致性 + 占位符/假 arXiv 启发式，不触网；0/false/off=完全不跑。"
            "**空/未配置 = 沿用调用方显式声明的默认档**（感悟报告=offline，深度审稿=skip）——"
            "旧描述写「空=在线」与三个调用方的实际行为都不一致，已于 2026-09-16 修正。"
        ),
    )
    crossref_mailto: str = Field(
        default="",
        validation_alias=AliasChoices("PAPERFORGE_CROSSREF_MAILTO", "CROSSREF_MAILTO"),
        description="Crossref polite-pool 邮箱（可选，提升配额）。",
    )
    citation_verify_cap: int = Field(
        default=30,
        validation_alias=AliasChoices("PAPERFORGE_CITATION_VERIFY_CAP", "CITATION_VERIFY_CAP"),
        description="单篇最多核验的 DOI 数（防止 bulk 卡死）。",
    )
    citation_verify_timeout: float = Field(
        default=4.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_CITATION_VERIFY_TIMEOUT", "CITATION_VERIFY_TIMEOUT"
        ),
        description="Crossref 单请求超时秒。",
    )

    # ── DEPTH v4.2 算法升级开关（图表证据 / QF 节点 / 合并评分 / 自适应校准）────
    # 任一设为 false/0 即回退 v4.1 对应行为。
    # 注意：图表证据并入默认关闭（见 depth_figure_evidence_enabled），9B 模型上已验证
    # figures-OFF acc=0.647 > figures-ON acc=0.353（figure 证据并入 QE 池影响 Q4/Q5c 辩论）。
    depth_figure_evidence_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED", "DEPTH_FIGURE_EVIDENCE_ENABLED"
        ),
        description=(
            "QE 证据池并入 PaperFigure 图表证据（ocr_text/qwen_summary，带来源标记）。"
            "默认 False：PeerRead 校准显示 9B 模型 figures-OFF acc=0.647 优于 figures-ON 0.353，"
            "figure 证据并入 QE 池会污染 Q4/Q5c 辩论节点。"
            "未来升级到更强模型（视觉模态或更大参数）可重启用。"
        ),
    )
    depth_qf_node_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("PAPERFORGE_DEPTH_QF_NODE_ENABLED", "DEPTH_QF_NODE_ENABLED"),
        description="DAG 中新增 QF 图文一致性节点（Wave3 与维度评分并行）；无图表时自动中性跳过。",
    )
    depth_figure_weight: float = Field(
        default=0.0,
        validation_alias=AliasChoices("PAPERFORGE_DEPTH_FIGURE_WEIGHT", "DEPTH_FIGURE_WEIGHT"),
        description=(
            "图文一致性对 final_base 的微调权重。"
            "默认 0：9B 模型 + LLM run-to-run ±0.15 随机性淹没 QF ±0.05 微调，"
            "实测降权(0.02)与激进(0.1)对 acc 无差异，故默认关闭。"
            "兜底+质量门控代码已保留，未来升级模型可重启用（建议 0.05 起步）。"
        ),
    )
    depth_vector_render_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_VECTOR_RENDER_ENABLED", "DEPTH_VECTOR_RENDER_ENABLED"
        ),
        description="M0: PDF 矢量图渲染兜底（cluster_drawings + get_pixmap），false 回退纯位图抽取。",
    )
    depth_axis_tick_filter_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_AXIS_TICK_FILTER_ENABLED",
            "DEPTH_AXIS_TICK_FILTER_ENABLED",
        ),
        description=(
            "P0-11: 图内数值指纹进检测前是否过滤坐标轴刻度/归一化基线（圆整数）。"
            "true 时剔除 100/150/200 等轴刻度与 control=100% 归一化基线，"
            "只对真实测量值跑统计指纹；false 关闭过滤（保留全部转写值）。"
        ),
    )
    depth_merged_scoring_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_MERGED_SCORING_ENABLED", "DEPTH_MERGED_SCORING_ENABLED"
        ),
        description="Q2/Q3/Q4 合并为单次多维度调用（Q234 节点），false 回退三次独立调用。",
    )
    depth_delta_adaptive_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_DELTA_ADAPTIVE_ENABLED", "DEPTH_DELTA_ADAPTIVE_ENABLED"
        ),
        description="Q5c delta 区间按辩论结果自适应放宽（硬上限 [-0.25, +0.25]），false 固定 [-0.08, 0.12]。",
    )
    depth_delta_default_min: float = Field(
        default=-0.08,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_DELTA_DEFAULT_MIN",
            "DEPTH_DELTA_DEFAULT_MIN",
            "DELTA_DEFAULT_MIN",
        ),
        description="Q5c delta 默认区间下限。",
    )
    depth_delta_default_max: float = Field(
        default=0.12,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_DELTA_DEFAULT_MAX",
            "DEPTH_DELTA_DEFAULT_MAX",
            "DELTA_DEFAULT_MAX",
        ),
        description="Q5c delta 默认区间上限。",
    )
    depth_delta_bounds_overrides: dict = Field(
        default_factory=dict,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_DELTA_BOUNDS_OVERRIDES", "DEPTH_DELTA_BOUNDS_OVERRIDES"
        ),
        description=(
            "按论文类型（A/B/C/D）或算力模式（speed/deep）覆盖 delta 默认区间。"
            '格式示例：{"A": {"min": -0.10, "max": 0.15}, "speed": {"min": -0.05, "max": 0.08}}'
        ),
    )
    depth_severity_classifier_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_SEVERITY_CLASSIFIER_ENABLED", "DEPTH_SEVERITY_CLASSIFIER_ENABLED"
        ),
        description="启用训练好的 severity 决策树分类器替代固定 0.5 阈值；false 时强制回退阈值规则。",
    )
    depth_severity_model_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_SEVERITY_MODEL_PATH", "DEPTH_SEVERITY_MODEL_PATH"
        ),
        description="训练好的 severity 决策树模型 JSON 路径；None 时使用默认路径 mock_api/config/severity_model.json。",
    )
    # ---- severity / Q5c 调参（线上 A/B 调优） ----
    depth_severity_fallback_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_SEVERITY_FALLBACK_THRESHOLD", "DEPTH_SEVERITY_FALLBACK_THRESHOLD"
        ),
        description="无 ML 模型时，out-of-range claim 判为 fatal 的相对偏差阈值。",
    )
    depth_severity_fatal_weight: float = Field(
        default=1.0,
        ge=0.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_SEVERITY_FATAL_WEIGHT", "DEPTH_SEVERITY_FATAL_WEIGHT"
        ),
        description="fatal 越界对 Q5c 惩罚因子的权重。",
    )
    depth_severity_minor_weight: float = Field(
        default=0.25,
        ge=0.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_SEVERITY_MINOR_WEIGHT", "DEPTH_SEVERITY_MINOR_WEIGHT"
        ),
        description="minor 越界对 Q5c 惩罚因子的权重。",
    )
    depth_q5c_claim_severity_factor: float = Field(
        default=0.05,
        ge=0.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_Q5C_CLAIM_SEVERITY_FACTOR", "DEPTH_Q5C_CLAIM_SEVERITY_FACTOR"
        ),
        description="每条 severity 惩罚单位对 Q5c delta 下限的收紧系数。",
    )
    depth_claim_validation_penalty_per_claim: float = Field(
        default=0.1,
        ge=0.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_CLAIM_VALIDATION_PENALTY_PER_CLAIM",
            "DEPTH_CLAIM_VALIDATION_PENALTY_PER_CLAIM",
        ),
        description="单个 out-of-range claim 对 figure_consistency_score 的惩罚值。",
    )
    depth_claim_validation_penalty_max: float = Field(
        default=0.3,
        ge=0.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_CLAIM_VALIDATION_PENALTY_MAX",
            "DEPTH_CLAIM_VALIDATION_PENALTY_MAX",
        ),
        description="figure_consistency_score 惩罚上限。",
    )
    # ---- claim-validation 对称 bonus（生产数据验证后开启）----
    depth_claim_validation_bonus_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_CLAIM_VALIDATION_BONUS_ENABLED",
            "DEPTH_CLAIM_VALIDATION_BONUS_ENABLED",
        ),
        description="是否启用 claim-validation 对称 bonus。默认关闭，待生产数据验证后开启。",
    )
    depth_claim_validation_bonus_max: float = Field(
        default=0.05,
        ge=0.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_CLAIM_VALIDATION_BONUS_MAX",
            "DEPTH_CLAIM_VALIDATION_BONUS_MAX",
        ),
        description="claim-validation bonus 上限，避免压倒 LLM 语义判断。",
    )
    depth_claim_validation_bonus_per_claim: float = Field(
        default=0.02,
        ge=0.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_CLAIM_VALIDATION_BONUS_PER_CLAIM",
            "DEPTH_CLAIM_VALIDATION_BONUS_PER_CLAIM",
        ),
        description="每条一致性 claim 的 bonus 基准值。",
    )
    depth_claim_validation_bonus_min_valid: int = Field(
        default=3,
        ge=1,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_CLAIM_VALIDATION_BONUS_MIN_VALID",
            "DEPTH_CLAIM_VALIDATION_BONUS_MIN_VALID",
        ),
        description="触发 bonus 所需的最少一致 claim 数量。",
    )

    # ── ADR-014 P9：全文覆盖层（分块摘要 + 采样增强）—— 漏洞 C ────────────
    # 本地 9B 模型 ctx≈8K token，segment_paper_text 只给摘要/引言/结论 + 正文开头
    # （max_chars_full），论文中段几乎不可见 → QE 证据池残缺。开启后对全文做
    # 分块摘要（map-reduce）生成全局摘要，并采样中段原文注入 QE/Q234 prompt。
    # 默认自动开启（论文超过阈值时生效）；设 PAPERFORGE_DEPTH_FULLTEXT_ENABLED=0
    # 可强制关闭。短文（≤ 1.2×max_chars_full）自动跳过以省 LLM 成本。
    # 全程 fail-open，失败自动降级。详见 mock_api/depth_fulltext.py。
    depth_fulltext_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_FULLTEXT_ENABLED", "PAPERFORGE_DEPTH_FULLTEXT"
        ),
        description=(
            "DEPTH 全文覆盖层开关（默认 True，自动开启）。"
            "长论文评审前对全文做分块摘要 + 中段原文采样，"
            "让本地模型在 8K ctx 内「看到」论文全文梗概与真实段落，"
            "缓解漏洞 C（ctx 只读头尾）。设 0 可强制关闭。全程 fail-open。"
        ),
    )
    depth_fulltext_chunk_size: int = Field(
        default=1500,
        ge=300,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_FULLTEXT_CHUNK_SIZE", "DEPTH_FULLTEXT_CHUNK_SIZE"
        ),
        description="分块摘要的块大小（字符）。",
    )
    depth_fulltext_chunk_overlap: int = Field(
        default=120,
        ge=0,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_FULLTEXT_CHUNK_OVERLAP", "DEPTH_FULLTEXT_CHUNK_OVERLAP"
        ),
        description="分块重叠（字符）。",
    )
    depth_fulltext_top_k: int = Field(
        default=8,
        ge=1,
        le=32,
        validation_alias=AliasChoices("PAPERFORGE_DEPTH_FULLTEXT_TOP_K", "DEPTH_FULLTEXT_TOP_K"),
        description="注入 prompt 的采样块数量。",
    )

    # ── ADR-014 P8：双模型交叉复核（second opinion）──────────────────────
    # 本地单模型意见不可当终审（qwen_vs_others 对比：与 ChatGPT 真实分歧 ~0.10、
    # 与混元排序不相关）。开启后每条评审会额外请第二个（云端/不同端点）模型
    # 独立打分，分歧大时在结果里标记 needs_human_review 建议。全程 fail-open。
    second_opinion_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PAPERFORGE_SECOND_OPINION_ENABLED", "PAPERFORGE_SECOND_OPINION"
        ),
        description=(
            "双模型交叉复核开关（默认 True）。开启后本地模型评审完，会从 "
            "llm_configs 里挑一个与主 provider 不同的云端/独立端点模型做第二次评审；"
            "若 llm_configs 无云端配置，则回退到 glm_vision_* 云端视觉后端（同一把 GLM key "
            "即可，文本复核也能用），实现「本地主 + 云端终审」的混合评审。\n"
            "分歧超过 second_opinion_threshold 时，云端（更强模型）结果将【覆盖】本地结果"
            "（由 second_opinion_override 控制），并将 original/corrected 一并存档供审计。\n"
            "全程 fail-open：无第二模型 / 调用失败 / 处于测试环境时静默跳过，绝不改变主评审。"
        ),
    )
    second_opinion_override: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PAPERFORGE_SECOND_OPINION_OVERRIDE", "SECOND_OPINION_OVERRIDE"
        ),
        description=(
            "云端纠正本地开关（默认 False，P0 安全默认）。当双模型分歧超过阈值时，若开启则以"
            "云端（更强）模型的 score/verdict 覆盖本地结果；否则云端仅作影子分记录"
            "（original/corrected 仍存档审计，resolved_score 保持本地）。"
            "经验证（2026-08-17，16 篇盲评金标）：云端 GLM 覆盖会把相关性从 r=0.697 压到 0.138、"
            "κ 由正转负（方差坍缩 + 量表未校准），故默认关闭以保护已校准的本地分。"
            "未来若云端完成金标校准并通过验证，可显式设 True 重新开启。"
        ),
    )
    second_opinion_threshold: float = Field(
        default=0.15,
        ge=0.01,
        validation_alias=AliasChoices(
            "PAPERFORGE_SECOND_OPINION_THRESHOLD", "SECOND_OPINION_THRESHOLD"
        ),
        description="双模型分数分歧阈值：|Δscore| ≥ 此值或 verdict 不一致 → 触发云端覆盖。",
    )
    second_opinion_model: str = Field(
        default="glm-4-flash",
        validation_alias=AliasChoices("PAPERFORGE_SECOND_OPINION_MODEL", "SECOND_OPINION_MODEL"),
        description=(
            "云端第二评审（文本）使用的模型。默认 glm-4-flash（通用文本模型）。\n"
            "⚠️ 不要用 glm_vision_model（glm-4v-flash 是视觉模型，纯文本长提示下"
            "常不按 score:/verdict: 格式输出，导致解析失败、第二评审静默跳过）；\n"
            "⚠️ 也不要用 glm-4.7-flash（部分 key/套餐对该模型返回空内容）。\n"
            "find_second_provider 的 glm_vision_* 回退路径使用此模型做文本复核。"
        ),
    )

    # ── 被引情感云端复核（分析功能，逐条引用分类）─────────────────────
    # 复用 second_opinion 的云端 provider 路由（glm_vision 回退 + pytest 守卫 + fail-open）。
    # 关键成本控制：每条引用都调云端太贵（一篇论文 50+ 引用），故仅当【本地分类置信度低】
    # 时才触发云端复核；分歧则以云端标签覆盖本地，original/corrected 写入 cloud_recheck 审计列。
    sentiment_recheck_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PAPERFORGE_SENTIMENT_RECHECK_ENABLED", "SENTIMENT_RECHECK_ENABLED"
        ),
        description=(
            "被引情感云端复核开关（默认 True）。开启且 second_opinion_enabled 也为 True 时，"
            "本地 LLM 对被引情感（support/criticize/background）的低置信分类会请云端（更强）模型复核，"
            "分歧则以云端标签覆盖本地，并把 original/corrected 写入 citation_sentiments.cloud_recheck。"
            "全程 fail-open。成本控制：仅本地置信度 < sentiment_recheck_low_conf 的引用才触发云端调用。"
        ),
    )
    sentiment_recheck_low_conf: float = Field(
        default=0.6,
        ge=0.0,
        le=1.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_SENTIMENT_RECHECK_LOW_CONF", "SENTIMENT_RECHECK_LOW_CONF"
        ),
        description="仅当本地被引情感分类置信度低于此值（默认 0.6）才触发云端复核，控制 API 成本。",
    )

    # ── 绑定地址 ──────────────────────────────────────────────────
    bind_host: str = Field(
        default="127.0.0.1",
        description="服务绑定地址（默认仅本机；设 0.0.0.0 可对外暴露）。",
    )
    bind_port: int = Field(
        default=8770,
        description="后端服务监听端口（launcher 会在此基础上自动探测空闲端口）。",
        gt=0,
        le=65535,
    )

    # ── 多 API Key 鉴权（Layer 1）+ 限流/审计（Layer 2）──────────────
    api_key_rate_limit_default: int = Field(
        default=60,
        description="每个 API Key 默认速率上限（请求/分钟）。0=不限流。",
        ge=0,
    )
    api_key_audit_enabled: bool = Field(
        default=True,
        description="是否记录 API 调用审计日志到 api_call_logs 表。",
    )

    # ── Web 前端 ──────────────────────────────────────────────────
    web_dist: str | None = Field(
        default=None,
        description="前端静态资源目录路径（frozen 模式或自定义构建产物位置）。",
    )

    # ── 外部视觉模型（Qwen3-VL HTTP）───────────────────────────────
    vision_http_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERFORGE_VISION_HTTP_URL", "VISION_HTTP_URL"),
        description=(
            "OpenAI 兼容的视觉模型 HTTP 端点（如 llama-server 服务的 Qwen3-VL-4B）。"
            "设置后 figure_qwen._ask_vision_on_figure 走 HTTP 调用此端点（多模态消息），"
            "不再依赖进程内 llama_cpp（后者常因未安装导致 qwen_summary 96.7% 缺失）。"
            "推荐端口 8082（8080=文本 Qwen）；云端 OpenAI 兼容端点填主机部分"
            "（不带 /v1，调用方会自行拼接，如 https://token-plan-cn.xiaomimimo.com）。"
        ),
    )
    vision_model: str = Field(
        default="qwen3-vl",
        validation_alias=AliasChoices("PAPERFORGE_VISION_MODEL", "VISION_MODEL"),
        description=(
            "视觉端点使用的模型 ID。默认 qwen3-vl（兼容本地 llama-server 的"
            "Qwen3-VL-4B）；云端 OpenAI 兼容端点可按需设为 mimo-v2.5 / gpt-4o 等。"
        ),
    )
    vision_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("PAPERFORGE_VISION_API_KEY", "VISION_API_KEY"),
        description=("视觉端点鉴权 API Key。本地 llama-server 无需；云端 OpenAI 兼容端点必填。"),
    )

    # ── 独立云端视觉后端（智谱 GLM 免费视觉，2026-08-16 新增）─────────────
    # 与上面的本地 Qwen3-VL（vision_http_url）完全独立：本地配置原样保留，
    # 此组用于云端免费视觉（glm-4v-flash / glm-4.1v-thinking-flash）。
    # 设计原则：云端推理不占本地显存，因此不走 vram_guard("vision")、不参与
    # text/vision 同卡互斥。默认关闭，需显式开启才接管审计的语义兜底。
    glm_vision_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("PAPERFORGE_GLM_VISION_ENABLED", "GLM_VISION_ENABLED"),
        description=(
            "是否用云端视觉接管图表语义审计（兜底路径）。False（默认）时审计仍走本地 "
            "Qwen3-VL（vision_http_url）或 OpenCV 确定性检测。True 时走 glm_vision_* 配置"
            "（云端，不占本地显存）。具体上游由 glm_vision_provider 决定。"
        ),
    )
    glm_vision_provider: str = Field(
        default="glm",
        validation_alias=AliasChoices("PAPERFORGE_GLM_VISION_PROVIDER", "GLM_VISION_PROVIDER"),
        description=(
            "云端视觉上游提供商。'glm'（默认）= 智谱开放平台（glm-4v-flash 等，"
            "/v4 路径）；'agnes' = Agnes AI 官方 OpenAI 兼容视觉理解"
            "（apihub.agnes-ai.com/v1，模型 agnes-2.5-flash，支持图片输入理解）。"
            "两者共用 glm_vision_api_key / glm_vision_model / glm_vision_base_url，"
            "provider=agnes 未显式设 base_url 时回落到 https://apihub.agnes-ai.com/v1，"
            "未显式设 model 时回落到 agnes-2.5-flash。Agnes Free 计划限速 20 RPM，"
            "故 agnes 路径内部按 ~3s/次节流。"
        ),
    )
    glm_vision_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("PAPERFORGE_GLM_VISION_API_KEY", "GLM_VISION_API_KEY"),
        description="智谱开放平台 API Key（云端视觉必填）。",
    )
    glm_vision_model: str = Field(
        default="glm-4v-flash",
        validation_alias=AliasChoices("PAPERFORGE_GLM_VISION_MODEL", "GLM_VISION_MODEL"),
        description=(
            "云端视觉模型 ID。免费档：glm-4v-flash（快、稳）/ glm-4.1v-thinking-flash（带思考链，"
            "稍慢但数字读数更准）。glm-4.6v-flash 当前平台过载频繁 429，不推荐。"
        ),
    )
    glm_vision_base_url: str = Field(
        default="https://open.bigmodel.cn/api/paas/v4",
        validation_alias=AliasChoices("PAPERFORGE_GLM_VISION_BASE_URL", "GLM_VISION_BASE_URL"),
        description=(
            "智谱云端视觉 OpenAI 兼容端点基址（不含 /chat/completions；调用方自行拼接）。"
            "注意路径是 /v4 而非本地 llama-server 的 /v1。"
        ),
    )
    glm_vision_max_concur: int = Field(
        default=4,
        validation_alias=AliasChoices("PAPERFORGE_GLM_VISION_MAX_CONCUR", "GLM_VISION_MAX_CONCUR"),
        description=(
            "云端视觉并发上限（信号量）。实测 glm-4v-flash 并发≤8 无 429；"
            "glm-4.1v-thinking-flash 并发=8 偶发 429，故默认 4 留余量。"
        ),
        gt=0,
    )
    # 把桌面上 start-llama-dflash-wsl.bat 的启动命令固化进配置，由 PaperForge
    # 托管 llama-server 进程：默认启动即拉起；vision HTTP 使用时仅做 text/vision 令牌仲裁。
    # 兼容手动开：若 8080 已被外部 llama-server 占用，则复用不重复拉起。
    llama_server_exe: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_EXE", "LLAMA_SERVER_EXE"),
        description="llama-server.exe 路径（Qwen/DEPTH 后端）。",
    )
    llama_server_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_MODEL", "LLAMA_SERVER_MODEL"),
        description="主模型 GGUF 路径。",
    )
    llama_server_draft: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_DRAFT", "LLAMA_SERVER_DRAFT"),
        description="草稿模型 GGUF 路径（speculative decoding，可选）。",
    )
    llama_server_port: int = Field(
        default=8080,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_PORT", "LLAMA_SERVER_PORT"),
        description="llama-server 监听端口。",
        gt=0,
    )
    llama_server_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_HOST", "LLAMA_SERVER_HOST"),
        description="llama-server 绑定地址（默认仅本机 127.0.0.1；设 0.0.0.0 可对外暴露，但无认证需自管防火墙）。",
    )
    llama_server_ngl: int = Field(
        default=35,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_NGL", "LLAMA_SERVER_NGL"),
        description="主模型卸载 GPU 层数。",
    )
    llama_server_draft_ngl: int = Field(
        default=35,
        validation_alias=AliasChoices(
            "PAPERFORGE_LLAMA_SERVER_DRAFT_NGL", "LLAMA_SERVER_DRAFT_NGL"
        ),
        description="草稿模型卸载 GPU 层数。",
    )
    llama_server_ctx: int = Field(
        default=16384,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_CTX", "LLAMA_SERVER_CTX"),
        description=(
            "上下文窗口大小。感悟评审（论文预览 ≤16000 字 + 报告全文 + 模板）最坏"
            "≈10.8K tokens，加 5000 token 输出共 ~15.8K，故默认 16384 保底（低于此值会"
            "静默截断 prompt → JSON 解析失败降级 0.3）。8GB 卡 + q4_0 KV 可到 24576"
            "（KV 约 0.9GB、总显存 ~6.5GB），见 ADR-014。"
        ),
        gt=0,
    )
    llama_server_parallel: int = Field(
        default=1,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_PARALLEL", "LLAMA_SERVER_PARALLEL"),
        description="llama-server 并发槽数（--parallel）。单卡 8GB 上限 2。默认 1 向后兼容。",
        ge=1,
    )
    llama_server_flash_attn: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PAPERFORGE_LLAMA_SERVER_FLASH_ATTN", "LLAMA_SERVER_FLASH_ATTN"
        ),
        description=(
            "Flash Attention（--flash-attn on）。Qwen3.5 是 GDN 混合架构，fa=off 会直接"
            "创建上下文失败（2026-08-11 bench 实测），必须保持 on；auto 在 CUDA 下等效 on，"
            "显式 on 更稳。bench 矩阵：fa on/off 与 batch 组合 decode 均 ≈56 tok/s，差异 <1%。"
        ),
    )
    llama_server_n_cpu_moe: int = Field(
        default=0,
        validation_alias=AliasChoices(
            "PAPERFORGE_LLAMA_SERVER_N_CPU_MOE", "LLAMA_SERVER_N_CPU_MOE"
        ),
        description=(
            "MoE 专家层卸载到 CPU 的数量（--n-cpu-moe）。仅当模型 > 显存时生效。"
            "GLM-4.7-Flash IQ2_XXS（9.79 GB）在 8 GB RTX 5060 上推荐 24。"
            "取值越大，CPU 卸载越多（decode 越慢但 GPU 显存越安全）。"
        ),
        ge=0,
    )
    # ── Ornith-1.5-9B / 通用采样档（官方推荐，env 驱动以便 Ornstein 旧基线可复现）──
    llama_server_reasoning: str = Field(
        default="off",
        validation_alias=AliasChoices(
            "PAPERFORGE_LLAMA_SERVER_REASONING", "LLAMA_SERVER_REASONING"
        ),
        description=(
            "llama-server 推理解析（--reasoning on/off）。Ornith-1.5-9B 是推理模型，"
            "开 on 让其先 <think> 后答；CoT 进 reasoning_content，最终答案仍在 content。"
        ),
    )
    llama_server_temp: float = Field(
        default=0.1,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_TEMP", "LLAMA_SERVER_TEMP"),
        description="采样温度（--temp）。Ornith 精确编码档 0.6；旧 Ornstein 默认 0.1。",
    )
    llama_server_top_p: float = Field(
        default=1.0,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_TOP_P", "LLAMA_SERVER_TOP_P"),
        description="核采样概率（--top-p）。Ornith 0.95；链内需含 top_p 才生效。",
    )
    llama_server_top_k: int = Field(
        default=40,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_TOP_K", "LLAMA_SERVER_TOP_K"),
        description="top-k 截断（--top-k）。Ornith 20；llama.cpp 默认 40。",
    )
    llama_server_min_p: float = Field(
        default=0.01,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_MIN_P", "LLAMA_SERVER_MIN_P"),
        description="最小概率阈值（--min-p）。Ornith 0.0；旧默认 0.01。",
    )
    llama_server_presence_penalty: float = Field(
        default=0.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_LLAMA_SERVER_PRESENCE_PENALTY", "LLAMA_SERVER_PRESENCE_PENALTY"
        ),
        description="存在惩罚（--presence-penalty）。Ornith 精确档 0.0 / 通用档 1.5。",
    )
    depth_temperature: float | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERFORGE_DEPTH_TEMPERATURE", "DEPTH_TEMPERATURE"),
        description=(
            "DEPTH 评分按请求下发的温度覆盖值。默认 None（沿用 compute_mode 历史行为，如 speed 档 0.2）。"
            "设为 0.0 即强制贪心（之前因 `0.0 or preset` 被静默回退到 preset，已修 2026-08-21）；"
            "Ornith 测评为 0.6，否则会被按请求覆盖盖掉 server 的 --temp。"
        ),
    )
    llama_server_draft_n_max: int = Field(
        default=15,
        validation_alias=AliasChoices(
            "PAPERFORGE_LLAMA_SERVER_DRAFT_N_MAX", "LLAMA_SERVER_DRAFT_N_MAX"
        ),
        description="speculative decoding 每步草稿 token 数（--spec-draft-n-max）。",
        ge=1,
    )
    llama_server_cold_grace: int = Field(
        default=360,
        validation_alias=AliasChoices(
            "PAPERFORGE_LLAMA_SERVER_COLD_GRACE", "LLAMA_SERVER_COLD_GRACE"
        ),
        description="冷启动宽限秒数（CUDA kernel 编译），超时仍未就绪才判失败。",
        gt=0,
    )
    qwen_autostart: bool = Field(
        default=True,
        validation_alias=AliasChoices("PAPERFORGE_QWEN_AUTOSTART", "QWEN_AUTOSTART"),
        description="PaperForge 启动即拉起 llama-server（默认 True）。",
    )
    vram_exclusive: bool = Field(
        default=True,
        validation_alias=AliasChoices("PAPERFORGE_VRAM_EXCLUSIVE", "VRAM_EXCLUSIVE"),
        description=(
            "text-Qwen(8080) 与 vision-Qwen(vision_http_url) 显存互斥仲裁（ADR-013）："
            "用时切换、同卡时 vision 让出 8080（默认 True，8GB 卡必开）。"
            "设为 False 为无保护模式：任一 kind 直接放行，不取互斥令牌、不做 8080/视觉切换协调。"
        ),
    )

    # ── 评测开关统一收口（2026-09-16 修复配置通道分裂）─────────────
    # 背景：这些开关过去只被 os.environ 读取，而 .env 只由 pydantic Settings 解析、
    # 从不注入进程环境（全仓无 load_dotenv）。结果 .env 里写 `PAPERFORGE_EVAL_SEED=42`
    # 之类**静默不生效**，违反 P4（配置显式、失败响亮）。现统一收口到 Settings，
    # 再由 `env_first_*` 让显式 os.environ 覆盖（测试/脚本热切换仍需 env 优先）。
    eval_seed: int | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERFORGE_EVAL_SEED", "EVAL_SEED"),
        description="评测固定随机种子；None=不注入（沿用模型默认随机行为）。",
    )
    uncertainty_gate: bool = Field(
        default=False,
        validation_alias=AliasChoices("PAPERFORGE_UNCERTAINTY_GATE", "UNCERTAINTY_GATE"),
        description="分数不确定门控（bootstrap 95% CI）总开关，默认关（零开销）。",
    )
    uncertainty_width: float = Field(
        default=0.15,
        validation_alias=AliasChoices("PAPERFORGE_UNCERTAINTY_WIDTH", "UNCERTAINTY_WIDTH"),
        description="不确定门控 CI 宽度阈值（超过则建议人工复核）。",
        gt=0.0,
    )
    reflection_thinking: bool = Field(
        default=False,
        validation_alias=AliasChoices("PAPERFORGE_REFLECTION_THINKING", "REFLECTION_THINKING"),
        description="感悟报告评审 per-request 开启 CoT（默认关，行为与历史一致）。",
    )
    reflection_skip_crossval: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PAPERFORGE_REFLECTION_SKIP_CROSSVAL", "REFLECTION_SKIP_CROSSVAL"
        ),
        description="跳过 arXiv 出处在线核验（离线环境置 true 省 30s 超时）。",
    )
    reflection_evidence_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PAPERFORGE_REFLECTION_EVIDENCE", "REFLECTION_EVIDENCE"
        ),
        description="报告条件化证据检索（注入【原论文参考内容】）总开关，默认关。",
    )
    reflection_evidence_chars: int = Field(
        default=2400,
        validation_alias=AliasChoices(
            "PAPERFORGE_REFLECTION_EVIDENCE_CHARS", "REFLECTION_EVIDENCE_CHARS"
        ),
        description="证据包字符预算。",
        gt=0,
    )
    reflection_evidence_max_blocks: int = Field(
        default=12,
        validation_alias=AliasChoices(
            "PAPERFORGE_REFLECTION_EVIDENCE_MAX_BLOCKS",
            "REFLECTION_EVIDENCE_MAX_BLOCKS",
        ),
        description="证据包块数上限。",
        gt=0,
    )
    reflection_penalize_paper_flags: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PAPERFORGE_REFLECTION_PENALIZE_PAPER_FLAGS",
            "REFLECTION_PENALIZE_PAPER_FLAGS",
        ),
        description=(
            "是否让「原论文自身」的统计/图表红旗扣学生感悟报告的分。"
            "默认 false：论文的问题只作 advisory 展示，不改变学生分数（学生不该"
            "为所读论文的数据负责）；true 恢复旧行为。"
        ),
    )
    reflection_crossval_bonus_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PAPERFORGE_REFLECTION_CROSSVAL_BONUS", "REFLECTION_CROSSVAL_BONUS"
        ),
        description=(
            "是否把「报告写出正确的 arXiv 出处」折算成 understanding_accuracy 加分。"
            "默认 false：出处核验只作 advisory（元数据抄写正确 ≠ 理解准确），"
            "不再影响分数；true 恢复旧的 +≤0.05 加分。"
        ),
    )
    qwen_calibration: bool = Field(
        default=False,
        validation_alias=AliasChoices("PAPERFORGE_QWEN_CALIBRATION", "QWEN_CALIBRATION"),
        description="感悟报告千问校准层（R5/R6/R7 结构下限），默认关。",
    )
    local_ml_embedder: bool = Field(
        default=True,
        validation_alias=AliasChoices("PAPERFORGE_LOCAL_ML", "LOCAL_ML"),
        description="优先加载本地多语言 ONNX 嵌入模型（关闭则走 fastembed 在线）。",
    )
    bench_no_llm: bool = Field(
        default=False,
        validation_alias=AliasChoices("PAPERFORGE_BENCH_NO_LLM", "BENCH_NO_LLM"),
        description="批量基准模式：跳过 LLM，用结构启发式（仅供离线跑分）。",
    )
    llm_watchdog_timeout: float = Field(
        default=120.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "LLM_WATCHDOG_TIMEOUT"
        ),
        description="单次 LLM 调用的看门狗硬超时（秒）。",
        gt=0.0,
    )
    llm_watchdog_thinking_extra: float = Field(
        default=60.0,
        validation_alias=AliasChoices(
            "PAPERFORGE_LLM_WATCHDOG_THINKING_EXTRA", "LLM_WATCHDOG_THINKING_EXTRA"
        ),
        description="思考模式在看门狗超时上的额外放宽（秒）。",
        gt=0.0,
    )

    # ── 审稿算法可调项（P4：可调项必须是校验过的配置字段，不是硬编码常量）──
    depth_node_view_chars: int = Field(
        default=4000,
        validation_alias=AliasChoices("PAPERFORGE_DEPTH_NODE_VIEW_CHARS", "DEPTH_NODE_VIEW_CHARS"),
        description="评分/辩论节点可见的论文正文头部字符数。",
        gt=0,
    )
    depth_node_view_tail_chars: int = Field(
        default=2000,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_NODE_VIEW_TAIL_CHARS", "DEPTH_NODE_VIEW_TAIL_CHARS"
        ),
        description=(
            "评分/辩论节点额外可见的论文尾部字符数。"
            "此前评分节点只看正文前 4000 字（= 摘要+引言），看不到结论与实验；"
            "现在按「头 + 尾」双端取样，让结论至少进入视野。0 = 关闭尾部（回旧行为）。"
        ),
        ge=0,
    )
    depth_fatal_veto_min: int = Field(
        default=3,
        validation_alias=AliasChoices("PAPERFORGE_DEPTH_FATAL_VETO_MIN", "DEPTH_FATAL_VETO_MIN"),
        description=(
            "触发一票否决所需的最少致命缺陷条数（≥1）。"
            "2026-09-16 从 2 提到 3：Q5a 提示词的 few-shot 示例曾把「消融缺失」这类"
            "常见局限锚定为 fatal，导致 36.4% 论文触发否决（详见 "
            "deliverables/diag/评分偏低根因诊断_20260916.md）。示例已同步修正，"
            "门槛再提高一档作为冗余保护。"
        ),
        ge=1,
    )
    depth_fatal_veto_downgrade_floor: float = Field(
        default=0.75,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_FATAL_VETO_DOWNGRADE_FLOOR",
            "DEPTH_FATAL_VETO_DOWNGRADE_FLOOR",
        ),
        description=(
            "一票否决的降级门槛：校准分 ≥ 该值时只降级为 major_revision，不直接 reject。"
            "默认 0.75 对齐 accept 档下界（原实现硬编码 0.8，形成 0.79→reject / "
            "0.81→major_revision 的任意断崖）。"
        ),
        ge=0.0,
        le=1.0,
    )
    depth_score_offset: float | None = Field(
        default=None,
        validation_alias=AliasChoices("PAPERFORGE_DEPTH_SCORE_OFFSET", "DEPTH_SCORE_OFFSET"),
        description=(
            "DEPTH 最终分全局偏移（显式配置，最高优先级）。"
            "None=未配置，允许 (source, year) 分档偏移表接管；"
            "0.0=显式关闭偏移（分档表也不得覆盖）。"
            "此前该开关只被 os.getenv 读取，而 .env 不注入进程环境（全仓无 load_dotenv），"
            "且 correct_final_score 会被硬编码分档表覆盖 → .env 里写 0 静默失效（P4 违规）。"
        ),
    )
    depth_stat_redline_min: int = Field(
        default=2,
        validation_alias=AliasChoices("PAPERFORGE_DEPTH_STAT_REDLINE_MIN", "DEPTH_STAT_REDLINE_MIN"),
        description="统计造假硬红线所需最少确定性指纹条数（≥1）。",
        ge=1,
    )
    depth_stat_benford_min_samples: int = Field(
        default=50,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_STAT_BENFORD_MIN_SAMPLES", "DEPTH_STAT_BENFORD_MIN_SAMPLES"
        ),
        description=(
            "Benford 首位分布检验的最小样本量（≥20）。"
            "旧实现固定 20 且首位取 int(str(v)[0])（0.85 → 数字 0），"
            "导致 ≥31 个 0.x 比率必然误报；提高到 50 并修正首位提取后误报率大幅下降。"
        ),
        ge=20,
    )
    depth_evidence_verbatim_gate: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_EVIDENCE_VERBATIM_GATE", "DEPTH_EVIDENCE_VERBATIM_GATE"
        ),
        description=(
            "QE 证据池是否要求「能在论文原文中定位」（字符 n-gram 包含率闸门）。"
            "开启后 LLM 自写/改写的证据会被标记为未定位并不再作为评分依据；"
            "图表证据（OCR/图注）走独立来源豁免。关闭＝回到旧行为（仅校验 ID 存在）。"
        ),
    )
    depth_evidence_verbatim_min_keep: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "PAPERFORGE_DEPTH_EVIDENCE_VERBATIM_MIN_KEEP", "DEPTH_EVIDENCE_VERBATIM_MIN_KEEP"
        ),
        description=(
            "原文定位闸门开启时，至少保留多少条证据；"
            "定位成功数低于此值时保留全部（标注未定位）并记日志，避免把证据池清空导致评审中止。"
        ),
        ge=1,
    )

    # ── 校验器 ────────────────────────────────────────────────────
    @field_validator("compute_mode")
    @classmethod
    def _validate_compute_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("fast", "speed", "deep"):  # [P2-3 SETTINGS_FAST_VALIDATOR]
            allowed = "'fast' / 'speed' / 'deep'"
            raise ValueError(f"compute_mode 必须为 {allowed}，实际 '{v}'")
        return v

    @field_validator("oim_feature_weights", mode="before")
    @classmethod
    def _validate_oim_feature_weights(cls, v):
        """支持 JSON 字符串或 dict；空字符串回退默认值。"""
        if v is None or v == "":
            return {
                "ablation": 0.2,
                "repro_signal": 0.25,
                "citation_density": 0.2,
                "structure": 0.2,
                "formalism": 0.15,
            }
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {
                    "ablation": 0.2,
                    "repro_signal": 0.25,
                    "citation_density": 0.2,
                    "structure": 0.2,
                    "formalism": 0.15,
                }
        return v

    @field_validator("depth_delta_bounds_overrides", mode="before")
    @classmethod
    def _validate_delta_bounds_overrides(cls, v):
        """支持 JSON 字符串或 dict；空字符串/None 回退空 dict。"""
        if v is None or v == "":
            return {}
        if isinstance(v, dict):
            return v
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return {}
        return {}

    @field_validator("eval_seed", mode="before")
    @classmethod
    def _validate_eval_seed(cls, v):
        """空字符串 → None（.env 里 `PAPERFORGE_EVAL_SEED=` 不应导致加载崩溃）。"""
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if isinstance(v, str):
            try:
                return int(float(v))
            except ValueError as e:
                raise ValueError(f"eval_seed 必须为整数，实际 {v!r}") from e
        return v

    @field_validator("verdict_accept_threshold")
    @classmethod
    def _validate_accept_threshold(cls, v: float) -> float:
        """加载期校验 accept 阈值下界（P4：配置错误必须响亮，不得运行时静默夹紧）。

        算法不变量：accept 档下界 VERDICT_ACCEPT_FLOOR=0.75（见 depth_calibration.py）。
        此前 `_apply_hard_verdict` 把低于下界的阈值静默 max() 回去，导致文档里那些
        「accept≥0.6 最优」的标定结论在代码里永远无法复现。现在配错即报错。
        """
        if v < _VERDICT_ACCEPT_FLOOR:
            raise ValueError(
                f"verdict_accept_threshold={v} 低于 accept 档下界 "
                f"{_VERDICT_ACCEPT_FLOOR}（算法不变量，见 depth_calibration.VERDICT_ACCEPT_FLOOR）；"
                "否则 minor_revision 档会被架空。请改用 ≥ 下界的值。"
            )
        return v

    # ── 派生属性 ──────────────────────────────────────────────────
    @property
    def resource_detect_enabled(self) -> bool:
        """资源检测是否生效（enable=True 且 disable=False）。"""
        return self.enable_resource_detect and not self.disable_resource_detect

    @property
    def arxiv_keywords_list(self) -> list[str]:
        """arXiv 关键词列表（逗号分隔 → list）。"""
        return [kw.strip() for kw in self.arxiv_auto_fetch_keywords.split(",") if kw.strip()]

    @property
    def effective_task_workers(self) -> int:
        """实际线程池大小（0 → 自动 min(4, cpu_count)）。"""
        if self.task_workers > 0:
            return self.task_workers
        return min(4, max(1, os.cpu_count() or 4))

    @property
    def effective_batch_parallel(self) -> int:
        """实际批量并发数（0 → 自动 min(4, cpu_count)）。"""
        if self.batch_parallel > 0:
            return self.batch_parallel
        return min(4, max(1, os.cpu_count() or 4))


# ---------------------------------------------------------------------------
# 单例获取（functools.cache 保证进程级唯一实例）
# ---------------------------------------------------------------------------
@functools.cache
def get_settings() -> Settings:
    """获取进程级 Settings 单例。

    首次调用时从环境变量 / .env 文件加载并校验配置。
    后续调用返回缓存实例（ functools.cache 保证）。
    若需重新加载（测试场景），调用 reset_settings()。
    """
    return Settings()


def reset_settings() -> None:
    """清除缓存的 Settings 实例（测试用，强制下次 get_settings() 重新加载）。"""
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# env-first 解析助手（修复「.env 只被 Settings 读取，os.environ 看不到」的配置分裂）
# ---------------------------------------------------------------------------
# 语义：**显式设置的环境变量优先**（测试 monkeypatch / 运维临时覆盖 / 脚本热切换），
# 未设置时回落到 Settings（它同时读真实 env 与 .env）。这样 .env 里的开关终于生效，
# 同时保留运行期 env 覆盖能力。
# ---------------------------------------------------------------------------

# 算法不变量（与 depth_calibration.py 的同名常量必须一致，双方在加载期交叉校验）
_VERDICT_ACCEPT_FLOOR = 0.75
_VERDICT_MINOR_FLOOR = 0.65


def env_first_str(name: str, fallback: str | None = None) -> str | None:
    """显式 env > Settings 值。"""
    raw = os.environ.get(name)
    return raw if raw is not None else fallback


def env_first_int(name: str, fallback: int) -> int:
    """显式 env > Settings 值；非法 env 记警告并回退 fallback（不静默用错值）。"""
    import logging

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return fallback
    try:
        return int(float(raw))
    except ValueError:
        logging.getLogger(__name__).warning(
            "环境变量 %s=%r 非法（需要整数），已回退 Settings 值 %s", name, raw, fallback
        )
        return fallback


def env_first_float(name: str, fallback: float, *, minimum: float | None = None) -> float:
    """显式 env > Settings 值；非法/越界 env 记警告并回退 fallback。"""
    import logging

    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return fallback
    try:
        value = float(raw)
    except ValueError:
        logging.getLogger(__name__).warning(
            "环境变量 %s=%r 非法（需要数字），已回退 Settings 值 %s", name, raw, fallback
        )
        return fallback
    if minimum is not None and value < minimum:
        logging.getLogger(__name__).warning(
            "环境变量 %s=%s 低于下限 %s，已回退 Settings 值 %s", name, value, minimum, fallback
        )
        return fallback
    return value


def env_first_bool(name: str, fallback: bool) -> bool:
    """显式 env > Settings 值（1/true/yes/on 为真；0/false/no/off 为假）。"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return fallback
    return raw.strip().lower() in ("1", "true", "yes", "on")


def env_first_optional_int(name: str, fallback: int | None) -> int | None:
    """显式 env > Settings 值；空串显式表示 None（用于「关掉种子」的热切换）。"""
    raw = os.environ.get(name)
    if raw is None:
        return fallback
    if not raw.strip():
        return None
    try:
        return int(float(raw))
    except ValueError:
        return fallback


# ---------------------------------------------------------------------------
# 引用真值校验：三态解析（消除「同一变量两套默认」的隐藏分叉）
# ---------------------------------------------------------------------------
# 历史问题：同一个 PAPERFORGE_CITATION_VERIFY 在三处被各自解释，默认值互相矛盾：
#   • citation_verifier.ONLINE_ENABLED  —— 未配置 = 在线（真）
#   • reflection_pipeline               —— 未配置 = 跑本地一致性、不触网
#   • depth_eval_v4._citation_integrity_report —— 未配置 = 完全不计算
# 这些都是「藏在实现里的默认值」：运维无法从配置面板看出实际行为。现统一解析为
# 显式三态，各调用方只允许**显式传入自己的默认档**并写清理由（其余语义完全一致）。
_CV_SKIP_VALUES = frozenset({"0", "false", "off", "no", "none", "disabled", "skip"})
_CV_ONLINE_VALUES = frozenset({"1", "true", "yes", "on", "online", "crossref"})
_CV_VALID_MODES = ("skip", "offline", "online")


def resolve_citation_verify_mode(default: str = "offline") -> str:
    """把引用真值校验开关解析为 ``'skip' | 'offline' | 'online'``。

    通道：显式 os.environ > Settings（Settings 同时读真实 env 与 .env）。
    取值映射（解析结果与 settings.citation_verify 的描述完全一致）：
      - 未配置 / 空串        → ``default``（调用方显式声明的历史默认档）
      - 0/false/off/no/...   → 'skip'    （完全不跑校验）
      - 1/true/yes/on/...    → 'online'  （额外接 Crossref 在线核验真伪）
      - offline 或其他非空串 → 'offline'（仅本地抽取 + 引用一致性，不触网）

    ``default`` 必须显式给出并写进调用方 docstring——不允许再各自发明默认值：
      * 感悟报告链路（单篇，成本低）：default='offline'
      * 深度审稿链路（批量，LLM 已是大头）：default='skip'
    """
    if default not in _CV_VALID_MODES:
        raise ValueError(
            f"resolve_citation_verify_mode(default={default!r}) 非法；"
            f"只接受 {_CV_VALID_MODES}"
        )
    raw = env_first_str("PAPERFORGE_CITATION_VERIFY")
    source = "env"
    if raw is None or not raw.strip():
        try:
            raw = get_settings().citation_verify
            source = ".env/settings"
        except Exception:  # noqa: BLE001 - 配置读取失败不得影响评测链路
            return default
    if raw is None or not raw.strip():
        return default
    mode = raw.strip().lower()
    if mode in _CV_SKIP_VALUES:
        resolved = "skip"
    elif mode in _CV_ONLINE_VALUES:
        resolved = "online"
    else:
        resolved = "offline"
    logging.getLogger(__name__).debug(
        "引用真值校验模式: %s（来源 %s，原始值 %r）", resolved, source, raw
    )
    return resolved
