"""
DEPTH v4.2 学术论文审稿流水线系统。

架构升级（v3 → v4.1）：
- 新增 QE（全局证据池）：从全文提取 8-12 条证据，分配唯一 ID + 关键词。
- 证据 ID 贯穿 Q2/Q3/Q4/Q5a/Q5b：每个节点输出 evidence_id。
- 三层证据 ID 校验：直接匹配 → 正则兜底 → 关键词提取 → 重试。
- Q1 辅类型判别：secondary_type 参与 Q3 自适应融合。
- Q5a 严重性分级：每个 critique_point 包含 severity（fatal/minor）。
- Q5b 硬约束：严禁编造，未涉及回答"原文暂未涉及，将在终稿补充"。
- Q5c 校准规则：calibrated_score = clamp(base_score + delta, 0, 1)。
- 硬编码 final_verdict 兜底：一票否决优先 + 非 fatal 分支严禁 reject。

架构升级（v4.1 → v4.2，对应《DEPTH算法现状与问题》#1/#2/#3/#4/#5/#6/#9/#11）：
- #1轻 QE 证据池并入 PaperFigure 图表证据（【图表】前缀标记来源，ID 延续编号）。
- #1深 新增 QF 图文一致性节点（无图表时中性跳过零成本），分数对 final_base
  做有界微调（DEPTH_FIGURE_WEIGHT，默认 ±0.05），并注入 Q5a/Q5c 提示词。
- #2/#3 降本：Q2/Q3/Q4 合并为单次多维度调用 Q234（LLM 硬失败回退三次独立调用）。
- #4 hotspot_alignment_score 随合并调用顺带产出（不再独占一次调用），仍不计入 DWM。
- #5 score_std 按需采样：仅 SEMANTIC_OVERRIDE_ENABLED 时 Q5c 才做共识多次采样。
- #6 类型弹性权重改平滑插值（t=confidence/ELASTIC_THRESHOLD，阈值处连续）。
- #9 Q5c delta 区间按辩论结果自适应放宽（硬上限 [-0.25, +0.25]）。
- #11 全部节点显式锁定 temperature（含 QE 与证据重试）。
- DI 统一：Q0/Q1/Q5a/Q5b 改走注入的 self._llm（此前直接调模块级 call_llm，
  导致测试无法全 mock）；生产默认 self._llm 即 call_llm，行为不变。

节点拓扑（v4.2 默认，DAG 波次并行）：
Q0 ∥ Q1 → QE → Q234 ∥ QF → Q5a → Q5b → Q5c
（DEPTH_MERGED_SCORING_ENABLED=False 时 Q234 拆回 Q2 ∥ Q3 ∥ Q4；
  DEPTH_QF_NODE_ENABLED=False 时移除 QF。）
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
import os
import re
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from statistics import median, stdev
from typing import Any, Protocol, cast

from pydantic import BaseModel, Field

from .config import (
    CALIBRATION_SET_PATH,
    CONSENSUS_ENABLED,
    CONSENSUS_SAMPLES,
    CONSENSUS_TEMPERATURE,
    DEPTH_DELTA_ADAPTIVE_ENABLED,
    DEPTH_FIGURE_EVIDENCE_ENABLED,
    DEPTH_FIGURE_WEIGHT,
    DEPTH_MERGED_SCORING_ENABLED,
    DEPTH_QF_NODE_ENABLED,
    HOTSPOTS_SOURCE,
    LOW_CONF_THRESHOLD,
    OIM_FEATURE_WEIGHTS,
    OIM_MIN_TEXT_CHARS,
    OIM_WEIGHT,
    SEMANTIC_OVERRIDE_ENABLED,
    VERDICT_ACCEPT_THRESHOLD,
    VERDICT_REJECT_THRESHOLD,
    get_compute_mode_config,
    get_delta_bounds,
    get_depth_claim_validation_bonus_enabled,
    get_depth_claim_validation_bonus_max,
    get_depth_claim_validation_bonus_min_valid,
    get_depth_claim_validation_bonus_per_claim,
    get_depth_claim_validation_penalty_max,
    get_depth_claim_validation_penalty_per_claim,
    get_depth_q5c_claim_severity_factor,
    get_depth_severity_fatal_weight,
    get_depth_severity_minor_weight,
)
from .depth_calibration import (
    CalibrationResult,
    CalibrationSample,
    correct_final_score,
    load_calibration_set,
)
from .depth_grammar_v4 import (
    q0_grammar,
    q1_grammar,
    q2_grammar,
    q3_grammar,
    q4_grammar,
    q5c_grammar,
    q234_grammar,
    qf_grammar,
)
from .depth_models import DAGOutputs, NodeOutput, PaperContext, QEOutput
from .depth_pipeline import PipelineResult, build_depth_dag
from .depth_prompts_v4 import (
    DEFAULT_HOTSPOTS,
    ELASTIC_THRESHOLD,
    PROMPT_Q0,
    PROMPT_Q1,
    PROMPT_Q2,
    PROMPT_Q4,
    PROMPT_Q5A,
    PROMPT_Q5B,
    PROMPT_Q5C,
    PROMPT_Q234,
    PROMPT_QE,
    PROMPT_QF,
    Q3_PROMPT_VARIANTS,
    Q3_SECONDARY_CHECKLIST,
    Q234_RIGOR_GUIDE,
    TYPE_WEIGHTS,
)
from .figure_claims import apply_curve_correction
from .llm import ChatMessage, get_factory
from .retry_utils import llm_retry
from .severity_classifier import classify_claim_severity

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置常量（温度/token 等 LLM 参数由 config.get_compute_mode_config() 动态提供）
# ---------------------------------------------------------------------------
MAX_CHARS_SHORT = 4000
MAX_CHARS_FULL = 32000
FALLBACK_CHARS = 2000  # 文本分段失败时的兜底长度
LLM_STOP = ["\n\n"]

# v4.2 主席校准 delta 默认区间与硬上限。
# 默认区间已从模块常量迁移到 config.py / settings.py，支持按论文类型或算力模式覆盖。
DELTA_HARD_MIN = -0.25
DELTA_HARD_MAX = 0.25

# 向后兼容别名（保留模块级导出，供外部引用）
from .config import DELTA_DEFAULT_MAX as _DELTA_DEFAULT_MAX
from .config import DELTA_DEFAULT_MIN as _DELTA_DEFAULT_MIN

DELTA_DEFAULT_MIN = _DELTA_DEFAULT_MIN
DELTA_DEFAULT_MAX = _DELTA_DEFAULT_MAX

# 一票否决门槛：需 ≥ 该数量的致命缺陷才触发否决（避免单条 LLM 误判
# 把 0.7~0.95 的论文直接拒稿/大修）。直接数原始 fatal 条数（CritiquePoint 无维度
# 字段，纯文本去重不可靠，故不去做重）。
FATAL_VETO_MIN = 2

# 一票否决分数护栏：校准分 ≥ 该值且 LLM 自身显式判 accept 时，
# 「高分 + accept」与「LLM 断言的 fatal」自相矛盾 → fatal 标签视为噪声，
# 不触发否决。专门保护奠基性/里程碑论文（如 Transformer）被 temp=0 贪婪解码
# 下过度断言的伪 fatal 误杀。0.8~0.9 的边界高分包仍可被合法否决。
FATAL_VETO_ACCEPT_FLOOR = 0.9

# v4.2 图表证据：QE 证据池并入 PaperFigure 的上限条数 / 单条截断长度
MAX_FIGURE_EVIDENCE = 8
FIGURE_EVIDENCE_CHARS = 160

# QF 曲线点二次校验：claim value 与曲线 y 范围比较时的相对容差
_CURVE_Y_TOLERANCE = 0.05


# ---------------------------------------------------------------------------


# ===========================================================================
# Pydantic 数据模型
# ===========================================================================
class EvidenceItem(BaseModel):
    """证据池中的单条证据（含关键词用于双重定位）。"""

    id: str = Field(..., description="证据唯一ID，如 E1, E2")
    content: str = Field(..., description="证据陈述，不超过60字（默认中文，供 LLM 提示使用）")
    content_zh: str = Field(default="", description="证据陈述中文版本")
    content_en: str = Field(default="", description="证据陈述英文版本")
    section: str = Field(default="", description="证据所在章节")
    keywords: list[str] = Field(default_factory=list, description="证据关键词，3-5个，用于辅助定位")
    severity: str = Field(default="minor", pattern=r"^fatal|minor$")


class Q0Result(BaseModel):
    reasoning: str = ""
    evidence: str = ""
    has_substance: bool = True
    expectation: float = Field(default=0.5, ge=0.0, le=1.0)


class Q1Result(BaseModel):
    reasoning: str = ""
    evidence: str = ""
    type: str = Field(default="B", pattern=r"^[ABCD]$")
    secondary_type: str = Field(default="none", pattern=r"^[ABCD]|none$")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class QEResult(BaseModel):
    reasoning: str = ""
    evidence_pool: list[EvidenceItem] = Field(default_factory=list)


class Q2Result(BaseModel):
    reasoning: str = ""
    core_contribution: str = ""
    novelty_score: float = Field(default=0.5, ge=0.0, le=1.0)
    hotspot_alignment_score: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_id: str = ""
    verified: bool = True


class Q3Result(BaseModel):
    reasoning: str = ""
    rigor_score: float = Field(default=0.5, ge=0.0, le=1.0)
    missing_items: list[str] = Field(default_factory=list)
    evidence_id: str = ""
    verified: bool = True


class Q4Result(BaseModel):
    reasoning: str = ""
    influence_score: float = Field(default=0.5, ge=0.0, le=1.0)
    reproducibility_score: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_id: str = ""
    verified: bool = True


class CritiquePoint(BaseModel):
    point: str = Field(..., description="质疑内容，不超过40字")
    severity: str = Field(default="minor", pattern=r"^fatal|minor$")


class Q5aResult(BaseModel):
    critique_points: list[CritiquePoint] = Field(default_factory=list)
    evidence_id: str = ""
    verified: bool = True


class Q5bResult(BaseModel):
    defense_points: list[str] = Field(default_factory=list)
    evidence_id: str = ""
    verified: bool = True


class Q5cResult(BaseModel):
    reasoning: str = ""
    # v4.2：pydantic 边界放宽到全局硬上限；实际有效区间由 _adaptive_delta_bounds 收紧
    delta: float = Field(default=0.0, ge=DELTA_HARD_MIN, le=DELTA_HARD_MAX)
    calibrated_score: float = Field(default=0.5, ge=0.0, le=1.0)
    score_std: float = Field(default=0.0, ge=0.0, le=1.0)  # P1-1 自洽采样标准差
    verdict: str = Field(default="major_revision")
    llm_verdict: str = Field(default="major_revision")
    delta_missing: bool = False  # LLM 未输出 delta 时标记
    balancer_log: str = ""  # 平衡者降级日志


class QFResult(BaseModel):
    """QF 图文一致性审查结果（v4.2 新增节点）。

    has_figures=False 时 figure_consistency_score 固定为中性 0.5，
    _compute_dwm 的图文微调分支会跳过（不产生任何分数影响）。
    """

    reasoning: str = ""
    figure_consistency_score: float = Field(default=0.5, ge=0.0, le=1.0)
    inconsistency_flags: list[str] = Field(default_factory=list)
    has_figures: bool = False
    evidence_id: str = ""
    verified: bool = True
    claim_validation_penalty: float = Field(default=0.0, ge=0.0)
    claim_validation_bonus: float = Field(default=0.0, ge=0.0)


class FinalVerdict(BaseModel):
    calibrated_score: float = Field(ge=0.0, le=1.0)
    final_verdict: str
    override_reason: str = ""
    llm_verdict: str = ""
    has_fatal: bool = False
    has_minor: bool = False
    fatal_count: int = 0
    minor_count: int = 0
    # v4.2 图表覆盖状态：analyzed / missing / disabled
    figure_coverage: str = "disabled"


@dataclass
class ObjectiveFeatures:
    """OIM 客观特征向量（P0-1）。"""

    ablation: float = 0.0
    repro_signal: float = 0.0
    citation_density: float = 0.0
    structure: float = 0.0
    formalism: float = 0.0

    def weighted_score(self, weights: dict[str, float]) -> float:
        vals = {
            "ablation": self.ablation,
            "repro_signal": self.repro_signal,
            "citation_density": self.citation_density,
            "structure": self.structure,
            "formalism": self.formalism,
        }
        total = 0.0
        wsum = 0.0
        for k, w in weights.items():
            total += w * vals.get(k, 0.0)
            wsum += w
        return total / wsum if wsum > 0 else 0.0


class DepthV4Result(BaseModel):
    paper_id: str = ""
    title: str = ""
    has_substance: bool = True
    expectation: float = 0.5
    q0_reasoning: str = ""
    q0_evidence: str = ""
    paper_type: str = "B"
    secondary_type: str = "none"
    confidence: float = 0.5
    q1_reasoning: str = ""
    novelty_score: float = 0.5
    hotspot_alignment_score: float = 0.5
    core_contribution: str = ""
    q2_reasoning: str = ""
    rigor_score: float = 0.5
    missing_items: list[str] = Field(default_factory=list)
    q3_reasoning: str = ""
    influence_score: float = 0.5
    reproducibility_score: float = 0.5
    q4_reasoning: str = ""
    critique_points: list[CritiquePoint] = Field(default_factory=list)
    defense_points: list[str] = Field(default_factory=list)
    # v4.2 QF 图文一致性（无图表 / 节点关闭时为中性默认值，不影响评分）
    figure_consistency_score: float = 0.5
    figure_flags: list[str] = Field(default_factory=list)
    figure_evidence_count: int = 0
    figure_coverage: str = "disabled"
    # claim-validation 惩罚 / 奖励（默认 0，仅当存在图表数据且开启时非零）
    claim_validation_penalty: float = 0.0
    claim_validation_bonus: float = 0.0
    qf_reasoning: str = ""
    calibrated_score: float = 0.5
    delta: float = 0.0
    delta_missing: bool = False
    chair_reasoning: str = ""
    llm_verdict: str = "major_revision"
    final_verdict: str = "major_revision"
    override_reason: str = ""
    evidence_pool: list[EvidenceItem] = Field(default_factory=list)
    evidence_checks: dict[str, bool] = Field(default_factory=dict)
    # B5: 各节点引用的真实证据ID字符串（如 {"Q2": "E3", "Q3": "E1", ...}），
    # 与 evidence_checks（bool 校验结果）分离，避免 evidence_id 存布尔。
    evidence_ids: dict[str, str] = Field(default_factory=dict)
    base_score: float = 0.5
    weights: dict[str, float] = Field(default_factory=dict)
    node_score_stds: dict[str, float] = Field(default_factory=dict)
    node_logs: list[str] = Field(default_factory=list)
    evaluated_at: str = ""
    # ADR-014 P0：可复现性快照。记录本次评测实际使用的 seed / 模型 / 提供商 / 温度，
    # 使任何一次评分都可被他人精确复现。空 dict 表示未启用可复现种子。
    llm_params_snapshot: dict = Field(default_factory=dict)
    # ADR-014 P4：引用真值校验报告（解决 W8：此前只数引用个数、不查真伪）。
    # 仅当 PAPERFORGE_CITATION_VERIFY 开启时填充，默认空 dict（零开销、向后兼容）。
    citation_integrity: dict = Field(default_factory=dict)
    # ADR-014 P2：分数不确定性（bootstrap 95% CI + 不确定门控）。解决 W4：此前只有点估计。
    # 仅当 PAPERFORGE_UNCERTAINTY_GATE 开启时填充，默认空 dict（零开销、向后兼容）。
    score_uncertainty: dict = Field(default_factory=dict)


# v4.2: Rebuild DAGOutputs after all result types are defined.
# Required because DAGOutputs uses TYPE_CHECKING-only imports for field types.
DAGOutputs.model_rebuild(force=True)


from .scorers.legacy import _score_text_figure_consistency  # noqa: F401  # v4.2 拆分
from .services.data_provider import DataProvider  # noqa: E402  # v4.2 分层解耦
from .services.db_provider import (  # noqa: E402  # v4.2 分层解耦
    get_db_data_provider,
)

# ===========================================================================
# 文本分段工具（含 fallback）
SECTION_PATTERNS = [
    re.compile(r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:abstract|ABSTRACT)\s*\n", re.IGNORECASE),
    re.compile(
        r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:introduction|INTRODUCTION|1\.?\s*intro)\s*\n", re.IGNORECASE
    ),
    re.compile(
        r"(?:^|\n)\s*(?:\d+\.?\s*)?(?:conclusion|conclusions|CONCLUDING REMARKS|"
        r"summary|discussion)\s*\n",
        re.IGNORECASE,
    ),
    re.compile(r"(?:^|\n)\s*摘\s*要\s*\n"),
    re.compile(r"(?:^|\n)\s*(?:引言|绪论|前言|1\.?\s*引)\s*\n"),
    re.compile(r"(?:^|\n)\s*(?:结论|总结|讨论|总\s*结)\s*\n"),
]


def _find_section_boundaries(text: str) -> list[tuple[int, str]]:
    boundaries: list[tuple[int, str]] = []
    for pattern in SECTION_PATTERNS:
        for match in pattern.finditer(text):
            boundaries.append((match.start(), match.group().strip()))
    boundaries.sort(key=lambda x: x[0])
    deduped: list[tuple[int, str]] = []
    for pos, name in boundaries:
        if not deduped or pos - deduped[-1][0] > 10:
            deduped.append((pos, name))
    return deduped


def _extract_section(
    text: str, boundaries: list[tuple[int, str]], section_keywords: list[str]
) -> str:
    for i, (pos, name) in enumerate(boundaries):
        if any(kw in name.lower() for kw in section_keywords):
            end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(text)
            return text[pos:end].strip()
    return ""


def segment_paper_text(
    full_text: str, abstract: str = "", max_short: int | None = None, max_full: int | None = None
) -> dict[str, str]:
    """将论文全文切分为三个视图。

    若章节标题不标准导致分段失败，使用固定长度 fallback。
    max_short / max_full 默认从动态算力配置读取，支持手动覆盖。
    """
    if max_short is None or max_full is None:
        cfg = get_compute_mode_config()
        if max_short is None:
            max_short = int(cfg.get("max_chars_short", MAX_CHARS_SHORT))
        if max_full is None:
            max_full = int(cfg.get("max_chars_full", MAX_CHARS_FULL))
    boundaries = _find_section_boundaries(full_text)

    if abstract and len(abstract) >= 20:
        abs_text = abstract.strip()
    else:
        abs_text = _extract_section(full_text, boundaries, ["abstract", "摘要"])

    intro = _extract_section(
        full_text,
        boundaries,
        [
            "introduction",
            "intro",
            "引言",
            "绪论",
            "前言",
        ],
    )
    conclusion = _extract_section(
        full_text,
        boundaries,
        [
            "conclusion",
            "conclusions",
            "concluding",
            "discussion",
            "summary",
            "结论",
            "总结",
            "讨论",
        ],
    )

    # Fallback：若无法通过章节标题切分，使用固定长度截断
    if not intro and not conclusion and len(full_text) > FALLBACK_CHARS * 2:
        logger.warning("DEPTH v4.1 文本分段失败（无标准章节标题），使用固定长度 fallback")
        paper_abstract_intro = full_text[:FALLBACK_CHARS]
        paper_abstract_conclusion = full_text[-FALLBACK_CHARS:]
    else:
        paper_abstract_intro = _smart_truncate(_join_sections(abs_text, intro), max_short)
        paper_abstract_conclusion = _smart_truncate(_join_sections(abs_text, conclusion), max_short)

    return {
        "paper_abstract_intro": paper_abstract_intro,
        "paper_full_text": full_text[:max_full],
        "paper_abstract_conclusion": paper_abstract_conclusion,
    }


def _join_sections(*sections: str) -> str:
    parts = [s.strip() for s in sections if s.strip()]
    return "\n\n".join(parts)


def _smart_truncate(text: str, max_chars: int) -> str:
    if not text or len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    for sep in ["。\n", "。", "！", "？", ".\n", ". ", "!", "?"]:
        pos = truncated.rfind(sep)
        if pos > max_chars * 0.6:
            return truncated[: pos + len(sep.rstrip())]
    return truncated


# ===========================================================================
# LLM 调用接口（同步 + 异步）
# ===========================================================================
def _get_llm_params() -> tuple[float, int]:
    """从动态算力配置获取 LLM 调用参数。"""
    cfg = get_compute_mode_config()
    temperature = float(cfg.get("temperature", 0.0))
    max_tokens = int(cfg.get("max_tokens", 512))
    return temperature, max_tokens


@llm_retry
def _call_llm_provider(
    messages: list[ChatMessage],
    temperature: float,
    max_tokens: int,
    **extra_kwargs: Any,
) -> Any:
    """直接调 provider.chat，由 @llm_retry 提供指数退避重试。

    v4.2: 仅对 provider 调用层做 retry（3 次，1s/2s/4s），
    外层 call_llm 仍然 fail-open 返回 ""，保持向后兼容。
    """
    from .llm.reproducibility import get_eval_seed

    factory = get_factory()
    provider = factory.get_provider()
    result = provider.chat(messages, temperature=temperature, max_tokens=max_tokens, **extra_kwargs)
    # ADR-014 P0：把本次实际使用的种子 / 模型 / 提供商写入结果 meta，
    # 供结果存档，使分数可精确复现。任何异常都不应影响主流程。
    try:
        seed = extra_kwargs.get("seed") if "seed" in extra_kwargs else get_eval_seed()
        if seed is not None:
            result.meta["seed"] = seed
        result.meta["model"] = getattr(provider, "model", None)
        result.meta["provider"] = getattr(provider, "provider_name", None)
    except Exception:  # noqa: BLE001 - 快照仅为存档
        pass
    return result


def call_llm(
    prompt: str,
    system_prompt: str = "",
    max_tokens: int | None = None,
    temperature: float | None = None,
    grammar: str | None = None,
) -> str | None:
    """同步调用 LLM（参数默认从动态算力配置读取）。

    【retry】v4.2 新增 tenacity 指数退避重试（3 次，1s/2s/4s，最长等 8s），
    覆盖本地 llama.cpp 网络波动与在线 API 偶发超时。

    【watchdog】使用进程级 ThreadPoolExecutor（lazy-init）包装 provider.chat，
    使用 future.result(timeout=120) 强制宕机走象限。即使 provider 本身不遵守
    timeout=（如老版 zhipuai SDK 不接受参数），该 watchdog 最多在 120s 后
    返回 None，不会无限阻塞主框架。但 future 所在线程仍会在后台运行 → 不影响
    asyncio.run 的 shutdown_default_executor（因为 call_llm 都走我们自己
    的 watchdog pool，与 asyncio 默认 executor 隔离）。

    【缓存】内嵌 TTL 内存缓存（key = hash(prompt+system_prompt+temperature+max_tokens+grammar)），
    避免同一 prompt 在重试/证据校验环节被重复调用。TTL 默认 300s，可通过
    PAPERFORGE_LLM_CACHE_TTL 环境变量调整（设为 0 禁用缓存）。

    【grammar】可选 GBNF 语法字符串。仅在配置启用（settings.depth_grammar_enabled）
    且当前后端为 llama.cpp / llama-server（OpenAI 兼容）时透传给 provider，
    约束评分节点输出为可解析结构。传给真实 OpenAI/Zhipu/DeepSeek 端点会被拒，
    因此默认关闭、需显式开启。

    Returns:
        LLM 响应内容，或 None（表示调用失败 / 超时 / 不可恢复错误）。
        调用方必须显式检查 None，而非用空字符串继续解析（会产出垃圾分数）。
    """
    # ADR-014 P0：可复现种子（延迟导入，避免模块顶层循环依赖）
    from .llm.reproducibility import get_eval_seed, with_eval_seed

    # ── 缓存检查 ──
    cache_key = 0  # 默认值，防止缓存块异常时未赋值
    try:
        if temperature is None or max_tokens is None:
            _t, _m = _get_llm_params()
            if temperature is None:
                temperature = _t
            if max_tokens is None:
                max_tokens = _m
        cache_key = _llm_cache_key(
            system_prompt, prompt, temperature, max_tokens, grammar, get_eval_seed()
        )
        cached = _llm_cache_get(cache_key)
        if cached is not None:
            logger.debug("DEPTH v4.1 LLM 缓存命中")
            return cached
    except Exception:
        pass  # 缓存操作异常不影响主流程

    try:
        # VRAM 互斥调度：确保 Qwen 在显存（必要时释放 OCR 并拉起 llama-server）。
        # 冷启动未就绪时记录事件供前端提示，不抛异常以保持 DEPTH 降级兼容。
        from .vram_scheduler import get_vram_scheduler

        get_vram_scheduler().request_text(wait=False)
        factory = get_factory()
        _ = factory.get_provider()  # 确保 provider 已初始化（_call_llm_provider 内部独立获取）
        messages: list[ChatMessage] = []
        if system_prompt:
            messages.append(ChatMessage(role="system", content=system_prompt))
        messages.append(ChatMessage(role="user", content=prompt))
        # GBNF 语法约束：仅在启用且传入 grammar 时透传给 provider。
        # llama.cpp/llama-server 通过 OpenAI 兼容接口接受顶层 `grammar` 字段
        # （detect_provider 对本地 127.0.0.1 端点返回 "openai"，走 OpenAIProvider
        # → build_payload 把 kwargs 直接并入 HTTP body）。真实 OpenAI/Zhipu/DeepSeek
        # 端点不识别该字段会 400，故由 settings.depth_grammar_enabled 显式门控，
        # 默认关闭 → 未开启时行为与之前完全一致。
        extra_kwargs: dict[str, Any] = {}
        if grammar and _depth_grammar_enabled():
            extra_kwargs["grammar"] = grammar
        # ADR-014 P0：注入可复现种子（仅当 PAPERFORGE_EVAL_SEED 已设置；
        # 未设置时等同于历史行为，完全向后兼容）。with_eval_seed 已在函数顶部导入。
        extra_kwargs = with_eval_seed(extra_kwargs)
        executor = _get_llm_watchdog_executor()
        future = executor.submit(
            _call_llm_provider,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **extra_kwargs,
        )
        # 【消音】立即挂 callback 消费 worker 线程的最终异常；
        # 否则若 SDK 在后台 raise（超时或正常返回后），stderr 会打印
        # "Exception was raised in Future" 噪声。 done_callback 在主线程上
        # 被 invoke 一次 (future 完成时)，无重入风险。
        future.add_done_callback(_silence_future_exception)
        try:
            result = future.result(timeout=_LLM_WATCHDOG_TIMEOUT)
            content = result.content or ""
            if content:
                _llm_cache_set(cache_key, content)
            return content
        except TimeoutError:
            # builtin TimeoutError；concurrent.futures.TimeoutError 是其子类
            # (Python 3.11+ 仍可用，但统一用 builtin 不再绑定 conftest API)
            _track_orphan_future(future)
            stats = get_watchdog_stats()
            logger.warning(
                "DEPTH v4.1 LLM watchdog 硬超时 (%.0fs) — provider 未及时返回；"
                "累计孤儿=%d，待回收=%d",
                _LLM_WATCHDOG_TIMEOUT,
                stats["orphans_total"],
                stats["orphans_pending"],
            )
            return None
    except Exception as e:
        logger.warning("DEPTH v4.1 LLM 调用失败：%s", e)
        return None


# ---------------------------------------------------------------------------
# LLM 响应 TTL 缓存（避免重复调用：证据校验重试、相同 prompt 复用）
# ---------------------------------------------------------------------------

from .settings import get_settings

_LLM_CACHE_TTL = get_settings().llm_cache_ttl  # 秒，0=禁用
_llm_cache: dict[int, tuple[float, str]] = {}  # key → (timestamp, content)
_llm_cache_lock = threading.Lock()
_MAX_CACHE_ENTRIES = 128  # 防止内存无限增长


def _llm_cache_key(
    system_prompt: str,
    prompt: str,
    temperature: float,
    max_tokens: int,
    grammar: str | None = None,
    seed: int | None = None,
) -> int:
    """生成缓存键（sha256，进程重启后仍稳定）。

    grammar 纳入键：同一 prompt 在有/无语法约束下输出不同，不能共用缓存。
    seed 纳入键（ADR-014 P0）：固定种子下输出确定，但不同种子必须隔离缓存。
    """
    raw = f"{system_prompt}\n{prompt}\n{temperature}\n{max_tokens}\n{grammar or ''}\n{seed}"
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest(), 16) % (2**63)


def _depth_grammar_enabled() -> bool:
    """是否启用 GBNF 语法约束（仅当后端为 llama.cpp/llama-server 时应开启）。"""
    try:
        return bool(get_settings().depth_grammar_enabled)
    except Exception:
        return False


def _llm_cache_get(key: int) -> str | None:
    """从缓存获取值（TTL 过期返回 None）。"""
    if _LLM_CACHE_TTL <= 0:
        return None
    with _llm_cache_lock:
        entry = _llm_cache.get(key)
        if entry is None:
            return None
        ts, content = entry
        if time.monotonic() - ts > _LLM_CACHE_TTL:
            del _llm_cache[key]
            return None
        return content


def _llm_cache_set(key: int, content: str) -> None:
    """写入缓存（含 FIFO 淘汰）。"""
    if _LLM_CACHE_TTL <= 0:
        return
    with _llm_cache_lock:
        if len(_llm_cache) >= _MAX_CACHE_ENTRIES:
            # FIFO 淘汰最旧条目
            oldest_key = min(_llm_cache, key=lambda k: _llm_cache[k][0], default=None)
            if oldest_key is not None:
                del _llm_cache[oldest_key]
        _llm_cache[key] = (time.monotonic(), content)


# ---------------------------------------------------------------------------
# LLM watchdog 全局 executor（避免 provider SDK 不遵守超时导致阻塞线程池）
# ---------------------------------------------------------------------------


def _env_positive_float(name: str, default: float) -> float:
    """读取正浮点环境变量；非法值回退默认值，避免启动阶段崩溃。"""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        logger.warning("忽略非法环境变量 %s，使用默认值 %s", name, default)
        return default
    if value <= 0:
        logger.warning("环境变量 %s 必须大于 0，使用默认值 %s", name, default)
        return default
    return value


_LLM_WATCHDOG_TIMEOUT = _env_positive_float(
    "PAPERFORGE_LLM_WATCHDOG_TIMEOUT", 120.0
)  # 秒；任何 provider.chat() 超此时长视为僵尸调用
_llm_watchdog_executor: concurrent.futures.ThreadPoolExecutor | None = None
_llm_watchdog_lock = threading.Lock()

# 超时被释放的“孤儿”future 集合（bounded → 防内存泄漏）。
# 每个孤儿仍在后台线程运行到 SDK 自然返回；我们仅记录以避免引用被 GC
# 导致线程二次 raise + 输出 “Exception in thread” 噪声。但这不能真正中断
# 阻塞 C 扩展调用——只能等 SDK 自己返回或超时。
_orphan_futures: list[concurrent.futures.Future[Any]] = []
_orphan_futures_lock = threading.Lock()
_MAX_ORPHAN_FUTURES = 64  # 队列上限；超出后拖弃最旧引用
_orphan_total = 0  # 累计观察到的孤儿数（监控指标）


def _track_orphan_future(future: concurrent.futures.Future[Any]) -> None:
    """记录超时被释放的 future，防止引用立即被 GC、防止引用 GC 后被弃用。

    控制内存：拖弃最旧引用（线程仍运行，但对象可被回收）。

    【消音前提】call_llm 在 executor.submit(...) 后同步调用 add_done_callback 注册。
    若未来看 require 此函数独立调用，需额外消费一次 future.exception()，否则
    同 future 会伴生 'Exception was raised in Future' 噪声。
    """
    global _orphan_total
    with _orphan_futures_lock:
        _orphan_total += 1
        _orphan_futures.append(future)
        while len(_orphan_futures) > _MAX_ORPHAN_FUTURES:
            _orphan_futures.pop(0)


def _silence_future_exception(future: concurrent.futures.Future[Any]) -> None:
    """Callback: 消费 future 异常，避免 Python 打 'Exception was raised in Future' 噪声。

    重入安全：exception() 调用多次不会报错。CancelledError 在 Python 3.8+ 是 Exception
    子类，被 except Exception 覆盖；不重复声明。
    【与项目其他 worker 一致】tasks._run_worker/export_tasks._worker 均用 except Exception 单层。
    """
    try:
        future.exception()
    except Exception:  # noqa: BLE001 - callback 必须吞下所有异常
        pass


def get_watchdog_stats() -> dict[str, int]:
    """返回 LLM watchdog 当前状态（供监控/调试）。"""
    with _orphan_futures_lock:
        return {
            "orphans_pending": len(_orphan_futures),
            "orphans_total": _orphan_total,
        }


def _get_llm_watchdog_executor() -> concurrent.futures.ThreadPoolExecutor:
    """获取进程级 LLM watchdog 线程池（lazy-init + 自动恢复）。

    当孤儿未来数超过线程池容量时，视为饱和，自动重建执行器。
    旧执行器的线程由 GC 回收（对应 HTTP 请求在超时后自动释放）。
    """
    global _llm_watchdog_executor
    with _llm_watchdog_lock:
        max_w = min(16, (os.cpu_count() or 4) * 4)
        with _orphan_futures_lock:
            orphan_count = len(_orphan_futures)
        # 孤儿数超过线程池容量 → 饱和，强制重建
        if _llm_watchdog_executor is not None and orphan_count >= max_w:
            logger.warning(
                "LLM watchdog 线程池饱和（孤儿=%d >= max_workers=%d），强制重建",
                orphan_count,
                max_w,
            )
            try:
                _llm_watchdog_executor.shutdown(wait=False)
            except Exception:
                pass
            _llm_watchdog_executor = None
            with _orphan_futures_lock:
                _orphan_futures.clear()
        if _llm_watchdog_executor is None:
            _llm_watchdog_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=max_w,
                thread_name_prefix="llm-watchdog-",
            )
        return _llm_watchdog_executor


async def async_call_llm(
    prompt: str, system_prompt: str = "", max_tokens: int | None = None
) -> str | None:
    """异步调用 LLM（为未来并发支持预留）。

    当前实现通过 asyncio.to_thread 包装同步调用。
    将来可直接替换为真正的异步 HTTP 调用（aiohttp/httpx）。
    """
    return await asyncio.to_thread(call_llm, prompt, system_prompt, max_tokens)


# ===========================================================================
# 硬证据检测：消融实验识别（非关键词匹配，支持多种表达方式）
# ===========================================================================
_ABLATION_PATTERNS: list[re.Pattern[str]] = [
    # 英文消融表达
    re.compile(r"ablation\s*(study|studies|experiment|analysis)?", re.IGNORECASE),
    re.compile(r"(we|we\s+also)\s+(vary|varied|remove|removed|removing)", re.IGNORECASE),
    re.compile(
        r"(without|w/o)\s+(the\s+)?(\w+\s+)?(module|component|branch|head|layer|block)",
        re.IGNORECASE,
    ),
    re.compile(r"component\s*(analysis|contribution|wise)", re.IGNORECASE),
    re.compile(r"factor\s*analysis", re.IGNORECASE),
    re.compile(
        r"(contribution|effect)\s+of\s+(each|every|individual)\s+(module|component|part)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(remov|removing)\s+(the\s+)?(\w+\s+)?(module|component|branch|head|layer|block|feature)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(drop|dropping|disable|disabling|turn\s*off)\s+(the\s+)?(\w+\s+)?(module|component|branch|head)",
        re.IGNORECASE,
    ),
    # 中文消融表达
    re.compile(r"消融\s*(实验|研究|分析)?"),
    re.compile(r"(去掉|移除|删除|关闭|禁用)\s*(了|掉)?\s*(\S{1,4})\s*(模块|组件|分支|层|头)"),
    re.compile(r"(逐个|逐一|分别)\s*(分析|评估|测试|验证)\s*(每个|各)\s*(模块|组件)"),
    re.compile(r"贡献度\s*(分析|评估)"),
]


def _detect_ablation_evidence(full_text: str) -> tuple[bool, list[str]]:
    """检测论文全文是否包含消融实验证据。

    不依赖单一关键词 "ablation"，覆盖多种自然表达方式：
    - \"we vary X\" / \"we remove Y\"（消融实验的核心操作）
    - \"without the Z module\" / \"w/o head\"（组件移除）
    - \"component analysis\" / \"contribution of each\"（贡献分析）
    - 中文：\"消融实验\"、\"去掉XX模块\"、\"贡献度分析\"

    Returns:
        (found: bool, matched_patterns: list[str]) —— found 为 True 表示检测到消融证据，
        matched_patterns 列出命中的模式供日志使用。
    """
    if not full_text:
        return False, []
    matched: list[str] = []
    for pat in _ABLATION_PATTERNS:
        m = pat.search(full_text)
        if m:
            matched.append(m.group(0)[:80])
    # 至少命中 2 个不同模式才认为有消融证据（单模式可能误匹配）
    found = len(matched) >= 2
    return found, matched


# 代码 / 数据 / 模型权重 公开发布信号的检测模式
_REPO_RELEASE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"https?://github\.com/\S+", re.I),
    re.compile(r"https?://huggingface\.co/\S+", re.I),
    re.compile(r"https?://gitlab\.com/\S+", re.I),
    re.compile(
        r"(?:our|the)\s+code\s+(?:is\s+)?(?:available|released|publicly\s+available|open[\s-]?source)",
        re.I,
    ),
    re.compile(
        r"(?:we\s+)?(?:release|open[\s-]?source|publicly\s+release)\s+(?:our\s+)?(?:code|implementation|source)",
        re.I,
    ),
    re.compile(r"代码[已]?\s*(?:开源|公开|发布|可获取|公开可用|公开可用)"),
]


def _detect_code_release(full_text: str) -> bool:
    """检测正文是否包含代码/数据/模型权重的公开获取信号。"""
    if not full_text:
        return False
    return any(pat.search(full_text) for pat in _REPO_RELEASE_PATTERNS)


def _cap_reproducibility(repro: float, full_text: str) -> tuple[float, bool]:
    """缺发布确定性封顶。

    当可复现性评分偏高（>0.6）但正文无任何代码/数据/权重公开获取方式时，
    封顶到 0.6。这是针对「系统对缺代码惩罚不够」的确定性纠偏：仅下拉高分段，
    不误伤确有发布的论文。返回 (封顶后分数, 是否触发封顶)。
    """
    repro = _clamp_float(repro)
    if repro > 0.6 and not _detect_code_release(full_text):
        return 0.6, True
    return repro, False


# ===========================================================================
# 辅助函数
# ===========================================================================
def _clamp_float(val: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        f = float(val)
    except (TypeError, ValueError):
        return (lo + hi) / 2
    return max(lo, min(hi, f))


def _extract_llm_fields(
    raw: str | None,
    patterns: dict[str, str],
    *,
    none_to_empty: bool = True,
) -> dict[str, str]:
    """从 LLM 原始输出中提取结构化字段。

    Args:
        raw: LLM 原始输出文本；None 时返回空字典（避免用空字符串解析出伪有效数据）。
        patterns: 字段名 -> 正则模式的映射。
        none_to_empty: 是否将 "none" 视为空字符串。
    """
    if raw is None:
        return {}
    data: dict[str, str] = {}
    for key, pat in patterns.items():
        m = re.search(pat, raw)
        if not m:
            continue
        val = m.group(1).strip()
        if none_to_empty and val.lower() == "none":
            val = ""
        data[key] = val
    return data


def _clamp_list(val: Any, max_len: int = 10) -> list[Any]:
    if not isinstance(val, list):
        return []
    return val[:max_len]


def _format_evidence_pool_text(pool: dict[str, str]) -> str:
    """格式化证据池为 LLM 可读文本（内容已含关键词）。"""
    if not pool:
        return "（空）"
    lines = [f"  {eid}: {content}" for eid, content in pool.items()]
    return "\n".join(lines)


_MAX_AXIS_TICKS = 10
_MAX_LEGEND_ITEMS = 6


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


# v4.2: _score_text_figure_consistency → scorers/legacy.py (imported at top of file)


def _extract_key_terms(text: str) -> set[str]:
    """从文本中提取关键术语用于辩护-质疑匹配。

    中英文混合处理：
    - 英文：提取长度 ≥ 3 的单词（过滤停用词）
    - 中文：提取 2-gram 特征（重叠词序列）
    """
    _EN_STOP_WORDS = {
        "the",
        "and",
        "for",
        "was",
        "not",
        "has",
        "can",
        "are",
        "this",
        "that",
        "with",
        "from",
        "have",
        "been",
        "will",
        "also",
        "were",
    }
    terms: set[str] = set()
    # 英文单词（≥3 字符）
    en_words = re.findall(r"[a-zA-Z]{3,}", text)
    for w in en_words:
        wl = w.lower()
        if wl not in _EN_STOP_WORDS:
            terms.add(wl)
    # 中文 2-gram 特征
    # 使用 Unicode 范围匹配 CJK 字符
    cjk_chars = re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", text)
    for i in range(len(cjk_chars) - 1):
        terms.add(cjk_chars[i] + cjk_chars[i + 1])
    # 中文单字也纳入（重要技术术语可能是单字）
    for ch in cjk_chars:
        terms.add(ch)
    return terms


def _strip_cot(raw: str) -> str:
    """剔除 <thinking>...</thinking> 思维链块（P1-3）。"""
    if not raw:
        return raw
    return re.sub(r"<thinking>.*?</thinking>", "", raw, flags=re.DOTALL | re.IGNORECASE)


def _extract_objective_features(full_text: str) -> ObjectiveFeatures | None:
    """从论文全文中抽取 OIM 客观特征（P0-1）。

    所有特征均通过本地正则/规则派生，不依赖外部 API。
    文本长度 < OIM_MIN_TEXT_CHARS 时返回 None，调用方应退化到纯 LLM 行为。
    """
    if not full_text or len(full_text) < OIM_MIN_TEXT_CHARS:
        return None

    text = full_text.lower()

    # ablation: 复用 _detect_ablation_evidence
    has_ablation, _ = _detect_ablation_evidence(full_text)
    ablation = 1.0 if has_ablation else 0.0

    # repro_signal: 代码/数据/环境可复现信号
    repro_patterns = [
        r"github\.com/\S+",
        r"gitlab\.com/\S+",
        r"bitbucket\.org/\S+",
        r"code.*available",
        r"open.source",
        r"源代码",
        r"代码已开源",
        r"dataset.*available",
        r"we release.*data",
        r"we release.*code",
        r"appendix.*\d+",
        r"supplementary material",
    ]
    repro_hits = sum(1 for pat in repro_patterns if re.search(pat, text))
    repro_signal = min(1.0, repro_hits / 3.0)

    # citation_density: 参考文献密度（粗略估计 [1]-[N] 或 (Author, year) 模式）
    bracket_refs = len(re.findall(r"\[\d+\]", full_text))
    paren_refs = len(re.findall(r"\([a-z\-]+(?:\s+et\s+al\.?)?,\s*\d{4}[a-z]?\)", text))
    total_refs = bracket_refs + paren_refs
    # 归一化：假设 10000 字论文 50 篇参考文献为 1.0
    citation_density = min(1.0, total_refs / max(len(full_text) / 200, 1.0))

    # structure: 标准结构完整度（摘要/引言/方法/实验/结论/参考文献）
    structure_keywords = [
        "abstract",
        "introduction",
        "method",
        "experiment",
        "result",
        "conclusion",
        "reference",
        "摘要",
        "引言",
        "方法",
        "实验",
        "结果",
        "结论",
        "参考文献",
    ]
    struct_hits = sum(1 for kw in structure_keywords if re.search(rf"\b{re.escape(kw)}\b", text))
    structure = min(1.0, struct_hits / 5.0)

    # formalism: 形式化信号（公式/定理/证明）
    formalism_patterns = [
        r"\\begin\{theorem\}",
        r"\\begin\{proof\}",
        r"\\begin\{equation\}",
        r"proof\.",
        r"theorem\s+\d+",
        r"lemma\s+\d+",
        r"proposition\s+\d+",
        r"证明",
        r"定理",
        r"引理",
    ]
    formal_hits = sum(1 for pat in formalism_patterns if re.search(pat, text))
    formalism = min(1.0, formal_hits / 3.0)

    return ObjectiveFeatures(
        ablation=round(ablation, 4),
        repro_signal=round(repro_signal, 4),
        citation_density=round(citation_density, 4),
        structure=round(structure, 4),
        formalism=round(formalism, 4),
    )


# ===========================================================================
# DepthReviewer v4.1
# ===========================================================================
class _LLMCallable(Protocol):
    """LLM 调用函数协议（支持 prompt + 可选 system_prompt/max_tokens/temperature）。

    v4.2: 返回 str | None — None 表示 LLM 调用失败/超时，调用方必须显式检查。
    """

    def __call__(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str | None: ...


class DepthReviewer:
    """DEPTH v4.1 论文审稿流水线。

    使用方式：
        reviewer = DepthReviewer(llm_func=call_llm, compute_mode="deep")
        # 串行执行（适用于同步调试）
        result = reviewer.review(paper_id, title, full_text, abstract, hotspots=...)
        # DAG 拓扑并行执行（推荐，自动失败传播 + 计时）
        result = await reviewer.review_async_dag(paper_id, title, full_text, abstract, hotspots=...)
    """

    def __init__(
        self,
        llm_func: _LLMCallable | None = None,
        compute_mode: str = "deep",
        data_provider: DataProvider | None = None,
    ):
        self._llm = llm_func or call_llm
        # [P2-3] compute_mode='fast' 准备在 review() body 中 short-circuit Q5 trio。
        # 当前 plumbing-only：仅记录到 self._compute_mode；
        # 实际 fast-path behavior 留待 follow-up iteration。
        self._compute_mode = (compute_mode or "deep").strip().lower()
        self._logs: list[str] = []
        self._log_lock = threading.Lock()
        # v4.2 分层解耦：数据访问走 Provider，不再直接依赖 SessionLocal / crud / models
        self.data_provider: DataProvider = data_provider or get_db_data_provider()
        # 算力模式参数 —— 优先动态预设，回退静态 COMPUTE_MODES
        from .config import get_compute_mode_config

        mode_cfg = get_compute_mode_config()
        self._temperature = mode_cfg.get("temperature", 0.0)
        # Respect the compute mode's max_tokens configuration (e.g. speed mode's
        # lower token budget). The previous 4096 floor effectively disabled the
        # speed mode's token savings, making deep and speed nearly identical.
        self._max_tokens = int(mode_cfg.get("max_tokens", 512))
        self._parallel = mode_cfg.get("parallel", True)

        # P0-2: 加载校准集
        self._calibration_set: list[CalibrationSample] = load_calibration_set(CALIBRATION_SET_PATH)
        self._calibration_result: CalibrationResult = CalibrationResult()
        if self._calibration_set:
            self._log(f"[校准集] 已加载 {len(self._calibration_set)} 条样本")

        # P2-1: 热点词源
        self._hotspots_source = HOTSPOTS_SOURCE

        # P1-1: 节点分数标准差（自洽采样产出）
        self._node_stds: dict[str, dict[str, float]] = {}

        # v4.2: PaperFigure 图表证据缓存（paper_id → items），避免 QE/QF 重复查库
        self._figure_cache: dict[str, list[dict[str, Any]]] = {}

    def _log(self, msg: str) -> None:
        timestamp = datetime.now().isoformat()
        entry = f"[{timestamp}] {msg}"
        with self._log_lock:
            self._logs.append(entry)
        logger.info(msg)

    def _invoke_llm(self, prompt: str, *, node_name: str | None = None, **kw: Any) -> str | None:
        """调用注入的 LLM，兼容不接受关键字参数的测试桩（MockLLM）。

        真实 call_llm 接受 max_tokens/temperature/grammar 等 kwarg；
        测试中注入的可调用对象可能仅接受 prompt，此时降级为仅传 prompt。
        None 值 kwarg 先剔除，避免无谓透传。

        v4.2: 返回 str | None — None 表示 LLM 调用失败，调用方必须显式处理。

        当 node_name 提供时，在 prompt 顶部注入显式节点标记 ``<!-- NODE:{node_name} -->``，
        便于测试桩（MockLLM）按节点精确路由，避免依赖 prompt 子串的脆弱匹配。
        该标记为 HTML/XML 注释，对 LLM 不可见，不污染任务内容。
        """
        kw = {k: v for k, v in kw.items() if v is not None}
        if node_name:
            prompt = f"<!-- NODE:{node_name} -->\n{prompt}"
        try:
            return self._llm(prompt, **kw)
        except TypeError:
            return self._llm(prompt)

    # ── v4.3 NodeOutput helpers ────────────────────────────────────
    def _wrap_failed(self, node_id: str, error: str) -> NodeOutput:
        """Return a failed NodeOutput, triggering DAG-level error handling."""
        return NodeOutput(node_id=node_id, status="failed", error=error)

    def _wrap_success(self, node_id: str, data: Any) -> NodeOutput:
        """Return a successful NodeOutput with typed payload."""
        return NodeOutput(node_id=node_id, status="success", data=data)

    def _unwrap_or_raise(self, no: NodeOutput, label: str) -> Any:
        """Unwrap a NodeOutput, raising ValueError if status != success."""
        if no.status != "success":
            raise ValueError(f"{label} failed: {no.error}")
        return no.data

    # ------------------------------------------------------------------
    # P1-1: 自洽采样（多次 LLM 调用，分数字段取中位数 + 标准差）
    # ------------------------------------------------------------------
    def _sample_node(
        self,
        node_name: str,
        prompt: str,
        parse_fn: Callable[[str], tuple[dict[str, Any], dict[str, float]]],
        grammar: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, float], str]:
        """对同一 prompt 多次采样，返回代表结果字典、各分数字段标准差、代表原始输出。

        当 CONSENSUS_ENABLED=False 或采样数不足时，退化为单次调用，std 全 0。
        代表结果选取欧氏距离最接近中位数的样本，并将其分数字段替换为中位数。

        v4.2: LLM 返回 None 时返回空结果（{}, {}, ""），调用方据此判断节点失败。

        grammar: 可选 GBNF 语法约束（仅 llama.cpp/llama-server 且配置启用时生效）。
        """
        self._node_stds.pop(node_name, None)

        if not CONSENSUS_ENABLED or CONSENSUS_SAMPLES <= 1:
            raw = self._invoke_llm(
                prompt,
                node_name=node_name,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                grammar=grammar,
            )
            if raw is None:
                self._log(f"[{node_name}] LLM 调用失败（返回 None），节点标记为失败")
                self._node_stds[node_name] = {}
                return {}, {}, ""
            result, scores = parse_fn(raw)
            self._node_stds[node_name] = dict.fromkeys(scores, 0.0)
            return result, self._node_stds[node_name], raw

        samples: list[tuple[dict[str, Any], dict[str, float], str]] = []
        sample_temp = (
            CONSENSUS_TEMPERATURE if CONSENSUS_TEMPERATURE is not None else self._temperature
        )
        for i in range(CONSENSUS_SAMPLES):
            raw = self._invoke_llm(
                prompt,
                node_name=node_name,
                max_tokens=self._max_tokens,
                temperature=sample_temp,
                grammar=grammar,
            )
            if raw is None:
                self._log(f"[{node_name}] 采样 #{i + 1} LLM 返回 None，跳过")
                continue
            try:
                result, scores = parse_fn(raw)
                if scores:
                    samples.append((result, scores, raw))
            except Exception as e:
                self._log(f"[{node_name}] 采样 #{i + 1} 解析失败: {e}")
                continue

        if not samples:
            self._log(f"[{node_name}] 自洽采样全部失败，返回空结果")
            self._node_stds[node_name] = {}
            return {}, {}, ""

        if len(samples) == 1:
            result, scores, raw = samples[0]
            self._node_stds[node_name] = dict.fromkeys(scores, 0.0)
            return result, self._node_stds[node_name], raw

        score_keys = list(samples[0][1].keys())
        median_scores: dict[str, float] = {}
        std_scores: dict[str, float] = {}
        for k in score_keys:
            vals = [s[1][k] for s in samples if k in s[1]]
            if vals:
                median_scores[k] = round(float(median(vals)), 4)
                std_scores[k] = round(float(stdev(vals)) if len(vals) > 1 else 0.0, 4)

        # 选最接近中位数的样本作为代表
        def _dist(item: tuple[dict[str, Any], dict[str, float], str]) -> float:
            return sum((item[1].get(k, 0.5) - median_scores.get(k, 0.5)) ** 2 for k in score_keys)

        representative_item = min(samples, key=_dist)
        representative = dict(representative_item[0])
        representative.update(median_scores)

        self._node_stds[node_name] = std_scores
        self._log(
            f"[{node_name}] 自洽采样 n={len(samples)}, median={median_scores}, std={std_scores}"
        )
        return representative, std_scores, representative_item[2]

    # ------------------------------------------------------------------
    # 证据 ID 正则兜底
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_evidence_id_regex(raw_output: str) -> str:
        """从原始 LLM 输出中用正则提取 evidence_id。

        覆盖格式：\"evidence_id\": \"E3\", \"evidence_id\":\"E3\", evidence_id: E3
        """
        if not raw_output:
            return ""
        patterns = [
            r'"evidence_id"\s*:\s*"(E\d+)"',
            r'evidence_id"?\s*:\s*"(E\d+)"',
            r'evidence_id["\s:]+(E\d+)',
        ]
        for pat in patterns:
            m = re.search(pat, raw_output, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return ""

    # ------------------------------------------------------------------
    # 三层证据 ID 校验
    # ------------------------------------------------------------------
    def _validate_evidence_id(
        self,
        evidence_id: str,
        evidence_pool: dict[str, str],
        node_name: str,
        raw_llm_output: str = "",
        retry_prompt_fn: Callable[[], str | None] | None = None,
    ) -> tuple[bool, dict[str, Any]]:
        """三层兜底校验 evidence_id。

        1. 直接匹配 pool 键
        2. 正则从原始输出提取
        3. 关键词提取 (E\\d+)
        4. 重试 1 次
        """
        if not evidence_id or not evidence_pool:
            return (evidence_id in evidence_pool) if evidence_id else False, {}

        if evidence_id in evidence_pool:
            self._log(f"[{node_name}] 证据 ID '{evidence_id}' 校验通过")
            return True, {}

        # 层 2：正则兜底
        if raw_llm_output:
            regex_eid = self._extract_evidence_id_regex(raw_llm_output)
            if regex_eid and regex_eid in evidence_pool:
                self._log(f"[{node_name}] 正则兜底成功：'{regex_eid}'")
                return True, {"evidence_id": regex_eid}

        # 层 3：关键词提取
        kw_match = re.search(r"(E\d+)", evidence_id)
        if kw_match:
            extracted = kw_match.group(1)
            if extracted in evidence_pool:
                self._log(f"[{node_name}] 关键词提取兜底：'{evidence_id}' → '{extracted}'")
                return True, {"evidence_id": extracted}

        # 层 4：重试
        available_keys = list(evidence_pool.keys())[:5]
        self._log(
            f"[{node_name}] 证据 ID '{evidence_id}' 无效（可用: {available_keys}...），触发重试"
        )
        if retry_prompt_fn is None:
            self._log(f"[{node_name}] 无法重试，标记 verified=false")
            return False, {}

        retry_prompt = retry_prompt_fn()
        if retry_prompt is None:
            self._log(f"[{node_name}] 无法重试，标记 verified=false")
            return False, {}
        # v4.2 温度锁定：重试同样显式传 temperature
        raw = self._invoke_llm(
            retry_prompt,
            node_name=node_name,
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        if not raw:
            self._log(f"[{node_name}] 重试失败（LLM 返回空），标记 verified=false")
            return False, {}

        new_eid = self._extract_evidence_id_regex(raw)
        if new_eid and new_eid in evidence_pool:
            self._log(f"[{node_name}] 重试正则兜底成功：'{new_eid}'")
            return True, {"evidence_id": new_eid}

        self._log(f"[{node_name}] 重试仍失败，标记 verified=false")
        return False, {}

    # ------------------------------------------------------------------
    # 证据 ID 校验 + 重试 + 数据同步的通用封装
    # ------------------------------------------------------------------
    def _run_evidence_validation(
        self,
        evidence_id: str,
        evidence_pool: dict[str, str],
        node_name: str,
        raw_llm_output: str,
        prompt: str,
        valid_ids: list[str],
        data: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """校验 evidence_id，必要时重试，并同步 data 与 evidence_id。"""
        verified, retry_data = self._validate_evidence_id(
            evidence_id,
            evidence_pool,
            node_name,
            raw_llm_output=raw_llm_output,
            retry_prompt_fn=lambda: (
                (
                    f"{prompt}\n\n【重试提示】你输出的 evidence_id '{evidence_id}' 无效。"
                    f"可用证据池 ID：{valid_ids}。请从可用证据池中重新选择有效的 ID。"
                )
                if evidence_id
                else None
            ),
        )
        if retry_data:
            if data is not None:
                data.update(retry_data)
            if verified:
                evidence_id = str(retry_data.get("evidence_id", evidence_id)).strip()
        return verified, evidence_id

    # ------------------------------------------------------------------
    # Q0: 整体印象
    # ------------------------------------------------------------------
    def _run_q0(self, ctx: PaperContext) -> NodeOutput[Q0Result]:
        text = ctx.paper_abstract_intro
        prompt = PROMPT_Q0.format(paper=text[:MAX_CHARS_SHORT])
        self._log(f"[Q0] 开始调用 LLM（纯文本模式, max_tokens={self._max_tokens}）...")
        grammar = q0_grammar() if _depth_grammar_enabled() else None
        # v4.2 DI 统一：走注入的 self._llm（测试可全 mock；生产默认即 call_llm）
        raw = self._invoke_llm(
            prompt,
            node_name="Q0",
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            grammar=grammar,
        )
        if raw is None:
            self._log("[Q0] LLM 调用失败（返回 None），返回默认结果")
            return self._wrap_failed("Q0", "LLM returned None")

        data = _extract_llm_fields(
            raw,
            {
                "reasoning": r"(?i)-?\s*reasoning\s*[:：]\s*(.+)",
                "evidence": r"(?i)-?\s*evidence\s*[:：]\s*(.+)",
                "has_substance": r"(?i)-?\s*has_substance\s*[:：]\s*(true|false)",
                "expectation": r"(?i)-?\s*expectation\s*[:：]\s*([0-9.]+)",
            },
        )

        # 诊断日志
        if "has_substance" not in data:
            self._log(f"[Q0] 解析警告：has_substance 缺失，raw[:300]={raw[:300]!r}")

        return self._wrap_success(
            "Q0",
            Q0Result(
                reasoning=str(data.get("reasoning", "")),
                evidence=str(data.get("evidence", "")),
                has_substance=data.get("has_substance", "true").lower() == "true",
                expectation=_clamp_float(data.get("expectation", 0.5)),
            ),
        )

    # ------------------------------------------------------------------
    # Q1: 类型判别
    # ------------------------------------------------------------------
    def _run_q1(self, ctx: PaperContext) -> NodeOutput[Q1Result]:
        text = ctx.paper_abstract_intro
        prompt = PROMPT_Q1.format(paper=text[:MAX_CHARS_SHORT])
        self._log(f"[Q1] 开始调用 LLM（纯文本模式, max_tokens={self._max_tokens}）...")
        grammar = q1_grammar() if _depth_grammar_enabled() else None
        # v4.2 DI 统一：走注入的 self._llm
        raw = self._invoke_llm(
            prompt,
            node_name="Q1",
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            grammar=grammar,
        )
        if raw is None:
            self._log("[Q1] LLM 调用失败（返回 None），返回默认结果")
            return self._wrap_failed("Q1", "LLM returned None")

        data = _extract_llm_fields(
            raw,
            {
                "reasoning": r"(?i)-?\s*reasoning\s*[:：]\s*(.+)",
                "evidence": r"(?i)-?\s*evidence\s*[:：]\s*(.+)",
                "type": r"(?i)-?\s*type\s*[:：]\s*([ABCDabcd])",
                "secondary_type": r"(?i)-?\s*secondary_type\s*[:：]\s*([ABCDabcd]|none)",
                "confidence": r"(?i)-?\s*confidence\s*[:：]\s*([0-9.]+)",
            },
            none_to_empty=False,
        )

        # type 字段有效性过滤
        if data.get("type", "").upper() not in ("A", "B", "C", "D"):
            data.pop("type", None)

        # 诊断日志
        if "type" not in data:
            self._log(f"[Q1] 解析警告：type 缺失，raw[:300]={raw[:300]!r}")

        ptype = str(data.get("type", "B")).upper()
        stype = str(data.get("secondary_type", "none")).lower()
        if stype not in ("a", "b", "c", "d", "none"):
            stype = "none"
        elif stype != "none":
            stype = stype.upper()  # 单字母大写以满足 Q1Result 的 pattern 验证

        return self._wrap_success(
            "Q1",
            Q1Result(
                reasoning=str(data.get("reasoning", "")),
                evidence=str(data.get("evidence", "")),
                type=ptype,
                secondary_type=stype,
                confidence=_clamp_float(data.get("confidence", 0.5)),
            ),
        )

    # ------------------------------------------------------------------
    # QE: 全局证据池（纯文本解析，避开推理模型 JSON 输出限制）
    # ------------------------------------------------------------------
    def _run_qe(self, ctx: PaperContext) -> NodeOutput[tuple[QEResult, str]]:
        text = ctx.paper_full_text
        prompt = PROMPT_QE.format(paper=text[:MAX_CHARS_FULL])
        self._log("[QE] 开始调用 LLM（纯文本模式）...")
        # v4.2 温度锁定：显式传 temperature（此前裸调 self._llm(prompt)，
        # 依赖下游默认值，复跑一致性无保障）
        raw = self._invoke_llm(
            prompt,
            node_name="QE",
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        if raw is None or not raw:
            self._log("[QE] LLM 返回空，使用默认值")
            return self._wrap_failed("QE", "LLM returned empty")

        # 解析纯文本证据行：匹配 "E1: content" / "E1：内容" 等格式
        items: list[EvidenceItem] = []
        seen_ids: set[str] = set()
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue
            # 匹配可选破折号前缀 + E数字 + 中英文冒号 + 内容（可选 [关键词: ...] 后缀）
            m = re.search(r"-?\s*(E\d+)\s*[:：]\s*(.+)", line)
            if m:
                eid = m.group(1)
                content = m.group(2).strip()
                # 提取可选关键词后缀，例如 "... [关键词: 端到端, 时态注意力, GCN]"
                keywords: list[str] = []
                kw_match = re.search(r"\s*\[关键词[:：]\s*([^\]]+)\]$", content)
                if kw_match:
                    keywords = [k.strip() for k in kw_match.group(1).split(",") if k.strip()]
                    content = content[: kw_match.start()].strip()
                # 过滤掉太短或明显不是证据的行（如 "E1: 1." 或 "E1: -"）
                if len(content) < 10:
                    continue
                if eid not in seen_ids and content:
                    seen_ids.add(eid)
                    items.append(EvidenceItem(id=eid, content=content, keywords=keywords))

        if len(items) == 0:
            self._log(f"[QE] 致命警告：未提取到任何证据！原始输出前300字符: {raw[:300]}")
        elif len(items) < 3:
            self._log(f"[QE] 警告：仅提取 {len(items)} 条证据，预期 5-7 条")
        else:
            self._log(f"[QE] 成功提取 {len(items)} 条证据")
        return self._wrap_success(
            "QE",
            (
                QEResult(reasoning=f"Extracted {len(items)} evidence items.", evidence_pool=items),
                raw,
            ),
        )

    # ------------------------------------------------------------------
    # Q2: 创新与热点（纯文本解析 + 大 token 预算，避开推理模型思考耗尽）
    # ------------------------------------------------------------------
    def _run_q2(self, ctx: PaperContext, results: DAGOutputs) -> NodeOutput[Q2Result]:
        text = ctx.paper_full_text
        q1_type = results.Q1.type if results.Q1 else "B"
        evidence_pool = results.QE.ev_pool if results.QE else {}
        hotspots = ctx.hotspots
        hotspots_str = "、".join(hotspots) if hotspots else "、".join(DEFAULT_HOTSPOTS)
        evidence_text = _format_evidence_pool_text(evidence_pool)
        valid_ids = list(evidence_pool.keys())

        prompt = PROMPT_Q2.format(
            paper=text[:MAX_CHARS_SHORT],
            paper_type=q1_type,
            hotspots=hotspots_str,
            evidence_pool_text=evidence_text,
        )
        self._log(f"[Q2] 开始调用 LLM（纯文本模式, max_tokens={self._max_tokens}）...")

        def _parse_q2(raw: str) -> tuple[dict[str, Any], dict[str, float]]:
            data = _extract_llm_fields(
                raw,
                {
                    "reasoning": r"(?i)-?\s*reasoning\s*[:：]\s*(.+)",
                    "core_contribution": r"(?i)-?\s*core_contribution\s*[:：]\s*(.+)",
                    "novelty_score": r"(?i)-?\s*novelty_score\s*[:：]\s*([0-9.]+)",
                    "hotspot_alignment_score": (
                        r"(?i)-?\s*hotspot_alignment_score\s*[:：]\s*([0-9.]+)"
                    ),
                    "evidence_id": r"(?i)-?\s*evidence_id\s*[:：]\s*(E\d+|none)",
                },
            )
            if "novelty_score" not in data or "hotspot_alignment_score" not in data:
                self._log(f"[Q2] 解析警告：关键字段缺失，raw[:300]={raw[:300]!r}")
            scores = {
                "novelty_score": _clamp_float(data.get("novelty_score", 0.5)),
                "hotspot_alignment_score": _clamp_float(data.get("hotspot_alignment_score", 0.5)),
            }
            return data, scores

        grammar = q2_grammar(valid_ids) if _depth_grammar_enabled() else None
        data, _score_stds, raw = self._sample_node("Q2", prompt, _parse_q2, grammar=grammar)

        evidence_id = str(data.get("evidence_id", "")).strip()
        verified, evidence_id = self._run_evidence_validation(
            evidence_id,
            evidence_pool,
            "Q2",
            raw,
            prompt,
            valid_ids,
            data=data,
        )

        return self._wrap_success(
            "Q2",
            Q2Result(
                reasoning=str(data.get("reasoning", "")),
                core_contribution=str(data.get("core_contribution", "")),
                novelty_score=_clamp_float(data.get("novelty_score", 0.5)),
                hotspot_alignment_score=_clamp_float(data.get("hotspot_alignment_score", 0.5)),
                evidence_id=evidence_id,
                verified=verified,
            ),
        )

    # ------------------------------------------------------------------
    # Q3: 类型自适应严谨性审查（纯文本 + 大 token 预算）
    # ------------------------------------------------------------------
    def _run_q3(self, ctx: PaperContext, results: DAGOutputs) -> NodeOutput[Q3Result]:
        text = ctx.paper_full_text
        q1_type = results.Q1.type if results.Q1 else "B"
        q1_secondary_type = results.Q1.secondary_type if results.Q1 else "none"
        evidence_pool = results.QE.ev_pool if results.QE else {}
        prompt_template = Q3_PROMPT_VARIANTS.get(q1_type, Q3_PROMPT_VARIANTS["B"])
        secondary_info = ""
        secondary_checklist = ""
        if q1_secondary_type != "none" and q1_secondary_type in Q3_SECONDARY_CHECKLIST:
            secondary_info = f" + {q1_secondary_type}类特征"
            secondary_checklist = Q3_SECONDARY_CHECKLIST[q1_secondary_type]

        evidence_text = _format_evidence_pool_text(evidence_pool)
        valid_ids = list(evidence_pool.keys())

        prompt = prompt_template.format(
            paper=text[:MAX_CHARS_SHORT],
            paper_type=q1_type,
            secondary_type_info=secondary_info,
            secondary_checklist=secondary_checklist,
            evidence_pool_text=evidence_text,
        )
        self._log(f"[Q3] 开始调用 LLM（纯文本模式, max_tokens={self._max_tokens}）...")

        def _parse_q3(raw: str) -> tuple[dict[str, Any], dict[str, float]]:
            data = _extract_llm_fields(
                raw,
                {
                    "reasoning": r"(?i)-?\s*reasoning\s*[:：]\s*(.+)",
                    "rigor_score": r"(?i)-?\s*rigor_score\s*[:：]\s*([0-9.]+)",
                    "missing_items": r"(?i)-?\s*missing_items\s*[:：]\s*(.+)",
                    "evidence_id": r"(?i)-?\s*evidence_id\s*[:：]\s*(E\d+|none)",
                },
            )
            if "rigor_score" not in data:
                self._log(f"[Q3] 解析警告：rigor_score 缺失，raw[:300]={raw[:300]!r}")
            scores = {"rigor_score": _clamp_float(data.get("rigor_score", 0.5))}
            return data, scores

        grammar = q3_grammar(valid_ids) if _depth_grammar_enabled() else None
        data, _score_stds, raw = self._sample_node("Q3", prompt, _parse_q3, grammar=grammar)

        evidence_id = str(data.get("evidence_id", "")).strip()
        verified, evidence_id = self._run_evidence_validation(
            evidence_id,
            evidence_pool,
            "Q3",
            raw,
            prompt,
            valid_ids,
            data=data,
        )

        # missing_items: 逗号分隔字符串 → 列表
        missing_raw = str(data.get("missing_items", "")).strip()
        if missing_raw.lower() == "none" or not missing_raw:
            missing_items: list[str] = []
        else:
            missing_items = [m.strip() for m in missing_raw.split(",") if m.strip()]
            missing_items = missing_items[:5]  # 最多5项

        # ── 硬证据检测：消融实验 → 上调严谨性评分 0.05-0.10 ──
        rigor_score = _clamp_float(data.get("rigor_score", 0.5))
        ablation_found, ablation_matches = _detect_ablation_evidence(text)
        if ablation_found:
            boost = min(0.10, 0.05 + 0.01 * min(len(ablation_matches) - 2, 5))
            old_score = rigor_score
            rigor_score = min(1.0, rigor_score + boost)
            logger.info(
                "[Q3] 消融实验证据检测到 %d 个模式 (%s)，严谨分 %.3f → %.3f (+%.3f)",
                len(ablation_matches),
                ", ".join(ablation_matches[:3]),
                old_score,
                rigor_score,
                boost,
            )
            # 从 missing_items 中移除"消融实验"相关项
            missing_items = [
                m
                for m in missing_items
                if not any(kw in m.lower() for kw in ("消融", "ablation", "消蚀"))
            ]

        return self._wrap_success(
            "Q3",
            Q3Result(
                reasoning=str(data.get("reasoning", "")),
                rigor_score=round(rigor_score, 4),
                missing_items=missing_items,
                evidence_id=evidence_id,
                verified=verified,
            ),
        )

    # ------------------------------------------------------------------
    # Q4: 影响力与可复现性（纯文本 + 大 token 预算）
    # ------------------------------------------------------------------
    def _run_q4(self, ctx: PaperContext, results: DAGOutputs) -> NodeOutput[Q4Result]:
        text = ctx.paper_full_text
        evidence_pool = results.QE.ev_pool if results.QE else {}
        evidence_text = _format_evidence_pool_text(evidence_pool)
        valid_ids = list(evidence_pool.keys())

        prompt = PROMPT_Q4.format(paper=text[:MAX_CHARS_SHORT], evidence_pool_text=evidence_text)
        self._log(f"[Q4] 开始调用 LLM（纯文本模式, max_tokens={self._max_tokens}）...")

        def _parse_q4(raw: str) -> tuple[dict[str, Any], dict[str, float]]:
            data = _extract_llm_fields(
                raw,
                {
                    "reasoning": r"(?i)-?\s*reasoning\s*[:：]\s*(.+)",
                    "influence_score": r"(?i)-?\s*influence_score\s*[:：]\s*([0-9.]+)",
                    "reproducibility_score": (
                        r"(?i)-?\s*repro(?:ducibility)?_?score\s*[:：]\s*([0-9.]+)"
                    ),
                    "evidence_id": r"(?i)-?\s*evidence_id\s*[:：]\s*(E\d+|none)",
                },
            )
            missing_keys = [
                k for k in ("influence_score", "reproducibility_score") if k not in data
            ]
            if missing_keys:
                self._log(f"[Q4] 解析警告：{missing_keys} 缺失，raw[:300]={raw[:300]!r}")
            scores = {
                "influence_score": _clamp_float(data.get("influence_score", 0.5)),
                "reproducibility_score": _clamp_float(data.get("reproducibility_score", 0.5)),
            }
            return data, scores

        grammar = q4_grammar(valid_ids) if _depth_grammar_enabled() else None
        data, _score_stds, raw = self._sample_node("Q4", prompt, _parse_q4, grammar=grammar)

        evidence_id = str(data.get("evidence_id", "")).strip()
        verified, evidence_id = self._run_evidence_validation(
            evidence_id,
            evidence_pool,
            "Q4",
            raw,
            prompt,
            valid_ids,
            data=data,
        )

        repro_score = _clamp_float(data.get("reproducibility_score", 0.5))
        repro_score, _repro_capped = _cap_reproducibility(repro_score, text)
        if _repro_capped:
            self._log("[Q4] 可复现性封顶：正文无代码/数据/权重发布信号，repro 降至 0.6")
        return self._wrap_success(
            "Q4",
            Q4Result(
                reasoning=str(data.get("reasoning", "")),
                influence_score=_clamp_float(data.get("influence_score", 0.5)),
                reproducibility_score=round(repro_score, 4),
                evidence_id=evidence_id,
                verified=verified,
            ),
        )

    # ------------------------------------------------------------------
    # Q234: 合并多维评分（v4.2 降本核心 —— 单次 LLM 调用替代 Q2/Q3/Q4 三次）
    # ------------------------------------------------------------------
    def _validate_q234_dim_eid(
        self,
        dim: str,
        evidence_id: str,
        raw: str,
        evidence_pool: dict[str, str],
    ) -> tuple[bool, str]:
        """Q234 合并输出的逐维度 evidence_id 校验（维度定向）。

        与通用 _validate_evidence_id 的差异：合并输出含 q2/q3/q4 三个
        evidence_id 字段，通用「正则兜底」会命中**第一个**出现的字段（可能
        属于其他维度），造成跨维度串号误判。这里所有匹配都锚定本维度字段名：
        直接匹配 → 维度定向正则 → 维度定向重试。
        """
        node_name = f"Q234-{dim.upper()}"
        # 1. 直接匹配
        if evidence_id and evidence_id in evidence_pool:
            return True, evidence_id
        # 2. 维度定向正则兜底（只匹配 q{dim}_evidence_id 字段）
        m = re.search(rf"(?i){dim}_evidence_id\s*[:：]\s*(E\d+)", raw)
        if m and m.group(1) in evidence_pool:
            self._log(f"[{node_name}] 正则兜底命中 {m.group(1)}")
            return True, m.group(1)
        # 3. 维度定向重试（只问本维度的一行输出）
        valid_ids = list(evidence_pool.keys())
        retry_prompt = (
            f"你此前输出的 {dim}_evidence_id '{evidence_id}' 无效。"
            f"可用证据池 ID：{', '.join(valid_ids)}。\n"
            f"【重试提示】请仅输出一行：{dim}_evidence_id: <有效ID 或 none>"
        )
        retry_raw = (
            self._invoke_llm(
                retry_prompt,
                node_name=node_name,
                max_tokens=self._max_tokens,
                temperature=self._temperature,
            )
            or ""
        )
        m2 = re.search(rf"(?i){dim}_evidence_id\s*[:：]\s*(E\d+)", retry_raw) or re.search(
            r"(?i)evidence_id[\"'\s:]+(E\d+)", retry_raw
        )
        if m2 and m2.group(1) in evidence_pool:
            self._log(f"[{node_name}] 重试成功 → {m2.group(1)}")
            return True, m2.group(1)
        if evidence_id:
            self._log(f"[{node_name}] evidence_id='{evidence_id}' 校验失败，标记 verified=false")
        return False, evidence_id

    def _run_q234(
        self, ctx: PaperContext, results: DAGOutputs
    ) -> NodeOutput[tuple[Q2Result, Q3Result, Q4Result]]:
        text = ctx.paper_full_text
        q1 = results.Q1
        evidence_pool = results.QE.ev_pool if results.QE else {}
        hotspots = ctx.hotspots
        """单次调用产出 novelty/hotspot/rigor/influence/reproducibility 五维分数。

        设计要点：
        - 与 Q2/Q3/Q4 共用同一 evidence_pool，输出逐维度 evidence_id（q2/q3/q4 各一），
          每层仍走既有的三层证据校验。
        - 按设计不做共识采样（std 全 0）：维度分的 std 仅用于展示，不为它多付 2 次调用。
        - LLM 硬失败（返回空）时回退 legacy 三次独立调用，保证可用性。
        - Q3 的消融实验硬证据上调逻辑原样保留。
        """
        hotspots_str = "、".join(hotspots) if hotspots else "、".join(DEFAULT_HOTSPOTS)
        evidence_text = _format_evidence_pool_text(evidence_pool)
        valid_ids = list(evidence_pool.keys())
        secondary_info = ""
        secondary_checklist = ""
        if q1.secondary_type != "none" and q1.secondary_type in Q3_SECONDARY_CHECKLIST:
            secondary_info = f" + {q1.secondary_type}类特征"
            secondary_checklist = Q3_SECONDARY_CHECKLIST[q1.secondary_type]
        rigor_guide = Q234_RIGOR_GUIDE.get(q1.type, Q234_RIGOR_GUIDE["B"])

        prompt = PROMPT_Q234.format(
            paper=text[:MAX_CHARS_SHORT],
            paper_type=q1.type,
            secondary_type_info=secondary_info,
            rigor_guide=rigor_guide,
            hotspots=hotspots_str,
            evidence_pool_text=evidence_text,
            secondary_checklist=secondary_checklist,
        )
        self._log(f"[Q234] 开始调用 LLM（合并多维评分, max_tokens={self._max_tokens}）...")
        grammar = q234_grammar(valid_ids) if _depth_grammar_enabled() else None
        raw = self._invoke_llm(
            prompt,
            node_name="Q234",
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            grammar=grammar,
        )
        if raw is None:
            self._log("[Q234] LLM 调用失败（返回 None），回退三次独立调用")
            try:
                q2 = self._unwrap_or_raise(self._run_q2(ctx, results), "Q2")
                q3 = self._unwrap_or_raise(self._run_q3(ctx, results), "Q3")
                q4 = self._unwrap_or_raise(self._run_q4(ctx, results), "Q4")
            except ValueError:
                return self._wrap_failed("Q234", "Fallback Q2/Q3/Q4 also failed")
            return self._wrap_success("Q234", (q2, q3, q4))

        if not raw.strip():
            # 合并调用硬失败 → 回退 legacy 三次独立调用（保活性优先）
            self._log("[Q234] LLM 返回空，回退 Q2/Q3/Q4 三次独立调用")
            try:
                q2 = self._unwrap_or_raise(self._run_q2(ctx, results), "Q2")
                q3 = self._unwrap_or_raise(self._run_q3(ctx, results), "Q3")
                q4 = self._unwrap_or_raise(self._run_q4(ctx, results), "Q4")
            except ValueError:
                return self._wrap_failed("Q234", "Fallback Q2/Q3/Q4 returned empty")
            return self._wrap_success("Q234", (q2, q3, q4))

        data = _extract_llm_fields(
            raw,
            {
                "reasoning": r"(?i)-?\s*reasoning\s*[:：]\s*(.+)",
                "core_contribution": r"(?i)-?\s*core_contribution\s*[:：]\s*(.+)",
                "novelty_score": r"(?i)-?\s*novelty_score\s*[:：]\s*([0-9.]+)",
                "hotspot_alignment_score": (
                    r"(?i)-?\s*hotspot_alignment_score\s*[:：]\s*([0-9.]+)"
                ),
                "rigor_score": r"(?i)-?\s*rigor_score\s*[:：]\s*([0-9.]+)",
                "missing_items": r"(?i)-?\s*missing_items\s*[:：]\s*(.+)",
                "influence_score": r"(?i)-?\s*influence_score\s*[:：]\s*([0-9.]+)",
                "reproducibility_score": (
                    r"(?i)-?\s*repro(?:ducibility)?_?score\s*[:：]\s*([0-9.]+)"
                ),
                "q2_evidence_id": r"(?i)-?\s*q2_evidence_id\s*[:：]\s*(E\d+|none)",
                "q3_evidence_id": r"(?i)-?\s*q3_evidence_id\s*[:：]\s*(E\d+|none)",
                "q4_evidence_id": r"(?i)-?\s*q4_evidence_id\s*[:：]\s*(E\d+|none)",
            },
        )
        missing_keys = [
            k
            for k in ("novelty_score", "rigor_score", "influence_score", "reproducibility_score")
            if k not in data
        ]
        if missing_keys:
            self._log(f"[Q234] 解析警告：{missing_keys} 缺失，raw[:300]={raw[:300]!r}")

        # 合并节点按设计不做共识采样 → 各维度 std 全 0
        self._node_stds["Q234"] = dict.fromkeys(
            [
                "novelty_score",
                "hotspot_alignment_score",
                "rigor_score",
                "influence_score",
                "reproducibility_score",
            ],
            0.0,
        )

        # 逐维度证据 ID 校验（维度定向：防止正则兜底跨维度串号 —— 合并输出含
        # 多个 evidence_id 字段，通用兜底会抓到其他维度的 ID 造成误判 verified）
        dim_eids: dict[str, tuple[bool, str]] = {}
        for dim in ("q2", "q3", "q4"):
            dim_eids[dim] = self._validate_q234_dim_eid(
                dim, str(data.get(f"{dim}_evidence_id", "")).strip(), raw, evidence_pool
            )

        # missing_items: 逗号分隔字符串 → 列表（同 _run_q3）
        missing_raw = str(data.get("missing_items", "")).strip()
        if missing_raw.lower() == "none" or not missing_raw:
            missing_items: list[str] = []
        else:
            missing_items = [m.strip() for m in missing_raw.split(",") if m.strip()]
            missing_items = missing_items[:5]

        # ── 硬证据检测：消融实验 → 上调严谨性评分（与 _run_q3 同规则）──
        rigor_score = _clamp_float(data.get("rigor_score", 0.5))
        ablation_found, ablation_matches = _detect_ablation_evidence(text)
        if ablation_found:
            boost = min(0.10, 0.05 + 0.01 * min(len(ablation_matches) - 2, 5))
            old_score = rigor_score
            rigor_score = min(1.0, rigor_score + boost)
            logger.info(
                "[Q234] 消融实验证据检测到 %d 个模式，严谨分 %.3f → %.3f (+%.3f)",
                len(ablation_matches),
                old_score,
                rigor_score,
                boost,
            )
            missing_items = [
                m
                for m in missing_items
                if not any(kw in m.lower() for kw in ("消融", "ablation", "消蚀"))
            ]

        q2 = Q2Result(
            reasoning=str(data.get("reasoning", "")),
            core_contribution=str(data.get("core_contribution", "")),
            novelty_score=_clamp_float(data.get("novelty_score", 0.5)),
            hotspot_alignment_score=_clamp_float(data.get("hotspot_alignment_score", 0.5)),
            evidence_id=dim_eids["q2"][1],
            verified=dim_eids["q2"][0],
        )
        q3 = Q3Result(
            reasoning=str(data.get("reasoning", "")),
            rigor_score=round(rigor_score, 4),
            missing_items=missing_items,
            evidence_id=dim_eids["q3"][1],
            verified=dim_eids["q3"][0],
        )
        q4 = Q4Result(
            reasoning=str(data.get("reasoning", "")),
            influence_score=_clamp_float(data.get("influence_score", 0.5)),
            reproducibility_score=round(
                _cap_reproducibility(_clamp_float(data.get("reproducibility_score", 0.5)), text)[0],
                4,
            ),
            evidence_id=dim_eids["q4"][1],
            verified=dim_eids["q4"][0],
        )
        return self._wrap_success("Q234", (q2, q3, q4))

    # ------------------------------------------------------------------
    # QF: 图文一致性审查（v4.2 新增 —— 消费 PaperFigure 图表证据）
    # ------------------------------------------------------------------
    def _run_qf(
        self,
        ctx: PaperContext | None = None,
        results: DAGOutputs | None = None,
        *,
        text: str | None = None,
        paper_id: str | None = None,
        evidence_pool: dict[str, str] | None = None,
    ) -> NodeOutput[QFResult] | QFResult:
        """Run QF as a DAG node, retaining a read-only legacy direct seam."""
        legacy_call = ctx is None or results is None
        if legacy_call:
            ctx = PaperContext(
                paper_id=paper_id or "",
                title="",
                full_text=text or "",
                paper_abstract_conclusion=text or "",
            )
            results = DAGOutputs(QE=QEOutput(ev_pool=evidence_pool or {}))
        output = self._run_qf_impl(ctx, results, force_figure_load=legacy_call)
        return output.data if legacy_call and output.status == "success" else output

    def _run_qf_impl(
        self,
        ctx: PaperContext,
        results: DAGOutputs,
        *,
        force_figure_load: bool = False,
    ) -> NodeOutput[QFResult]:
        text = ctx.paper_abstract_conclusion
        paper_id = ctx.paper_id
        evidence_pool = results.QE.ev_pool if results.QE else {}
        """核对论文图表与正文的一致性，产出 figure_consistency_score ∈ [0,1]。

        有 PaperFigure 时调用 LLM 做图文一致性审查；
        无 PaperFigure 时直接返回中性 0.5（P0-B 文本层兜底路径已弃用）。
        """
        # figure_weight>0 但 evidence_enabled=false 时：QF 独立加载 figure
        # （测试配置：QF 评分但不并入 QE 池，避免污染 Q4/Q5c 辩论）
        if force_figure_load or (DEPTH_FIGURE_WEIGHT > 0 and not DEPTH_FIGURE_EVIDENCE_ENABLED):
            figures = self.data_provider.load_figure_evidence(paper_id)
        else:
            figures = self._load_figure_items(paper_id)
        if not figures:
            # [DEPRECATED] P0-B 文本层图文一致性路径已下线。
            # 早期实现会在无 PaperFigure 时走 regex 解析图注并调用
            # _score_text_figure_consistency，但 A/B 验证显示该信号无判别力
            # （PeerRead/盲评 w_fig=0 均最优，开门即 FP 飙升），
            # 且白白消耗 full_text regex IO。直接返回中性，保持门关。
            self._log("[QF] 无 PaperFigure，跳过已弃用的 P0-B 文本层一致性检查")
            self._node_stds["QF"] = {"figure_consistency_score": 0.0}
            return self._wrap_success(
                "QF", QFResult(reasoning="无图表数据，未执行图文一致性审查", has_figures=False)
            )

        figure_lines: list[str] = []
        for fig in figures[:MAX_FIGURE_EVIDENCE]:
            parts = [f"第{fig['page']}页图{fig['index']}"]
            if fig["caption"]:
                parts.append(f"Caption: {fig['caption']}")
            if fig["summary"]:
                parts.append(f"Qwen语义桥解读: {fig['summary']}")
            if fig["ocr"]:
                parts.append(f"OCR: {fig['ocr']}")
            axis_info_text = _format_axis_info(fig.get("axis_info"))
            if axis_info_text:
                parts.append(axis_info_text)
            figure_lines.append("- " + "；".join(parts))
        figure_text = "\n".join(figure_lines)
        evidence_text = _format_evidence_pool_text(evidence_pool)
        valid_ids = list(evidence_pool.keys())

        prompt = PROMPT_QF.format(
            paper=text[:MAX_CHARS_SHORT],
            figure_text=figure_text,
            evidence_pool_text=evidence_text,
        )
        self._log(f"[QF] 开始调用 LLM（图文一致性, {len(figures)} 张图）...")
        grammar = qf_grammar(valid_ids) if _depth_grammar_enabled() else None
        raw = self._invoke_llm(
            prompt,
            node_name="QF",
            max_tokens=self._max_tokens,
            temperature=self._temperature,
            grammar=grammar,
        )
        if raw is None:
            self._log("[QF] LLM 调用失败（返回 None），返回默认结果")
            return self._wrap_failed("QF", "LLM returned None")

        data = _extract_llm_fields(
            raw,
            {
                "reasoning": r"(?i)-?\s*reasoning\s*[:：]\s*(.+)",
                "figure_consistency_score": (
                    r"(?i)-?\s*figure_consistency_score\s*[:：]\s*([0-9.]+)"
                ),
                "inconsistency_flags": r"(?i)-?\s*inconsistency_flags\s*[:：]\s*(.+)",
                "evidence_id": r"(?i)-?\s*evidence_id\s*[:：]\s*(E\d+|none)",
            },
        )
        if "figure_consistency_score" not in data:
            self._log(f"[QF] 解析警告：figure_consistency_score 缺失，raw[:300]={raw[:300]!r}")

        flags_raw = str(data.get("inconsistency_flags", "")).strip()
        if flags_raw.lower() == "none" or not flags_raw:
            flags: list[str] = []
        else:
            flags = [f.strip() for f in flags_raw.split(",") if f.strip()][:5]

        evidence_id = str(data.get("evidence_id", "")).strip()
        verified, evidence_id = self._run_evidence_validation(
            evidence_id,
            evidence_pool,
            "QF",
            raw,
            prompt,
            valid_ids,
        )

        self._node_stds["QF"] = {"figure_consistency_score": 0.0}
        score = _clamp_float(data.get("figure_consistency_score", 0.5))

        # v4.2 #1: apply claim-validation penalty / bonus to figure_consistency_score
        penalty, penalty_flags, penalty_reasoning = self._compute_claim_validation_penalty(figures)
        bonus, bonus_reasoning = self._compute_claim_validation_bonus(figures)
        if penalty > 0 or bonus > 0:
            score = _clamp_float(score - penalty + bonus)
            flags.extend(penalty_flags)
            self._log(
                f"[QF] claim-validation penalty={penalty:.3f}, bonus={bonus:.3f}, "
                f"adjusted_score={score:.3f}"
            )

        self._log(
            f"[QF] figure_consistency_score={score:.3f}, claim_validation_penalty={penalty:.3f}, "
            f"claim_validation_bonus={bonus:.3f}, flags={len(flags)}, eid={evidence_id}"
        )
        reasoning = str(data.get("reasoning", ""))
        combined_reasoning = " ".join(
            r for r in [penalty_reasoning, bonus_reasoning, reasoning] if r
        ).strip()
        return self._wrap_success(
            "QF",
            QFResult(
                reasoning=combined_reasoning,
                figure_consistency_score=score,
                inconsistency_flags=flags,
                has_figures=True,
                evidence_id=evidence_id,
                verified=verified,
                claim_validation_penalty=penalty,
                claim_validation_bonus=bonus,
            ),
        )

    # ------------------------------------------------------------------
    # Q5a: 质疑者（纯文本 + 大 token 预算）
    # ------------------------------------------------------------------
    def _run_q5a(
        self,
        ctx: PaperContext,
        results: DAGOutputs,
    ) -> NodeOutput[Q5aResult]:
        text = ctx.paper_full_text
        q1 = results.Q1
        # support both merged (Q234) and split (Q2/Q3/Q4) modes
        if results.Q234 is not None:
            q2, q3, q4 = results.Q234
        else:
            q2 = results.Q2
            q3 = results.Q3
            q4 = results.Q4
        evidence_pool = results.QE.ev_pool if results.QE else {}
        qf = results.QF
        evidence_text = _format_evidence_pool_text(evidence_pool)
        valid_ids = list(evidence_pool.keys())
        figure_consistency = qf.figure_consistency_score if qf is not None else 0.5

        prompt = PROMPT_Q5A.format(
            paper=text[:MAX_CHARS_SHORT],
            paper_type=q1.type,
            novelty=f"{q2.novelty_score:.2f}",
            hotspot=f"{q2.hotspot_alignment_score:.2f}",
            rigor=f"{q3.rigor_score:.2f}",
            influence=f"{q4.influence_score:.2f}",
            reproducibility=f"{q4.reproducibility_score:.2f}",
            figure_consistency=f"{figure_consistency:.2f}",
            evidence_pool_text=evidence_text,
        )
        self._log(f"[Q5a] 开始调用 LLM（纯文本模式, max_tokens={self._max_tokens}）...")
        # v4.2 DI 统一：走注入的 self._llm
        raw = self._invoke_llm(
            prompt,
            node_name="Q5a",
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        if raw is None:
            self._log("[Q5a] LLM 调用失败（返回 None），返回默认结果")
            return self._wrap_failed("Q5a", "LLM returned None")

        # 正则提取 evidence_id
        evidence_id = ""
        m = re.search(r"(?i)-?\s*evidence_id\s*[:：]\s*(E\d+|none)", raw)
        if m:
            evidence_id = m.group(1).strip()
            if evidence_id.lower() == "none":
                evidence_id = ""

        # 正则提取每条 critique: ... | severity: fatal/minor（同行 + 跨行兼容）
        critique_points: list[CritiquePoint] = []
        lines = raw.split("\n")
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            # 策略 1：同行格式 critique: text | severity: fatal/minor
            m = re.search(
                r"(?i)-?\s*critique\s*[:：]\s*(.+?)\s*\|\s*severity\s*[:：]\s*(fatal|minor)", line
            )
            if m:
                pt = m.group(1).strip()
                sev = m.group(2).strip().lower()
                if pt:
                    critique_points.append(CritiquePoint(point=pt, severity=sev))
                i += 1
                continue
            # 策略 2：跨行格式  critique: text  + 下一行 severity: fatal/minor
            m2 = re.search(r"(?i)-?\s*critique\s*[:：]\s*(.+)", line)
            if m2 and i + 1 < len(lines):
                pt = m2.group(1).strip()
                next_line = lines[i + 1].strip()
                m3 = re.search(r"(?i)-?\s*severity\s*[:：]\s*(fatal|minor)", next_line)
                if m3 and pt:
                    critique_points.append(
                        CritiquePoint(point=pt, severity=m3.group(1).strip().lower())
                    )
                    i += 2
                    continue
            i += 1

        # 诊断日志：未提取到质疑时输出 raw 前 300 字符
        if not critique_points:
            self._log(f"[Q5a] 解析警告：未提取到 critique 行，raw[:300]={raw[:300]!r}")

        verified, evidence_id = self._run_evidence_validation(
            evidence_id,
            evidence_pool,
            "Q5a",
            raw,
            prompt,
            valid_ids,
        )

        critique_points = self._inject_figure_flags(critique_points, qf)

        return self._wrap_success(
            "Q5a",
            Q5aResult(critique_points=critique_points, evidence_id=evidence_id, verified=verified),
        )

    # ------------------------------------------------------------------
    # Q5b: 辩护者（纯文本 + 大 token 预算）
    # ------------------------------------------------------------------
    def _run_q5b(self, ctx: PaperContext, results: DAGOutputs) -> NodeOutput[Q5bResult]:
        text = ctx.paper_full_text
        q5a_points = results.Q5a.critique_points if results.Q5a else []
        evidence_pool = results.QE.ev_pool if results.QE else {}
        evidence_text = _format_evidence_pool_text(evidence_pool)
        valid_ids = list(evidence_pool.keys())

        # 格式化为纯文本列表（每行一条质疑）
        critique_lines = []
        for i, cp in enumerate(q5a_points):
            critique_lines.append(f"{i + 1}. [{cp.severity}] {cp.point}")
        critique_str = "\n".join(critique_lines)

        prompt = PROMPT_Q5B.format(
            paper=text[:MAX_CHARS_SHORT],
            critique_points=critique_str,
            evidence_pool_text=evidence_text,
        )
        self._log(f"[Q5b] 开始调用 LLM（纯文本模式, max_tokens={self._max_tokens}）...")
        # v4.2 DI 统一：走注入的 self._llm
        raw = self._invoke_llm(
            prompt,
            node_name="Q5b",
            max_tokens=self._max_tokens,
            temperature=self._temperature,
        )
        if raw is None:
            self._log("[Q5b] LLM 调用失败（返回 None），返回默认结果")
            return self._wrap_failed("Q5b", "LLM returned None")

        # 正则提取 evidence_id
        evidence_id = ""
        m = re.search(r"(?i)-?\s*evidence_id\s*[:：]\s*(E\d+|none)", raw)
        if m:
            evidence_id = m.group(1).strip()
            if evidence_id.lower() == "none":
                evidence_id = ""

        # 正则提取每条 defense: ...
        defense_points: list[str] = []
        for line in raw.split("\n"):
            m = re.search(r"(?i)-?\s*defense\s*[:：]\s*(.+)", line)
            if m:
                dp = m.group(1).strip()
                if dp:
                    defense_points.append(dp)

        # 诊断日志：未提取到辩护时输出 raw 前 300 字符
        if not defense_points:
            self._log(f"[Q5b] 解析警告：未提取到 defense 行，raw[:300]={raw[:300]!r}")

        if len(defense_points) < len(q5a_points):
            self._log(
                f"[Q5b] LLM 仅返回 {len(defense_points)} 条辩护，"
                f"预期 {len(q5a_points)} 条，补齐默认答复"
            )
        while len(defense_points) < len(q5a_points):
            defense_points.append("原文暂未涉及，将在终稿补充")

        verified, evidence_id = self._run_evidence_validation(
            evidence_id,
            evidence_pool,
            "Q5b",
            raw,
            prompt,
            valid_ids,
        )

        return self._wrap_success(
            "Q5b",
            Q5bResult(defense_points=defense_points, evidence_id=evidence_id, verified=verified),
        )

    # ------------------------------------------------------------------
    # v4.2 #9: 自适应 delta 区间（按辩论结果放宽，硬上限 [-0.25, +0.25]）
    # ------------------------------------------------------------------
    def _adaptive_delta_bounds(
        self,
        balanced_cp: list[CritiquePoint],
        default_min: float,
        default_max: float,
    ) -> tuple[float, float]:
        """根据平衡后质疑的严重度分布，计算本次 Q5c 允许的 delta 区间。

        规则（DEPTH_DELTA_ADAPTIVE_ENABLED=False 时恒为 [default_min, default_max]）：
        - 存活 fatal 越多 → 向下修正空间越大（每个 fatal -0.06）；
        - 存活 minor 超过 1 条后每条再补 -0.02；
        - 无任何存活质疑（干净论文）→ 向上放宽到 +0.20；
        - 全局硬上限 [-0.25, +0.25] 永不被突破。
        """
        if not DEPTH_DELTA_ADAPTIVE_ENABLED:
            return default_min, default_max
        fatal_n = sum(1 for cp in balanced_cp if cp.severity == "fatal")
        minor_n = sum(1 for cp in balanced_cp if cp.severity == "minor")
        lo = default_min - min(0.17, 0.06 * fatal_n + 0.02 * max(0, minor_n - 1))
        hi = default_max + (0.08 if fatal_n == 0 and minor_n == 0 else 0.0)
        return max(DELTA_HARD_MIN, lo), min(DELTA_HARD_MAX, hi)

    # ------------------------------------------------------------------
    # Q5c: 主席裁决（含平衡者逻辑——辩护成功则降级质疑严重程度）
    # ------------------------------------------------------------------
    def _run_q5c(
        self,
        ctx: PaperContext,
        results: DAGOutputs,
        base_score: float,
    ) -> NodeOutput[tuple[Q5cResult, list[CritiquePoint]]]:
        text = ctx.paper_full_text
        # extract upstream results (support both merged Q234 and split Q2/Q3/Q4)
        if results.Q234 is not None:
            q2, q3, q4 = results.Q234
        else:
            q2 = results.Q2
            q3 = results.Q3
            q4 = results.Q4
        q5a = results.Q5a
        q5b = results.Q5b
        qf = results.QF
        claim_severity_penalty = results.QE.claim_severity_penalty if results.QE else 0.0
        paper_type = results.Q1.type if results.Q1 else "B"
        # ── 平衡者：逐条比对辩护是否成功反驳质疑 ──
        balanced_cp: list[CritiquePoint] = []
        balancer_log_parts: list[str] = []
        for i, cp in enumerate(q5a.critique_points):
            defense = q5b.defense_points[i] if i < len(q5b.defense_points) else ""
            rebutted = self._is_successful_rebuttal(defense, cp.point)
            if rebutted and cp.severity == "fatal":
                balanced_cp.append(CritiquePoint(point=cp.point, severity="minor"))
                balancer_log_parts.append(
                    f'  #{i + 1} [fatal→minor] 辩护成功: "{cp.point[:30]}..." → 降级'
                )
            elif rebutted and cp.severity == "minor":
                # minor 被成功反驳 → 保留但标记为已回应
                balanced_cp.append(CritiquePoint(point=cp.point, severity="minor"))
                balancer_log_parts.append(
                    f'  #{i + 1} [minor→minor] 辩护成功: "{cp.point[:30]}..." → 维持但已有效回应'
                )
            else:
                balanced_cp.append(cp)
                balancer_log_parts.append(
                    f'  #{i + 1} [{cp.severity}] 辩护不足: "{cp.point[:30]}..." → 维持原级'
                )
        balancer_log = "\n".join(balancer_log_parts)
        if balancer_log_parts:
            self._log(f"[Q5c] 平衡者分析:\n{balancer_log}")

        # 纯文本格式化质疑（使用降级后的 severity）和辩护
        critique_lines = []
        for i, cp in enumerate(balanced_cp):
            critique_lines.append(f"{i + 1}. [{cp.severity}] {cp.point}")
        critique_str = "\n".join(critique_lines) if critique_lines else "（无）"
        defense_str = "\n".join(f"{i + 1}. {dp}" for i, dp in enumerate(q5b.defense_points))

        # v4.2 #9：按辩论结果自适应 delta 区间（默认区间可随论文类型/算力模式覆盖）
        default_delta_min, default_delta_max = get_delta_bounds(paper_type, self._compute_mode)
        delta_lo, delta_hi = self._adaptive_delta_bounds(
            balanced_cp, default_delta_min, default_delta_max
        )
        if claim_severity_penalty:
            # 严重越界加剧负面 delta，轻微越界影响小；硬上限 DELTA_HARD_MIN
            claim_severity_factor = get_depth_q5c_claim_severity_factor()
            delta_lo = max(
                DELTA_HARD_MIN,
                delta_lo - claim_severity_penalty * claim_severity_factor,
            )
            self._log(
                f"[Q5c] 图文断言越界严重度加权: -{claim_severity_penalty:.2f}, "
                f"delta_lo 从 {delta_lo + claim_severity_penalty * claim_severity_factor:.2f} "
                f"调整至 {delta_lo:.2f}"
            )

        prompt = PROMPT_Q5C.format(
            paper_abstract_conclusion=text[:MAX_CHARS_SHORT],
            base_score=base_score,
            novelty=f"{q2.novelty_score:.2f}",
            hotspot=f"{q2.hotspot_alignment_score:.2f}",
            rigor=f"{q3.rigor_score:.2f}",
            influence=f"{q4.influence_score:.2f}",
            reproducibility=f"{q4.reproducibility_score:.2f}",
            figure_consistency=f"{(qf.figure_consistency_score if qf is not None else 0.5):.2f}",
            delta_min=f"{delta_lo:.2f}",
            delta_max=f"{delta_hi:.2f}",
            critique_points=critique_str,
            defense_points=defense_str,
        )
        self._log(
            f"[Q5c] 开始调用 LLM（纯文本模式, max_tokens={self._max_tokens}, "
            f"delta∈[{delta_lo:.2f}, {delta_hi:.2f}]）..."
        )

        def _parse_q5c(raw: str) -> tuple[dict[str, Any], dict[str, float]]:
            data: dict[str, Any] = _extract_llm_fields(
                raw,
                {
                    "reasoning": r"(?i)-?\s*reasoning\s*[:：]\s*(.+)",
                    "delta": r"(?i)-?\s*delta\s*[:：]\s*(-?[0-9.]+)",
                    "verdict": (
                        r"(?i)-?\s*verdict\s*[:：]\s*"
                        r"(accept|minor[\s_]revision|major[\s_]revision|reject)"
                    ),
                },
            )
            delta_missing = "delta" not in data
            if delta_missing:
                self._log(f"[Q5c] 解析警告：delta 缺失，使用默认值 0.0，raw[:300]={raw[:300]!r}")
            delta = _clamp_float(data.get("delta", 0.0), delta_lo, delta_hi)
            raw_calibrated = _clamp_float(base_score + delta, 0.0, 1.0)
            # 分数偏移校正层：施加全局偏移 + 顶刊封顶，消除 DEPTH 系统性偏高
            # （校准实证 +0.09~+0.12）。offset=0 时恒等返回，向后兼容。
            calibrated_score = correct_final_score(
                raw_calibrated, paper=getattr(self, "_paper_meta", None)
            )
            llm_verdict = data.get("verdict", "major_revision").lower().replace(" ", "_")
            VALID_VERDICTS = {"accept", "minor_revision", "major_revision", "reject"}
            if llm_verdict not in VALID_VERDICTS:
                self._log(f"[Q5c] 解析警告：verdict='{llm_verdict}' 无效，使用 major_revision")
                llm_verdict = "major_revision"
            data["delta"] = delta
            data["calibrated_score"] = calibrated_score
            data["calibrated_score_raw"] = raw_calibrated
            data["llm_verdict"] = llm_verdict
            data["delta_missing"] = delta_missing
            scores = {"calibrated_score": calibrated_score}
            return data, scores

        grammar = q5c_grammar() if _depth_grammar_enabled() else None
        # v4.2 #5：score_std 按需采样 —— 只有语义脱耦分支（SEMANTIC_OVERRIDE）启用时，
        # score_std 才会被 _apply_hard_verdict 消费，此时才值得为 Q5c 多次采样；
        # 否则单次调用即可（std 置 0，语义脱耦分支因 low_conf=False 之外的逻辑不依赖它）。
        if CONSENSUS_ENABLED and CONSENSUS_SAMPLES > 1 and SEMANTIC_OVERRIDE_ENABLED:
            data, score_stds, _raw = self._sample_node("Q5c", prompt, _parse_q5c, grammar=grammar)
        else:
            self._node_stds.pop("Q5c", None)
            _raw_single = self._invoke_llm(
                prompt,
                node_name="Q5c",
                max_tokens=self._max_tokens,
                temperature=self._temperature,
                grammar=grammar,
            )
            if _raw_single is None:
                self._log("[Q5c] LLM 调用失败（返回 None），返回默认结果")
                return self._wrap_failed("Q5c", "LLM returned None")
            data, _scores = _parse_q5c(_raw_single)
            score_stds = dict.fromkeys(_scores, 0.0)
            self._node_stds["Q5c"] = score_stds

        # Q5c 的 score_std 写入模型字段，供 verdict 语义脱耦分支使用
        q5c_score_std = score_stds.get("calibrated_score", 0.0)

        return self._wrap_success(
            "Q5c",
            (
                Q5cResult(
                    reasoning=str(data.get("reasoning", "")),
                    delta=round(_clamp_float(data.get("delta", 0.0), delta_lo, delta_hi), 4),
                    calibrated_score=round(
                        _clamp_float(data.get("calibrated_score", 0.5), 0.0, 1.0), 4
                    ),
                    verdict=str(data.get("llm_verdict", "major_revision")),
                    llm_verdict=str(data.get("llm_verdict", "major_revision")),
                    delta_missing=bool(data.get("delta_missing", False)),
                    score_std=q5c_score_std,
                    balancer_log=balancer_log,
                ),
                balanced_cp,
            ),
        )

    # ------------------------------------------------------------------
    # 平衡者辅助：判断辩护是否成功反驳质疑
    # ------------------------------------------------------------------
    @staticmethod
    def _is_successful_rebuttal(defense: str, critique: str) -> bool:
        """判断辩护是否成功反驳了质疑。

        判定标准（代码层硬规则，不依赖 LLM）：
        1. 辩护非锅炉板回复（非 "原文暂未涉及，将在终稿补充" 等默认答复）
        2. 辩护有实质技术内容（长度 ≥ 10 字符）
        3. 辩护与质疑关键术语有重叠（表明辩护确实针对该质疑）
        """
        if not defense or not critique:
            return False
        _BOILERPLATE_DEFENSES = (
            "原文暂未涉及，将在终稿补充",
            "将在终稿补充",
            "原文暂未涉及",
            "暂未涉及",
        )
        defense_stripped = defense.strip()
        # 规则 1：非锅炉板
        if any(bp in defense_stripped for bp in _BOILERPLATE_DEFENSES):
            return False
        # 规则 2：实质内容（≥10 字符）
        if len(defense_stripped) < 10:
            return False
        # 规则 3：术语重叠检测（从质疑和辩护中各提取关键词，检查交集）
        # 简单但有效的启发式：对中英文分别提取 2-gram 特征词
        critique_features = _extract_key_terms(critique)
        defense_features = _extract_key_terms(defense_stripped)
        overlap = critique_features & defense_features
        # 至少需要 1 个关键术语重叠才认为"针对性辩护"
        return len(overlap) >= 1

    # ------------------------------------------------------------------
    # P2-1: 热点词加载（default / db / paper）
    # ------------------------------------------------------------------
    def _load_hotspots(self, paper: Any | None = None) -> list[str]:
        """按配置加载热点词表。"""
        if self._hotspots_source == "default" or not self._hotspots_source:
            return list(DEFAULT_HOTSPOTS)
        if self._hotspots_source == "paper" and paper is not None:
            # 从论文 tags/fields_of_study 推导，若无则回退 default
            tags = getattr(paper, "tags", None) or []
            fields = getattr(paper, "fields_of_study", None) or []
            derived = [str(t) for t in tags if t] + [str(f) for f in fields if f]
            if derived:
                return derived[:12]
            return list(DEFAULT_HOTSPOTS)
        if self._hotspots_source == "db":
            keywords = self.data_provider.get_hotspot_keywords(
                paper_id=getattr(paper, "id", 0) if paper else 0
            )
            if keywords:
                self._log(f"[热点词] db 加载 {len(keywords)} 个")
                return keywords
            return list(DEFAULT_HOTSPOTS)
        return list(DEFAULT_HOTSPOTS)

    # ------------------------------------------------------------------
    # v4.2 #1: PaperFigure 图表证据加载（QE 证据池并入 + QF 节点消费）
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # v4.2 #1 : ͼ֤һԴ
    # ------------------------------------------------------------------
    @staticmethod
    def _classify_claim_severity(
        value: Any,
        axis_min: float | None,
        axis_max: float | None,
        curve_y_min: float | None = None,
        curve_y_max: float | None = None,
        *,
        paper_verdict_risk: float = 0.0,
    ) -> str:
        """根据越界幅度区分轻微(minor)与严重(fatal)越界。

        优先加载训练好的 severity 决策树模型；缺失时回退到固定 0.5 阈值。
        """
        return classify_claim_severity(
            value,
            axis_min,
            axis_max,
            curve_y_min,
            curve_y_max,
            paper_verdict_risk=paper_verdict_risk,
        )

    @staticmethod
    def _extract_out_of_range_claims(
        figures: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int]:
        """从 figure 列表中提取真正 out-of-range 的数值断言。

        当同一 figure 存在 curve_points 时，用实际曲线 y 值范围做二次校验；
        复核通过的断言不视为 out-of-range，并在 ``claim_validation`` 中标记
        ``curve_corrected=True``。

        返回：
            - out_of_range: 真正越界的断言列表，每个 dict 包含：
                figure, page, figure_index, metric, value, axis_range,
                curve_y_min, curve_y_max
            - corrected: 被 curve_points 修正为合理的断言数量
        """
        out_of_range: list[dict[str, Any]] = []
        corrected = 0
        for fig in figures or []:
            claim_validation = fig.get("claim_validation") or {}
            if not isinstance(claim_validation, dict):
                continue
            claims = claim_validation.get("claims") or []
            validated = claim_validation.get("validated") or []
            if not isinstance(validated, list):
                continue

            # 应用共享的 curve_points 二次复核逻辑，并持久化到 claim_validation。
            corrected += apply_curve_correction(claim_validation, fig.get("curve_points"))

            # 计算曲线 y 范围，用于后续 flag 展示
            y_min: float | None = None
            y_max: float | None = None
            curve_points = fig.get("curve_points")
            if isinstance(curve_points, list) and curve_points:
                ys = [
                    float(p["y"])
                    for p in curve_points
                    if isinstance(p, dict) and isinstance(p.get("y"), (int, float))
                ]
                if ys:
                    y_min, y_max = min(ys), max(ys)

            axis_info = fig.get("axis_info") or {}
            axis_range = axis_info.get("y_ticks") or axis_info.get("yticks") or []
            axis_min: float | None = None
            axis_max: float | None = None
            if isinstance(axis_range, (list, tuple)) and axis_range:
                try:
                    axis_min = float(min(axis_range))
                    axis_max = float(max(axis_range))
                except (TypeError, ValueError):
                    axis_min = axis_max = None

            for idx, v in enumerate(validated):
                if not isinstance(v, dict):
                    continue
                if v.get("valid") is not False:
                    continue
                claim = (
                    claims[idx]
                    if idx < len(claims) and isinstance(claims[idx], dict)
                    else (v.get("claim") or {})
                )
                value = claim.get("value")
                metric = claim.get("metric", "unknown") or "unknown"
                severity = DepthReviewer._classify_claim_severity(
                    value, axis_min, axis_max, y_min, y_max
                )
                # 兼容旧调用：classify_claim_severity 在模型缺失时回退 0.5 阈值
                out_of_range.append(
                    {
                        "figure": fig,
                        "page": fig.get("page"),
                        "figure_index": fig.get("figure_index") or fig.get("index"),
                        "metric": metric,
                        "value": value,
                        "axis_range": axis_range,
                        "axis_min": axis_min,
                        "axis_max": axis_max,
                        "curve_y_min": y_min,
                        "curve_y_max": y_max,
                        "severity": severity,
                    }
                )
        return out_of_range, corrected

    @staticmethod
    def _count_validated_claims(figures: list[dict[str, Any]]) -> tuple[int, int]:
        """统计 claim_validation 中总校验条目与一致条目数。

        Returns:
            (total_validated, valid_claims)
        """
        total_validated = 0
        valid_claims = 0
        for fig in figures or []:
            claim_validation = fig.get("claim_validation") or {}
            if not isinstance(claim_validation, dict):
                continue
            validated = claim_validation.get("validated") or []
            if not isinstance(validated, list):
                continue
            for v in validated:
                if isinstance(v, dict) and v.get("valid") is not None:
                    total_validated += 1
                    if v.get("valid") is True:
                        valid_claims += 1
        return total_validated, valid_claims

    @staticmethod
    def _compute_claim_validation_penalty(
        figures: list[dict[str, Any]],
    ) -> tuple[float, list[str], str]:
        """基于 PaperFigure.claim_validation 计算数值断言越界惩罚。

        当同一 figure 存在 curve_points 时，用实际曲线 y 值范围做更精确的
        二次校验：若 claim value 落在曲线 y 范围内，则不视为 out-of-range，
        从而提升 figure_consistency_score。

        逻辑：
        - 遍历每个 figure 的 claim_validation.claims / validated。
        - valid == False 且 metric 匹配时，尝试用 curve_points 的 y 范围复核。
        - 复核通过（值在曲线范围内）则不扣分；仍越界才计数。
        - 每个 out-of-range claim 扣 0.1 分，上限 0.3，避免压倒 LLM 语义判断。
        - 没有 claim validation 信息时返回 0 惩罚。

        对称 bonus 由 _compute_claim_validation_bonus 单独计算并在 _run_qf 中
        叠加到 figure_consistency_score，默认关闭，可通过运行时配置开启。

        Returns:
            (penalty, flags, reasoning)
        """
        total_validated, valid_claims = DepthReviewer._count_validated_claims(figures)

        out_of_range_claims, corrected = DepthReviewer._extract_out_of_range_claims(figures)
        out_of_range = len(out_of_range_claims)
        # 记录 asymmetry 指标：一致/越界 claim 数，供生产数据分析。
        if total_validated:
            logger.debug(
                "[QF claim-validation asymmetry] valid=%d out_of_range=%d total=%d",
                valid_claims,
                out_of_range,
                total_validated,
            )

        if out_of_range == 0:
            if corrected:
                return (
                    0.0,
                    [f"claim_curve_corrected:{corrected}"],
                    (f"曲线点二次校验修正 {corrected} 个越界断言，无惩罚"),
                )
            return 0.0, [], ""
        penalty = min(
            get_depth_claim_validation_penalty_max(),
            get_depth_claim_validation_penalty_per_claim() * out_of_range,
        )

        def _format_claim_flag(c: dict[str, Any]) -> str:
            figure_index = c.get("figure_index")
            page = c.get("page")
            metric = c.get("metric", "unknown") or "unknown"
            value = c.get("value")
            axis_min = c.get("axis_min")
            axis_max = c.get("axis_max")
            curve_min = c.get("curve_y_min")
            curve_max = c.get("curve_y_max")

            if figure_index is None:
                location = "Fig"
            elif page is None:
                location = f"Fig{figure_index}"
            else:
                location = f"Fig{figure_index} (p{page})"
            parts = [location, f"{metric}={value}"]
            if axis_min is not None and axis_max is not None:
                parts.append(f"axis[{axis_min:.4g},{axis_max:.4g}]")
            if curve_min is not None and curve_max is not None:
                parts.append(f"curve[{curve_min:.4g},{curve_max:.4g}]")
            parts.append("out-of-range")
            return " ".join(parts)

        claim_flags = [_format_claim_flag(c) for c in out_of_range_claims]
        flags: list[str] = [f"claim_out_of_range:{out_of_range}", *claim_flags]
        if corrected:
            flags.append(f"claim_curve_corrected:{corrected}")
        reasoning = (
            f"数值断言校验发现 {out_of_range} 个值超出 axis/curve 范围"
            f"(共 {total_validated} 条校验，曲线修正 {corrected} 条)，应用惩罚 -{penalty:.2f}"
        )
        return penalty, flags, reasoning

    @staticmethod
    def _compute_claim_validation_bonus(
        figures: list[dict[str, Any]],
    ) -> tuple[float, str]:
        """基于一致 claim 的比例计算对称 bonus。

        生产数据验证：当 valid:out_of_range 比例较高时，给予小幅正向激励，
        避免 LLM 给 0.5 中性 + claim 一致时 Δ 永远非正。
        默认关闭（depth_claim_validation_bonus_enabled=False）；开启后：
          - 需要至少 min_valid 条有效 claim；
          - bonus 随 valid/(valid+out_of_range) 比例衰减；
          - 单 claim 基准 × 数量，但不超过 max。

        Returns:
            (bonus, reasoning)
        """
        if not get_depth_claim_validation_bonus_enabled():
            return 0.0, ""

        total_validated, valid_claims = DepthReviewer._count_validated_claims(figures)

        min_valid = get_depth_claim_validation_bonus_min_valid()
        if total_validated < min_valid or valid_claims == 0:
            return 0.0, ""

        out_of_range_claims, _ = DepthReviewer._extract_out_of_range_claims(figures)
        out_of_range = len(out_of_range_claims)
        consistency_ratio = valid_claims / max(valid_claims + out_of_range, 1)
        per_claim_bonus = get_depth_claim_validation_bonus_per_claim()
        max_bonus = get_depth_claim_validation_bonus_max()
        bonus = min(max_bonus, per_claim_bonus * valid_claims) * consistency_ratio
        if bonus <= 0.0:
            return 0.0, ""

        reasoning = (
            f"一致 claim {valid_claims}/{total_validated}，越界 {out_of_range}，"
            f"应用 bonus +{bonus:.3f}"
        )
        return round(bonus, 3), reasoning

    def _load_figure_items(self, paper_id: str) -> list[dict[str, Any]]:
        """从 PaperFigure 加载与论文相关的图表数据（caption_text / ocr_text / qwen_summary
        / axis_info / claim_validation）。

        fail-soft：任何异常（数据库不存在、查询失败）都返回空列表，
        让图表证据路径降级为文本层，不影响主流程。对 paper_id 做缓存。
        """
        if not paper_id or not DEPTH_FIGURE_EVIDENCE_ENABLED:
            return []
        if paper_id in self._figure_cache:
            return self._figure_cache[paper_id]
        items: list[dict[str, Any]] = []
        # v4.2 分层解耦：图表数据走 Provider，不再直接接触 SessionLocal / PaperFigure
        items = self.data_provider.load_figure_evidence(paper_id)
        self._figure_cache[paper_id] = items
        return items

    def _augment_qe_items_with_figures(self, qe_items: list[EvidenceItem], paper_id: str) -> int:
        """把 PaperFigure 图表证据追加进 QE 证据池（EvidenceItem 列表）。

        ID 编号延续 LLM 抽取的 E1..En（不引入新命名空间），下游证据校验、
        GBNF 枚举、正则兜底全部无需改动即可引用图表证据。
        内容以「【图表】」前缀标记来源（学术诚信：可追溯，非正文编造）。

        Returns:
            实际追加的图表证据条数。
        """
        figures = self._load_figure_items(paper_id)
        if not figures:
            return 0
        max_eid = 0
        for item in qe_items:
            m = re.fullmatch(r"E(\d+)", item.id)
            if m:
                max_eid = max(max_eid, int(m.group(1)))
        added = 0
        for fig in figures[:MAX_FIGURE_EVIDENCE]:
            max_eid += 1
            body = fig["summary"] or fig["ocr"]
            content = f"【图表】第{fig['page']}页图{fig['index']}: {body[:FIGURE_EVIDENCE_CHARS]}"
            qe_items.append(
                EvidenceItem(
                    id=f"E{max_eid}",
                    content=content,
                    section="Figures",
                    keywords=["图表", "figure"],
                )
            )
            added += 1
        if added:
            self._log(f"[图表证据] 并入 {added} 条 PaperFigure 图表证据到 QE 证据池")
        return added

    def _augment_qe_items_with_claim_validation(
        self, qe_items: list[EvidenceItem], paper_id: str
    ) -> tuple[int, float]:
        """Append out-of-range figure claim validations to the QE evidence pool.

        使用 _extract_out_of_range_claims 做曲线点二次校验，只把真正
        out-of-range 的数值断言注入证据池，供下游节点（Q5a/Q5c）作为
        图文一致性矛盾证据。

        Returns:
            (added_count, severity_penalty) — severity_penalty 用于 Q5c 差异化加权：
            fatal 越界加权 1.0，minor 越界加权 0.25。
        """
        figures = self._load_figure_items(paper_id)
        if not figures:
            return 0, 0.0

        out_of_range_claims, _corrected = self._extract_out_of_range_claims(figures)
        if not out_of_range_claims:
            return 0, 0.0

        max_eid = 0
        for item in qe_items:
            m = re.fullmatch(r"E(\d+)", item.id)
            if m:
                max_eid = max(max_eid, int(m.group(1)))

        added = 0
        severity_penalty = 0.0
        for claim_info in out_of_range_claims[:MAX_FIGURE_EVIDENCE]:
            max_eid += 1
            metric = claim_info["metric"]
            value = claim_info["value"]
            if value is None:
                continue
            severity = str(claim_info.get("severity", "minor"))
            severity_penalty += (
                get_depth_severity_fatal_weight()
                if severity == "fatal"
                else get_depth_severity_minor_weight()
            )
            axis_range = claim_info["axis_range"]
            curve_min = claim_info["curve_y_min"]
            curve_max = claim_info["curve_y_max"]
            curve_part = ""
            curve_part_en = ""
            if curve_min is not None and curve_max is not None:
                curve_part = f"; 曲线点 y 范围 [{curve_min:.4g}, {curve_max:.4g}]"
                curve_part_en = f"; curve_points y-range [{curve_min:.4g}, {curve_max:.4g}]"
            content_zh = (
                f"[{severity.upper()}] [图文一致性] 图{claim_info['figure_index']} "
                f"(第{claim_info['page']}页): {metric}={value} 超出坐标轴范围 "
                f"{axis_range}{curve_part}"
            )
            content_en = (
                f"[{severity.upper()}] [Figure consistency] Figure {claim_info['figure_index']} "
                f"(p{claim_info['page']}): {metric}={value} is outside axis range "
                f"{axis_range}{curve_part_en}"
            )
            qe_items.append(
                EvidenceItem(
                    id=f"E{max_eid}",
                    content=content_zh,
                    content_zh=content_zh,
                    content_en=content_en,
                    section="Figures",
                    keywords=["figure", "claim", str(metric), "out-of-range"],
                    severity=severity,
                )
            )
            added += 1
        if added:
            self._log(f"[figure claim evidence] appended {added} out-of-range claim(s) to QE pool")
        return added, severity_penalty

    # ------------------------------------------------------------------
    # DWM: 动态权重（P0-1 合并 OIM）
    # ------------------------------------------------------------------
    def _compute_dwm(
        self,
        q1: Q1Result,
        q2: Q2Result,
        q3: Q3Result,
        q4: Q4Result,
        objective_score: float | None = None,
        qf: QFResult | None = None,
    ) -> tuple[float, float, dict[str, float]]:
        """返回 (base_score, final_base_score, weights)。

        P2-1: hotspot_alignment_score 不再参与 DWM 计分。
        P0-1: 当 OIM_WEIGHT>0 且 objective_score 有效时，
              final_base = (1-w)*base_score + w*objective_score。
        v4.2 #6: 弹性权重改平滑插值 —— 低置信时 t=confidence/ELASTIC_THRESHOLD
              连续过渡（阈值处 t→1，与纯类型权重相接），消除硬跳变。
        v4.2 #1深: QF 图文一致性对 final_base 做有界微调
              （DEPTH_FIGURE_WEIGHT × (score-0.5)，默认最大 ±0.05）。
        """
        base = TYPE_WEIGHTS.get(q1.type, TYPE_WEIGHTS["B"])
        if q1.confidence >= ELASTIC_THRESHOLD:
            weights = dict(base)
        else:
            others = [t for t in TYPE_WEIGHTS if t != q1.type]
            # 平滑插值：阈值以下 t 从 1 连续衰减到 0（confidence=0 时退化为
            # 其他类型权重的均匀平均），权重和恒为 1。
            t = min(1.0, q1.confidence / ELASTIC_THRESHOLD) if ELASTIC_THRESHOLD > 0 else 1.0
            weights = {}
            for dim in base:
                other_avg = sum(TYPE_WEIGHTS[ot][dim] for ot in others) / len(others)
                weights[dim] = t * base[dim] + (1.0 - t) * other_avg

        base_score = (
            weights["beta"] * q2.novelty_score
            + weights["gamma"] * q3.rigor_score
            + weights["delta"] * q4.influence_score
            + weights["epsilon"] * q4.reproducibility_score
        )
        base_score = _clamp_float(base_score)

        # P0-1 OIM 合并
        final_base = base_score
        if objective_score is not None and OIM_WEIGHT > 0:
            w = min(max(OIM_WEIGHT, 0.0), 1.0)
            final_base = (1.0 - w) * base_score + w * objective_score
            final_base = _clamp_float(final_base)
            self._log(
                f"OIM: w={w:.2f}, objective={objective_score:.3f}, "
                f"base={base_score:.3f} -> final_base={final_base:.3f}"
            )

        # v4.2 QF 图文一致性微调（有界，仅在实际存在图表证据时生效）
        if qf is not None and qf.has_figures and DEPTH_FIGURE_WEIGHT > 0:
            w_fig = min(max(DEPTH_FIGURE_WEIGHT, 0.0), 0.5)
            adjusted = final_base + w_fig * (qf.figure_consistency_score - 0.5)
            adjusted = _clamp_float(adjusted)
            self._log(
                f"QF 微调: w={w_fig:.2f}, figure_consistency={qf.figure_consistency_score:.3f}, "
                f"claim_validation_penalty={qf.claim_validation_penalty:.3f}, "
                f"claim_validation_bonus={qf.claim_validation_bonus:.3f}, "
                f"final_base {final_base:.3f} -> {adjusted:.3f}"
            )
            final_base = adjusted

        return base_score, final_base, weights

    # ------------------------------------------------------------------
    # 硬编码最终裁决（一票否决优先 + 非 fatal 分支严禁 reject）
    # P1-2: 增加语义脱耦分支
    # ------------------------------------------------------------------
    def _apply_hard_verdict(
        self,
        q5c: Q5cResult,
        critique_points: list[CritiquePoint],
        figure_coverage: str = "disabled",
    ) -> FinalVerdict:
        """代码层硬编码裁决。

        路径覆盖矩阵：
        ┌───────────┬──────────┬────────────────┬───────────────┐
        │ has_fatal │ score    │ llm_verdict    │ final_verdict │
        ├───────────┼──────────┼────────────────┼───────────────┤
        │ True      │ ≥ 0.8*   │ (任意)         │ major_revision│ 一票否决
        │ True      │ < 0.8    │ (任意)         │ reject        │ 一票否决
        │ True      │ ≥ 0.9    │ accept         │ accept        │ 护栏豁免(伪fatal)
        │ False     │ (任意)   │ reject         │ major_revision│ 严禁reject
        │ False     │ ≥ 0.8    │ ≠ reject       │ accept        │ 分数对齐
        │ False     │ [0.7,0.8)│ ≠ reject       │ minor_revision│ 分数对齐
        │ False     │ [0.5,0.7)│ ≠ reject       │ major_revision│ 分数对齐
        │ False     │ < 0.5    │ ≠ reject       │ major_revision│ 低分兜底
        └───────────┴──────────┴────────────────┴───────────────┘
        """
        has_fatal = any(cp.severity == "fatal" for cp in critique_points)
        has_minor = any(cp.severity == "minor" for cp in critique_points)
        fatal_count = sum(1 for cp in critique_points if cp.severity == "fatal")
        minor_count = sum(1 for cp in critique_points if cp.severity == "minor")
        score = q5c.calibrated_score
        llm_v = q5c.llm_verdict

        accept_threshold = self._calibration_result.accept_threshold or VERDICT_ACCEPT_THRESHOLD
        reject_threshold = self._calibration_result.reject_threshold or VERDICT_REJECT_THRESHOLD

        # ── 规则 1（最高优先级）：一票否决（需 ≥FATAL_VETO_MIN 条致命缺陷）──
        # 单条致命缺陷不足以否决：避免 LLM 一次误判把 0.7~0.95 的论文直接拒稿/大修。
        # 分数护栏：校准分 ≥ FATAL_VETO_ACCEPT_FLOOR 且 LLM 自身判 accept 时，
        # 高分与 fatal 标签自相矛盾 → fatal 视为噪声，跳过否决（落到规则 2 分数对齐）。
        strong_and_accepted = (score >= FATAL_VETO_ACCEPT_FLOOR) and (llm_v == "accept")
        if fatal_count >= FATAL_VETO_MIN and not strong_and_accepted:
            if score >= 0.8:
                final_verdict = "major_revision"
                reason = (
                    f"存在 {fatal_count} 个致命缺陷（需 ≥{FATAL_VETO_MIN} 条才触发一票否决），"
                    f"虽然校准分 {score:.3f} ≥ 0.8，但强制降级为 major_revision"
                )
            else:
                final_verdict = "reject"
                reason = (
                    f"存在 {fatal_count} 个致命缺陷（一票否决触发），"
                    f"校准分 {score:.3f} < 0.8，判定为 reject"
                )
            return FinalVerdict(
                calibrated_score=score,
                final_verdict=final_verdict,
                override_reason=reason,
                llm_verdict=llm_v,
                has_fatal=has_fatal,
                has_minor=has_minor,
                fatal_count=fatal_count,
                minor_count=minor_count,
                figure_coverage=figure_coverage,
            )

        # ── P1-2 语义脱耦：高置信 + LLM 显式裁决时尊重语义判断 ──
        score_std = getattr(q5c, "score_std", 0.0)
        low_conf = score_std > LOW_CONF_THRESHOLD
        if SEMANTIC_OVERRIDE_ENABLED and not low_conf and llm_v in ("accept", "reject"):
            if llm_v == "accept" and score >= accept_threshold:
                final_verdict = "accept"
                reason = (
                    f"无触发否决的致命缺陷，Q5c 显式裁决 accept，且分数 {score:.3f} ≥ {accept_threshold}，"
                    f"置信 std={score_std:.3f}，尊重语义判断"
                )
            elif llm_v == "reject" and score < reject_threshold:
                final_verdict = "reject"
                reason = (
                    f"无触发否决的致命缺陷，Q5c 显式裁决 reject，且分数 {score:.3f} < {reject_threshold}，"
                    f"置信 std={score_std:.3f}，尊重语义判断"
                )
            else:
                # 语义裁决与分数冲突，回退分数对齐
                final_verdict = "major_revision"
                reason = f"无触发否决的致命缺陷，Q5c 裁决 {llm_v} 与分数 {score:.3f} 冲突，回退为 major_revision"
            return FinalVerdict(
                calibrated_score=score,
                final_verdict=final_verdict,
                override_reason=reason,
                llm_verdict=llm_v,
                has_fatal=has_fatal,
                has_minor=has_minor,
                fatal_count=fatal_count,
                minor_count=minor_count,
                figure_coverage=figure_coverage,
            )

        # ── 规则 2：非 fatal 分支（严禁 reject）──
        if score >= accept_threshold:
            alignment_verdict = "accept"
        elif score >= 0.7:
            alignment_verdict = "minor_revision"
        elif score >= reject_threshold:
            alignment_verdict = "major_revision"
        else:
            alignment_verdict = "reject"

        # 非 fatal 分支的任何 reject 都强制转为 major_revision
        if llm_v == "reject" or alignment_verdict == "reject":
            final_verdict = "major_revision"
            clause = (
                "LLM 误判 reject"
                if llm_v == "reject"
                else f"分数对齐到 reject（{score:.3f} < {reject_threshold}）"
            )
            reason = (
                f"无触发否决的致命缺陷（{fatal_count} fatal + {minor_count} minor），"
                f"{clause} → 修正为 major_revision"
            )
        else:
            final_verdict = alignment_verdict
            if minor_count > 0:
                reason = (
                    f"无触发否决的致命缺陷（{minor_count} 个 minor），"
                    f"按分数 {score:.3f} 对齐为 {final_verdict}"
                )
            else:
                fatal_note = (
                    f"（存在 {fatal_count} 个致命缺陷但未达否决门槛 {FATAL_VETO_MIN} 条）"
                    if has_fatal
                    else ""
                )
                reason = f"无触发否决的缺陷{fatal_note}，按分数 {score:.3f} 对齐为 {final_verdict}"

        return FinalVerdict(
            calibrated_score=score,
            final_verdict=final_verdict,
            override_reason=reason,
            llm_verdict=llm_v,
            has_fatal=has_fatal,
            has_minor=has_minor,
            fatal_count=fatal_count,
            minor_count=minor_count,
            figure_coverage=figure_coverage,
        )

    def _figure_coverage(self, qf: QFResult | None) -> str:
        """根据 QF 节点配置与运行结果确定图表覆盖状态。"""
        # 图表证据功能被整体禁用时，即使 QF 节点开关打开也应视为 disabled，
        # 否则 _load_figure_items 恒返空，QF 一定检测为无图表，会被误标成
        # missing 并触发无用的后台补跑。
        # 例外：figure_weight>0 时 QF 独立加载 figure（测试配置），不算 disabled。
        if not DEPTH_QF_NODE_ENABLED:
            return "disabled"
        if not DEPTH_FIGURE_EVIDENCE_ENABLED and DEPTH_FIGURE_WEIGHT <= 0:
            return "disabled"
        if qf is not None and qf.has_figures:
            return "analyzed"
        return "missing"

    def _inject_figure_flags(
        self, critique_points: list[CritiquePoint], qf: QFResult | None
    ) -> list[CritiquePoint]:
        """将 QF 图表一致性 flags 以 minor 形式注入 critique_points。

        去重：若 flag 与已有 critique 文本互相包含，则不再重复注入。
        """
        if not qf or not qf.has_figures or not qf.inconsistency_flags:
            return critique_points
        existing_texts = [cp.point.lower() for cp in critique_points]

        def _is_duplicate(flag: str) -> bool:
            flag_lower = flag.lower()
            for existing in existing_texts:
                if flag_lower in existing or existing in flag_lower:
                    return True
            return False

        injected = list(critique_points)
        for flag in qf.inconsistency_flags:
            if _is_duplicate(flag):
                continue
            injected.append(CritiquePoint(point=f"【图表】{flag}", severity="minor"))
        return injected

    def _apply_figure_corroboration(
        self,
        critique_points: list[CritiquePoint],
        qf: QFResult | None,
    ) -> list[CritiquePoint]:
        """corroboration 升级：严重图文不一致时把图表来源 minor 升为 fatal。"""
        if (
            not qf
            or not qf.has_figures
            or qf.figure_consistency_score >= 0.3
            or len(qf.inconsistency_flags) < 2
        ):
            return critique_points
        return [
            CritiquePoint(point=cp.point, severity="fatal")
            if cp.point.startswith("【图表】") and cp.severity == "minor"
            else cp
            for cp in critique_points
        ]

    # ------------------------------------------------------------------
    # 主入口（串行）
    # ------------------------------------------------------------------
    def _fast_result(self, paper_id: str, title: str) -> DepthV4Result:
        """fast 模式：合成 neutral DepthV4Result，跳过全部 LLM 调用。"""
        self._log(f"[fast-path] paper={paper_id}: 跳过 LLM，返回合成 neutral review")
        return DepthV4Result(
            paper_id=paper_id,
            title=title,
            has_substance=True,
            expectation=0.5,
            q0_reasoning="fast 模式：未调用 LLM",
            q0_evidence="",
            paper_type="B",
            secondary_type="none",
            confidence=0.5,
            q1_reasoning="fast 模式：未调用 LLM",
            novelty_score=0.5,
            hotspot_alignment_score=0.5,
            core_contribution="",
            q2_reasoning="fast 模式：未调用 LLM",
            rigor_score=0.5,
            missing_items=[],
            q3_reasoning="fast 模式：未调用 LLM",
            influence_score=0.5,
            reproducibility_score=0.5,
            q4_reasoning="fast 模式：未调用 LLM",
            critique_points=[],
            defense_points=[],
            figure_consistency_score=0.5,
            figure_flags=[],
            figure_evidence_count=0,
            claim_validation_penalty=0.0,
            claim_validation_bonus=0.0,
            qf_reasoning="fast 模式：未执行图文一致性审查",
            calibrated_score=0.5,
            delta=0.0,
            delta_missing=False,
            chair_reasoning="fast 模式：未调用 LLM，合成 neutral review",
            llm_verdict="major_revision",
            final_verdict="major_revision",
            override_reason="fast 模式短路裁决",
            evidence_pool=[],
            evidence_checks={},
            base_score=0.5,
            weights={"alpha": 0.0, "beta": 0.0, "gamma": 0.0},
            node_score_stds={},
            node_logs=list(self._logs),
            evaluated_at=datetime.now().isoformat(),
        )

    def review(
        self,
        paper_id: str,
        title: str,
        full_text: str,
        abstract: str = "",
        hotspots: list[str] | None = None,
        paper_obj: Any | None = None,
        paper_meta: dict[str, Any] | None = None,
    ) -> DepthV4Result:
        """串行执行审稿流水线（v4.2：Q0→Q1→QE→Q234→QF→Q5a→Q5b→Q5c）。"""
        self._paper_meta = paper_meta
        self._logs = []
        self._log(f"======== DEPTH v4.1 审稿开始: {paper_id} (串行模式) ========")

        if self._compute_mode == "fast":
            return self._fast_result(paper_id, title)

        if hotspots is None:
            hotspots = self._load_hotspots(paper_obj)

        # 1. 文本分段
        segments = segment_paper_text(full_text, abstract)
        pa_intro = segments["paper_abstract_intro"]
        pa_full = segments["paper_full_text"]
        pa_concl = segments["paper_abstract_conclusion"]
        self._log(
            f"文本分段完成: intro={len(pa_intro)}c, full={len(pa_full)}c, concl={len(pa_concl)}c"
        )
        self._log(f"热点词: {hotspots[:5]}{'...' if len(hotspots) > 5 else ''}")

        # P0-1: 提前抽取客观特征
        objective_features = _extract_objective_features(pa_full)
        objective_score: float | None = None
        if objective_features is not None:
            objective_score = objective_features.weighted_score(OIM_FEATURE_WEIGHTS)
            self._log(f"OIM 特征: {objective_features}, objective_score={objective_score:.3f}")

        # 2. 串行节点链（Q0→Q1→QE→Q234/Q2Q3Q4→QF→Q5a→Q5b→Q5c）
        ctx = PaperContext(
            paper_id=paper_id,
            title=title,
            full_text=full_text,
            abstract=abstract,
            paper_abstract_intro=pa_intro,
            paper_full_text=pa_full,
            paper_abstract_conclusion=pa_concl,
            hotspots=hotspots,
        )
        results = DAGOutputs()

        q0 = self._unwrap_or_raise(self._run_q0(ctx), "Q0")
        results.Q0 = q0
        self._log(f"Q0: has_substance={q0.has_substance}, expectation={q0.expectation:.3f}")

        q1 = self._unwrap_or_raise(self._run_q1(ctx), "Q1")
        results.Q1 = q1
        self._log(
            f"Q1: type={q1.type}, secondary={q1.secondary_type}, confidence={q1.confidence:.3f}"
        )

        qe_result, _ = self._unwrap_or_raise(self._run_qe(ctx), "QE")
        qe = qe_result
        evidence_pool: dict[str, str] = {}
        for item in qe.evidence_pool:
            kw_suffix = ""
            if item.keywords:
                kw_suffix = f" [关键词: {', '.join(item.keywords)}]"
            evidence_pool[item.id] = item.content + kw_suffix
        self._log(f"QE: {len(evidence_pool)} 条证据, IDs={list(evidence_pool.keys())}")

        # ── 证据池为空 → 不可恢复的评审失败（LLM 未返回有效输出） ──
        if not evidence_pool:
            raise ValueError(
                "证据池为空——LLM 未返回有效证据（可能超时或返回空响应）。"
                "QE 节点是整个 DAG 的基础，无证据则下游节点无意义，评审中止。"
            )

        # ── v4.2 #1轻：PaperFigure 图表证据并入证据池（在空池检查之后：
        # 图表证据是增益项，不能挽救 LLM 证据抽取失效）──
        n_llm_items = len(qe.evidence_pool)
        figure_n = self._augment_qe_items_with_figures(qe.evidence_pool, paper_id)
        _claim_n, _claim_severity_penalty = self._augment_qe_items_with_claim_validation(
            qe.evidence_pool, paper_id
        )
        for item in qe.evidence_pool[n_llm_items:]:
            kw_suffix = f" [关键词: {', '.join(item.keywords)}]" if item.keywords else ""
            evidence_pool[item.id] = item.content + kw_suffix
        if figure_n:
            self._log(f"QE: 证据池扩至 {len(evidence_pool)} 条（含 {figure_n} 条图表证据）")

        # Populate typed QE output
        results.QE = QEOutput(
            qe_result=qe,
            ev_pool=evidence_pool,
            claim_severity_penalty=_claim_severity_penalty,
        )

        # ── 维度评分：v4.2 默认合并单次调用（Q234），失败/关闭时回退三次独立调用 ──
        if DEPTH_MERGED_SCORING_ENABLED:
            q2, q3, q4 = self._unwrap_or_raise(self._run_q234(ctx, results), "Q234")
            results.Q234 = (q2, q3, q4)  # v4.2: keep DAG path symmetry
        else:
            q2 = self._unwrap_or_raise(self._run_q2(ctx, results), "Q2")
            q3 = self._unwrap_or_raise(self._run_q3(ctx, results), "Q3")
            q4 = self._unwrap_or_raise(self._run_q4(ctx, results), "Q4")
        results.Q2 = q2
        results.Q3 = q3
        results.Q4 = q4
        self._log(
            f"Q2: novelty={q2.novelty_score:.3f}, "
            f"hotspot={q2.hotspot_alignment_score:.3f}, eid={q2.evidence_id} v={q2.verified}"
        )
        self._log(
            f"Q3: rigor={q3.rigor_score:.3f}, missing={len(q3.missing_items)}, "
            f"eid={q3.evidence_id} v={q3.verified}"
        )
        self._log(
            f"Q4: influence={q4.influence_score:.3f}, repro={q4.reproducibility_score:.3f}, "
            f"eid={q4.evidence_id} v={q4.verified}"
        )

        # ── v4.2 #1深：QF 图文一致性节点（无图表时中性跳过，零成本）──
        if DEPTH_QF_NODE_ENABLED:
            qf = self._unwrap_or_raise(self._run_qf(ctx, results), "QF")
        else:
            qf = QFResult()
        results.QF = qf
        self._log(
            f"QF: figure_consistency={qf.figure_consistency_score:.3f}, "
            f"has_figures={qf.has_figures}, flags={len(qf.inconsistency_flags)}"
        )

        q5a = self._unwrap_or_raise(self._run_q5a(ctx, results), "Q5a")
        results.Q5a = q5a
        self._log(f"Q5a: {len(q5a.critique_points)} 条质疑, eid={q5a.evidence_id} v={q5a.verified}")

        q5b = self._unwrap_or_raise(self._run_q5b(ctx, results), "Q5b")
        results.Q5b = q5b
        self._log(f"Q5b: {len(q5b.defense_points)} 条辩护, eid={q5b.evidence_id} v={q5b.verified}")

        base_score, final_base, weights = self._compute_dwm(q1, q2, q3, q4, objective_score, qf=qf)
        self._log(
            f"DWM: base_score={base_score:.3f}, final_base={final_base:.3f}, weights={weights}"
        )
        q5c, balanced_cp = self._unwrap_or_raise(self._run_q5c(ctx, results, final_base), "Q5c")
        self._log(
            f"Q5c: delta={q5c.delta}, calibrated={q5c.calibrated_score:.3f}, "
            f"llm_v={q5c.llm_verdict}, delta_missing={q5c.delta_missing}"
        )

        figure_coverage = self._figure_coverage(qf)
        balanced_cp = self._apply_figure_corroboration(balanced_cp, qf)
        final = self._apply_hard_verdict(q5c, balanced_cp, figure_coverage=figure_coverage)
        self._log(
            f"硬编码裁决: {final.final_verdict} "
            f"(LLM={final.llm_verdict}, fatal={final.fatal_count}, minor={final.minor_count})"
        )
        self._log(f"裁决理由: {final.override_reason}")
        self._log(f"======== DEPTH v4.1 审稿完成: {paper_id} ========")

        return self._assemble_result(
            paper_id,
            title,
            q0,
            q1,
            qe,
            q2,
            q3,
            q4,
            q5a,
            q5b,
            q5c,
            final,
            base_score,
            weights,
            node_stds=self._node_stds,
            qf=qf,
            full_text=full_text,
        )

    # ------------------------------------------------------------------
    # 主入口（DepthDAG 引擎）
    # ------------------------------------------------------------------
    async def review_async_dag(
        self,
        paper_id: str,
        title: str,
        full_text: str,
        abstract: str = "",
        hotspots: list[str] | None = None,
        paper_meta: dict[str, Any] | None = None,
    ) -> DepthV4Result:
        """使用 DepthDAG 引擎执行拓扑并行审稿。

        通过 build_depth_dag + DepthDAG.execute()
        实现真正的 DAG 拓扑波次并行，自动处理依赖传递与失败级联取消。
        """
        self._paper_meta = paper_meta
        self._logs = []
        self._log(f"======== DEPTH v4.1 审稿开始: {paper_id} (DAG引擎) ========")

        if self._compute_mode == "fast":
            return self._fast_result(paper_id, title)

        if hotspots is None:
            hotspots = DEFAULT_HOTSPOTS

        # 1. 文本分段
        segments = segment_paper_text(full_text, abstract)
        pa_intro = segments["paper_abstract_intro"]
        pa_full = segments["paper_full_text"]
        pa_concl = segments["paper_abstract_conclusion"]
        self._log(
            f"文本分段完成: intro={len(pa_intro)}c, full={len(pa_full)}c, concl={len(pa_concl)}c"
        )

        # 2. 构建 PaperContext（v4.2 泛型擦除：统一传递类型化上下文）
        ctx = PaperContext(
            paper_id=paper_id,
            title=title,
            full_text=full_text,
            abstract=abstract,
            paper_abstract_intro=pa_intro,
            paper_full_text=pa_full,
            paper_abstract_conclusion=pa_concl,
            paper_meta=paper_meta or {},
            hotspots=hotspots,
        )

        # P0-1: 提前抽取客观特征（v4.2 修复：DAG 路径此前漏算 OIM，与串行不一致）
        objective_features = _extract_objective_features(pa_full)
        objective_score: float | None = None
        if objective_features is not None:
            objective_score = objective_features.weighted_score(OIM_FEATURE_WEIGHTS)
            self._log(f"OIM 特征: {objective_features}, objective_score={objective_score:.3f}")

        # 3. 构建 executor 工厂（闭包捕获 self + PaperContext）
        def executor_factory(node_name: str) -> Callable[[DAGOutputs], Awaitable[Any]]:
            if node_name == "Q0":

                async def _exec(results: DAGOutputs) -> Any:
                    return await asyncio.to_thread(self._run_q0, ctx)

                return _exec

            elif node_name == "Q1":

                async def _exec(results: DAGOutputs) -> Any:
                    return await asyncio.to_thread(self._run_q1, ctx)

                return _exec

            elif node_name == "QE":

                async def _exec(results: DAGOutputs) -> Any:
                    no = await asyncio.to_thread(self._run_qe, ctx)
                    if no.status != "success":
                        return no  # v4.3: propagate NodeOutput failure to DAG
                    qe_result, _ = no.data
                    # 空池检查基于 LLM 抽取结果（图表证据不能挽救 LLM 失效）
                    if not qe_result.evidence_pool:
                        return NodeOutput(
                            node_id="QE",
                            status="failed",
                            error="证据池为空——LLM 未返回有效证据（可能超时或返回空响应）。",
                        )
                    # v4.2 #1轻：PaperFigure 图表证据并入证据池
                    n_llm = len(qe_result.evidence_pool)
                    figure_n = self._augment_qe_items_with_figures(
                        qe_result.evidence_pool, paper_id
                    )
                    _claim_n_dag, _claim_severity_penalty_dag = (
                        self._augment_qe_items_with_claim_validation(
                            qe_result.evidence_pool, paper_id
                        )
                    )
                    ev_items = qe_result.evidence_pool
                    ev_pool: dict[str, str] = {
                        item.id: item.content
                        + (f" [关键词: {', '.join(item.keywords)}]" if item.keywords else "")
                        for item in ev_items
                    }
                    if figure_n:
                        self._log(
                            f"QE: 证据池扩至 {len(ev_pool)} 条"
                            f"（LLM {n_llm} 条 + 图表 {figure_n} 条）"
                        )
                    return QEOutput(
                        qe_result=qe_result,
                        ev_pool=ev_pool,
                        claim_severity_penalty=_claim_severity_penalty_dag,
                    )

                return _exec

            elif node_name == "Q234":

                async def _exec(results: DAGOutputs) -> Any:
                    return await asyncio.to_thread(self._run_q234, ctx, results)

                return _exec

            elif node_name == "QF":

                async def _exec(results: DAGOutputs) -> Any:
                    return await asyncio.to_thread(self._run_qf, ctx, results)

                return _exec

            elif node_name == "Q2":

                async def _exec(results: DAGOutputs) -> Any:
                    return await asyncio.to_thread(self._run_q2, ctx, results)

                return _exec

            elif node_name == "Q3":

                async def _exec(results: DAGOutputs) -> Any:
                    return await asyncio.to_thread(self._run_q3, ctx, results)

                return _exec

            elif node_name == "Q4":

                async def _exec(results: DAGOutputs) -> Any:
                    return await asyncio.to_thread(self._run_q4, ctx, results)

                return _exec

            elif node_name == "Q5a":

                async def _exec(results: DAGOutputs) -> Any:
                    return await asyncio.to_thread(self._run_q5a, ctx, results)

                return _exec

            elif node_name == "Q5b":

                async def _exec(results: DAGOutputs) -> Any:
                    return await asyncio.to_thread(self._run_q5b, ctx, results)

                return _exec

            elif node_name == "Q5c":

                async def _exec(results: DAGOutputs) -> Any:
                    q1_res = results.Q1
                    if results.Q234 is not None:
                        q2_res, q3_res, q4_res = results.Q234
                    else:
                        q2_res = results.Q2
                        q3_res = results.Q3
                        q4_res = results.Q4
                    qf_res = results.QF or QFResult()
                    base_score, final_base, weights = self._compute_dwm(
                        q1_res, q2_res, q3_res, q4_res, objective_score, qf=qf_res
                    )
                    no = await asyncio.to_thread(self._run_q5c, ctx, results, final_base)
                    if no.status != "success":
                        raise ValueError(f"Q5c failed: {no.error}")
                    q5c_res, balanced_cp = no.data
                    return (q5c_res, balanced_cp, base_score, weights)

                return _exec

            raise ValueError(f"未知的 DAG 节点: {node_name}")

        # 3. 构建 DAG 并执行（拓扑随 v4.2 开关动态：Q234 合并节点 / QF 图文节点）
        dag = build_depth_dag(
            executor_factory,
            merged_dimensions=DEPTH_MERGED_SCORING_ENABLED,
            include_figure_node=DEPTH_QF_NODE_ENABLED,
        )
        pipeline_result: PipelineResult = await dag.execute()

        # 4. 处理失败
        if not pipeline_result.success:
            failed = [name for name, r in pipeline_result.nodes.items() if r.status == "failed"]
            cancelled = [
                name for name, r in pipeline_result.nodes.items() if r.status == "cancelled"
            ]
            error_detail = (
                pipeline_result.error
                or f"节点 {failed} 执行失败，{len(cancelled)} 个节点被级联取消"
            )
            self._log(f"DAG 审稿失败 — 失败节点: {failed}, 级联取消: {cancelled}")
            self._log(f"错误详情: {error_detail}")
            raise RuntimeError(
                f"DEPTH v4.1 DAG 审稿失败: 节点 {failed} 失败，{len(cancelled)} 个节点被取消"
            )

        # 5. 从 PipelineResult 提取各节点输出
        n = pipeline_result.nodes
        q0_res = n["Q0"].output
        q1_res = cast(Q1Result, n["Q1"].output)
        qe_res = n["QE"].output.qe_result if n["QE"].output is not None else None
        if "Q234" in n:
            q2_res, q3_res, q4_res = cast(tuple[Q2Result, Q3Result, Q4Result], n["Q234"].output)  # type: ignore[assignment,misc]
        else:
            q2_res = n["Q2"].output  # type: ignore[assignment]
            q3_res = n["Q3"].output  # type: ignore[assignment]
            q4_res = n["Q4"].output  # type: ignore[assignment]
        qf_res = cast(QFResult, n["QF"].output) if "QF" in n else QFResult()
        q5a_res = cast(Q5aResult, n["Q5a"].output)
        q5b_res = cast(Q5bResult, n["Q5b"].output)
        q5c_res, balanced_cp, base_score, weights = cast(
            tuple[Q5cResult, list[CritiquePoint], float, dict[str, float]], n["Q5c"].output
        )

        # 6. DAG 执行计时日志
        timings = dag.get_timings()
        for name, elapsed in sorted(timings.items()):
            self._log(f"  节点 {name}: {elapsed:.2f}s")
        self._log(f"总耗时: {pipeline_result.total_elapsed:.2f}s")

        # 7. 应用硬性裁决并组装
        figure_coverage = self._figure_coverage(qf_res)
        balanced_cp = self._apply_figure_corroboration(balanced_cp, qf_res)
        final = self._apply_hard_verdict(q5c_res, balanced_cp, figure_coverage=figure_coverage)
        self._log(f"硬编码裁决: {final.final_verdict}")
        self._log(f"======== DEPTH v4.1 审稿完成: {paper_id} (DAG引擎) ========")

        return self._assemble_result(
            paper_id,
            title,
            q0_res,
            q1_res,
            qe_res,
            q2_res,
            q3_res,
            q4_res,
            q5a_res,
            q5b_res,
            q5c_res,
            final,
            base_score,
            weights,
            node_stds=self._node_stds,
            qf=qf_res,
            full_text=full_text,
        )

    # ------------------------------------------------------------------
    # 组装最终结果
    # ------------------------------------------------------------------
    def _assemble_result(
        self,
        paper_id: str,
        title: str,
        q0: Q0Result,
        q1: Q1Result,
        qe: QEResult,
        q2: Q2Result,
        q3: Q3Result,
        q4: Q4Result,
        q5a: Q5aResult,
        q5b: Q5bResult,
        q5c: Q5cResult,
        final: FinalVerdict,
        base_score: float,
        weights: dict[str, float],
        node_stds: dict[str, dict[str, float]] | None = None,
        qf: QFResult | None = None,
        full_text: str = "",
    ) -> DepthV4Result:
        node_stds = node_stds or {}
        qf = qf or QFResult()
        # 图表证据条数（QE 证据池中 section="Figures" 的条目）
        figure_evidence_count = sum(1 for item in qe.evidence_pool if item.section == "Figures")
        # 把各节点 std 打平成 node_score_stds（展示用）
        flat_stds: dict[str, float] = {}
        for node, scores in node_stds.items():
            for k, v in scores.items():
                flat_stds[f"{node}:{k}"] = round(v, 4)
            if node == "Q5c" and "calibrated_score" in scores:
                flat_stds["Q5c"] = round(scores["calibrated_score"], 4)
        return DepthV4Result(
            paper_id=paper_id,
            title=title,
            has_substance=q0.has_substance,
            expectation=round(q0.expectation, 4),
            q0_reasoning=q0.reasoning,
            q0_evidence=q0.evidence,
            paper_type=q1.type,
            secondary_type=q1.secondary_type,
            confidence=round(q1.confidence, 4),
            q1_reasoning=q1.reasoning,
            novelty_score=round(q2.novelty_score, 4),
            hotspot_alignment_score=round(q2.hotspot_alignment_score, 4),
            core_contribution=q2.core_contribution,
            q2_reasoning=q2.reasoning,
            rigor_score=round(q3.rigor_score, 4),
            missing_items=q3.missing_items,
            q3_reasoning=q3.reasoning,
            influence_score=round(q4.influence_score, 4),
            reproducibility_score=round(q4.reproducibility_score, 4),
            q4_reasoning=q4.reasoning,
            critique_points=q5a.critique_points,
            defense_points=q5b.defense_points,
            figure_consistency_score=round(qf.figure_consistency_score, 4),
            figure_flags=qf.inconsistency_flags,
            figure_evidence_count=figure_evidence_count,
            figure_coverage=self._figure_coverage(qf),
            claim_validation_penalty=round(qf.claim_validation_penalty, 4),
            claim_validation_bonus=round(qf.claim_validation_bonus, 4),
            qf_reasoning=qf.reasoning,
            calibrated_score=round(q5c.calibrated_score, 4),
            delta=round(q5c.delta, 4),
            delta_missing=q5c.delta_missing,
            chair_reasoning=q5c.reasoning,
            llm_verdict=q5c.llm_verdict,
            final_verdict=final.final_verdict,
            override_reason=final.override_reason,
            evidence_pool=qe.evidence_pool,
            evidence_checks={
                "Q2": q2.verified,
                "Q3": q3.verified,
                "Q4": q4.verified,
                "QF": qf.verified,
                "Q5a": q5a.verified,
                "Q5b": q5b.verified,
            },
            evidence_ids={
                "Q2": q2.evidence_id,
                "Q3": q3.evidence_id,
                "Q4": q4.evidence_id,
                "QF": qf.evidence_id,
                "Q5a": q5a.evidence_id,
                "Q5b": q5b.evidence_id,
            },
            base_score=round(base_score, 4),
            weights={k: round(v, 4) for k, v in weights.items()},
            node_score_stds=flat_stds,
            node_logs=list(self._logs),
            evaluated_at=datetime.now().isoformat(),
            llm_params_snapshot=_eval_params_snapshot(),
            citation_integrity=_citation_integrity_report(full_text),
            score_uncertainty=_score_uncertainty_report(q5c.calibrated_score, q2, q3, q4, q5c),
        )


def _eval_params_snapshot() -> dict:
    """ADR-014 P0：收集本次评测的 LLM 参数快照（种子 / 模型 / 提供商）。

    fail-open：任何异常都返回空 dict，绝不因快照失败影响主流程。
    """
    try:
        from .llm.reproducibility import get_llm_params_snapshot

        return get_llm_params_snapshot()
    except Exception:  # noqa: BLE001
        return {}


def _citation_integrity_report(full_text: str) -> dict:
    """ADR-014 P4：论文侧引用真值校验（解决 W8：此前只数引用个数、不查真伪）。

    环境开关（默认关闭，零开销、100% 向后兼容）：
      - 未设置 PAPERFORGE_CITATION_VERIFY            → 返回 {}（不计算）
      - PAPERFORGE_CITATION_VERIFY=offline          → 仅本地抽取 + 引用一致性（不触网）
      - PAPERFORGE_CITATION_VERIFY=1/true/on/yes     → 额外接 Crossref 在线核验真伪

    fail-open：任何异常（网络/解析/限流）都返回 {}，绝不阻断评测管线。
    """
    raw = os.environ.get("PAPERFORGE_CITATION_VERIFY")
    if raw is None or raw.strip() == "":
        return {}
    mode = raw.strip().lower()
    if mode in ("offline",):
        online = False
    elif mode in ("1", "true", "yes", "on"):
        online = True
    else:
        # 其他非空值视为开启离线一致性检查（安全默认）
        online = False
    try:
        from .integrity.citation_verifier import assess_citation_integrity

        return assess_citation_integrity(full_text, verify_online=online)
    except Exception as e:  # noqa: BLE001
        logger.warning("引用真值校验异常降级: %s", e)
        return {}


def _score_uncertainty_report(
    calibrated_score: float,
    q2: Any,
    q3: Any,
    q4: Any,
    q5c: Any,
) -> dict:
    """ADR-014 P2：分数不确定性（bootstrap 95% CI + 不确定门控，解决 W4 点估计无置信）。

    用各维度子分数作为 bootstrap 样本，估计整体质量评分的置信区间；
    CI 过宽时（且开启 PAPERFORGE_UNCERTAINTY_GATE）标为 needs_human_review。

    仅当 PAPERFORGE_UNCERTAINTY_GATE 开启时计算，默认返回 {}（零开销、向后兼容）。
    fail-open：任何异常返回 {}，绝不改变原有 verdict。
    """
    raw = os.environ.get("PAPERFORGE_UNCERTAINTY_GATE")
    if raw is None or raw.strip() == "":
        return {}
    gate = raw.strip().lower() in ("1", "true", "on", "yes")
    try:
        from .stats.bootstrap import uncertainty_gate

        sub_scores = [
            float(getattr(q2, "novelty_score", 0.5)),
            float(getattr(q2, "hotspot_alignment_score", 0.5)),
            float(getattr(q3, "rigor_score", 0.5)),
            float(getattr(q4, "influence_score", 0.5)),
            float(getattr(q4, "reproducibility_score", 0.5)),
        ]
        width = float(os.environ.get("PAPERFORGE_UNCERTAINTY_WIDTH", "0.15"))
        result = uncertainty_gate(
            float(calibrated_score), sub_scores, width_threshold=width, gate_enabled=gate
        )
        return result.to_dict()
    except Exception as e:  # noqa: BLE001
        logger.warning("不确定门控异常降级: %s", e)
        return {}


# ===========================================================================
# 便捷函数 —— ⚠️ evaluate_paper_v4 已废弃，请直接使用 DepthReviewer 实例
# ===========================================================================
# 保留 evaluate_paper_v4 仅供向后兼容（外部直接调用场景），内部全部走 depth_tasks


def evaluate_paper_v4(
    paper_id: str, title: str, full_text: str, abstract: str = "", hotspots: list[str] | None = None
) -> dict[str, Any]:
    """⚠️ 已废弃：请使用 DepthReviewer().review_async_dag() 或 review()。

    保留此函数仅为向后兼容（外部脚本可能直接 import 调用）。
    计划在 v5.0 中移除。
    """
    import warnings

    warnings.warn(
        "evaluate_paper_v4 已废弃，请改用 DepthReviewer().review() 或 review_async_dag()",
        DeprecationWarning,
        stacklevel=2,
    )
    reviewer = DepthReviewer()
    result = reviewer.review(
        paper_id=paper_id, title=title, full_text=full_text, abstract=abstract, hotspots=hotspots
    )
    return result.model_dump()


# ===========================================================================
# v3 兼容层（evaluate_paper / get_cached_score / _save_v3_score）
# ===========================================================================
# 这些函数被 mock_api/depth_eval.py 重新导出，供旧路由/脚本/tests 使用。
# 实现上统一走 DepthReviewer v4.1，结果写入 DepthScore 表。
# ===========================================================================


def _v3_compute_oim(paper: Any) -> float:
    """计算 v3 客观影响力指标（Objective Impact Metric），结果 ∈ [0, 1]。

    基于论文引用数、有影响力引用数和全文长度进行归一化估算。
    """
    citations = getattr(paper, "citations", 0) or 0
    influential = getattr(paper, "influential_citations", 0) or 0
    full_text = getattr(paper, "full_text", "") or ""

    # 引用分：100 引用 ≈ 0.5，封顶 1.0
    citation_score = min(1.0, citations / 200.0)
    # 有影响力引用加成
    influential_score = min(1.0, influential / 50.0)
    # 文本长度分：5000 字符 ≈ 0.3
    text_score = min(1.0, len(full_text) / 16666.0)

    # 加权综合
    score = 0.5 * citation_score + 0.3 * influential_score + 0.2 * text_score
    return round(max(0.0, min(1.0, score)), 4)


def _depthv4result_to_v3_dict(v4_result: Any, paper: Any) -> dict[str, Any]:
    """将 DepthV4Result 转换为 v3 评分字典（供 API / 缓存使用）。"""
    verdict = getattr(v4_result, "final_verdict", "major_revision")
    if verdict not in {"accept", "minor_revision", "major_revision", "reject"}:
        verdict = "major_revision"

    calibrated = float(getattr(v4_result, "calibrated_score", 0.5))
    final_score = round(calibrated * 100.0, 2)

    # 从 v4 结果提取关键词（证据池 keywords 去重）
    evidence_pool = getattr(v4_result, "evidence_pool", []) or []
    keywords: set[str] = set()
    for item in evidence_pool:
        if isinstance(item, dict):
            keywords.update(item.get("keywords", []))
        else:
            keywords.update(getattr(item, "keywords", []) or [])

    # 提取 critique / defense points
    critique_points = []
    for cp in getattr(v4_result, "critique_points", []) or []:
        if isinstance(cp, dict):
            critique_points.append(cp.get("point", ""))
        else:
            critique_points.append(getattr(cp, "point", ""))

    defense_points = []
    for dp in getattr(v4_result, "defense_points", []) or []:
        defense_points.append(str(dp))

    return {
        "paper_id": getattr(v4_result, "paper_id", getattr(paper, "id", "")),
        "title": getattr(v4_result, "title", getattr(paper, "title", "")),
        "type": getattr(v4_result, "paper_type", "B"),
        "confidence": float(getattr(v4_result, "confidence", 0.5)),
        "has_substance": bool(getattr(v4_result, "has_substance", True)),
        "expectation": float(getattr(v4_result, "expectation", 0.5)),
        "obj_score": _v3_compute_oim(paper),
        "novelty_score": float(getattr(v4_result, "novelty_score", 0.5)),
        "rigor_score": float(getattr(v4_result, "rigor_score", 0.5)),
        "influence_score": float(getattr(v4_result, "influence_score", 0.5)),
        "reproducibility_score": float(getattr(v4_result, "reproducibility_score", 0.5)),
        "calibrated_score": calibrated,
        "final_score": final_score,
        "verdict": verdict,
        "core_contribution": str(getattr(v4_result, "core_contribution", "")),
        "keywords": list(keywords),
        "missing_items": list(getattr(v4_result, "missing_items", []) or []),
        "critique_points": critique_points,
        "defense_points": defense_points,
        "chair_reasoning": str(getattr(v4_result, "chair_reasoning", "")),
        "claim_validation_penalty": float(getattr(v4_result, "claim_validation_penalty", 0.0)),
        "claim_validation_bonus": float(getattr(v4_result, "claim_validation_bonus", 0.0)),
        "content_source": "depth_v4",
    }


def _save_v3_score(db: Any, result: dict[str, Any]) -> None:
    """将 v3 评分结果持久化到 DepthScore 表。"""
    from .models import DepthScore as DepthScoreORM
    from .models import Paper as PaperORM

    paper_id = result.get("paper_id")
    if not paper_id:
        return

    # 确保对应 Paper 行存在（外键约束）
    paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not paper:
        paper = PaperORM(id=paper_id, title=result.get("title", ""))
        db.add(paper)
        db.flush()

    existing = db.query(DepthScoreORM).filter(DepthScoreORM.paper_id == paper_id).first()
    if existing:
        row = existing
    else:
        row = DepthScoreORM(paper_id=paper_id)
        db.add(row)

    row.title = result.get("title", paper.title or "")
    row.type = result.get("type", "B")
    row.confidence = float(result.get("confidence", 0.5))
    row.has_substance = bool(result.get("has_substance", True))
    row.expectation = float(result.get("expectation", 0.5))
    row.obj_score = float(result.get("obj_score", 0.0))
    row.novelty_score = float(result.get("novelty_score", 0.0))
    row.rigor_score = float(result.get("rigor_score", 0.0))
    row.influence_score = float(result.get("influence_score", 0.0))
    row.reproducibility_score = float(result.get("reproducibility_score", 0.0))
    row.calibrated_score = float(result.get("calibrated_score", 0.0))
    row.final_score = float(result.get("final_score", 0.0))
    row.verdict = result.get("verdict", "major_revision")
    row.core_contribution = result.get("core_contribution", "")
    row.keywords = list(result.get("keywords", []) or [])
    row.missing_items = list(result.get("missing_items", []) or [])
    row.critique_points = list(result.get("critique_points", []) or [])
    row.defense_points = list(result.get("defense_points", []) or [])
    row.chair_reasoning = result.get("chair_reasoning", "")
    row.content_source = result.get("content_source", "depth_v4")
    row.evaluated_at = datetime.now()

    db.commit()


def get_cached_score(db: Any, paper_id: str) -> dict[str, Any] | None:
    """读取论文的 v3 缓存评分；未缓存返回 None。"""
    from .models import DepthScore as DepthScoreORM

    row = db.query(DepthScoreORM).filter(DepthScoreORM.paper_id == paper_id).first()
    if not row:
        return None

    return {
        "paper_id": row.paper_id,
        "title": row.title,
        "type": row.type,
        "confidence": row.confidence,
        "has_substance": row.has_substance,
        "expectation": row.expectation,
        "obj_score": row.obj_score,
        "novelty_score": row.novelty_score,
        "rigor_score": row.rigor_score,
        "influence_score": row.influence_score,
        "reproducibility_score": row.reproducibility_score,
        "calibrated_score": row.calibrated_score,
        "final_score": row.final_score,
        "verdict": row.verdict,
        "core_contribution": row.core_contribution,
        "keywords": list(row.keywords or []),
        "missing_items": list(row.missing_items or []),
        "critique_points": list(row.critique_points or []),
        "defense_points": list(row.defense_points or []),
        "chair_reasoning": row.chair_reasoning,
        "content_source": row.content_source,
    }


def evaluate_paper(
    paper_id: str,
    db: Any | None = None,
    compute_mode: str | None = None,
) -> dict[str, Any]:
    """v3 兼容入口：对单篇论文执行 DEPTH v4.1 审稿并返回 v3 格式结果。

    v4.2: 缓存读写 / 评分持久化已迁移至 DataProvider，不再直接操作 SessionLocal。
    paper 查询（PaperORM）仍然走 db 参数以保持 v3 兼容。

    Args:
        paper_id: 论文 ID。
        db: 数据库会话；为 None 时自动创建 SessionLocal（仅用于 PaperORM 查询）。
        compute_mode: 算力模式（fast/speed/deep），默认 deep。

    Returns:
        v3 评分字典；失败时返回 {"error": "..."}。
    """
    from .database import SessionLocal
    from .models import Paper as PaperORM

    own_db = db is None
    if own_db:
        db = SessionLocal()

    try:
        paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()  # type: ignore[union-attr]
        if not paper:
            return {"error": f"论文 {paper_id} 不存在"}

        # v4.2: 优先通过 Provider 读缓存（解耦 SessionLocal）
        from .services.db_provider import DBDataProvider

        provider = DBDataProvider()
        cached = provider.get_cached_score(paper_id)
        if cached:
            return cached

        # 走 v4.1 审稿（缓存未命中才初始化 reviewer）
        reviewer = DepthReviewer(compute_mode=compute_mode or "deep")
        v4_result = reviewer.review(
            paper_id=paper.id,
            title=paper.title or "",
            full_text=paper.full_text or "",
            abstract=paper.abstract or "",
            paper_obj=paper,
        )
        result = _depthv4result_to_v3_dict(v4_result, paper)
        provider.save_score(paper_id, result)
        return result
    except Exception as exc:  # noqa: BLE001 - v3 兼容层：异常降级为 error 字段
        logger.exception("DEPTH evaluate_paper 失败: paper=%s", paper_id)
        return {"error": f"评估失败: {exc}"}
    finally:
        if own_db:
            db.close()  # type: ignore[union-attr]
