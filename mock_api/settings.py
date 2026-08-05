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
        default=3000,
        description="Reflection 评审 max_tokens 下限。",
        gt=0,
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
        default=0.6,
        validation_alias=AliasChoices(
            "PAPERFORGE_VERDICT_ACCEPT_THRESHOLD", "VERDICT_ACCEPT_THRESHOLD"
        ),
        description=(
            "verdict accept 阈值。"
            "PeerRead 校准：9B 模型 accept 论文分数中位数 0.752，原 0.8 过严导致 FN=9；"
            "降到 0.6 后 acc 0.438→0.875（FP=1, FN=1）。"
        ),
    )
    verdict_reject_threshold: float = Field(
        default=0.5,
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
            "推荐端口 8082（8080=文本 Qwen）。"
        ),
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
        default=8192,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_CTX", "LLAMA_SERVER_CTX"),
        description="上下文窗口大小。",
        gt=0,
    )
    llama_server_parallel: int = Field(
        default=1,
        validation_alias=AliasChoices("PAPERFORGE_LLAMA_SERVER_PARALLEL", "LLAMA_SERVER_PARALLEL"),
        description="llama-server 并发槽数（--parallel）。单卡 8GB 上限 2。默认 1 向后兼容。",
        ge=1,
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
