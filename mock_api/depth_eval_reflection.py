"""DEPTH reflection 报告轻量评审流水线。

DESIGN PHILOSOPHY
==================

论文与感悟报告是「不同维度的内容评审」，不应该用统一 prompt 强迫一个 LLM
同时输出 6 个互不相关的分数——这会丢失证据锚定、并丧失类型自适应。

保留现有 DEPTH v4.1 九节点 DAG 的纯粹性（[`depth_eval_v4.py`]），新建本模块
专门评审感悟/读后/复现类报告：

    论文 → depth_eval_v4.py            （既有 9 节点 DAG）
    报告 → depth_eval_reflection.py    （轻量单 LLM + 硬编码校验）

PIPELINE（单次 LLM 调用 + 代码层硬校验）
=======================================

  R1 → 单次 LLM 调用返回：
    - claims              3-5 个核心观点，每条带 evidence_id 引用
    - evidence_pool       从报告本体 full_text 提取 3-5 段支撑性原文片段
    - evidence_support 每个评分必须由 evidence_id 锚定（防幻觉打分）
    - analysis_depth analysis_depth analysis_depth analysis_depth
    - summary, verdict_suggestion
  R1.5 → II 维中位数采样（抑制 9B 模型措辞噪声）：
    对同一 base_prompt 用不同 seed 额外跑 2-3 次，只取 innovative_insights 的
    中位数（PAPERFORGE_REFLECTION_II_SAMPLES 默认 3，1=关闭）；UA/AD/ES 单次。
  R2 → 代码层硬校验：
    1. claims 与 evidence_pool 交叉引用 → 计算 effective_evidence_count
    2. effective_evidence_count < MIN_EVIDENCE_FOR_VALID_REVIEW (2)
       → 4 维评分上限 CAP 到 0.3 + verdict="rewrite_required"
    3. effective_evidence_count 2-4 条 → R1.5 分级帽（2→0.75/3→0.80/4→0.85）
    4. understanding_accuracy < UNDERSTANDING_THRESHOLD_DEEP (0.50)
       → verdict="needs_depth"（理解不够深）
    5. 4 维平均 < 0.65 → verdict="needs_evidence"（证据不足）
    6. 否则 → 通过 + verdict="well_done"，并附 verdict_reason 说明

EVIDENCE POOL 语义（与 v4.1 不同）
- 论文 v4.1 的"证据"指论文原文 + 章节，是 external anchoring
- 报告 reflection 的"证据"指 **报告本身** 的支撑性原文片段，
  评估 internal consistency：「报告是否用自己提出的具体引述、数据、例子
  支撑了自己的观点？」这是一个对内一致性检测，而非 cross-source 锚定。

HARDCODED FALLBACK 哲学（与 v4.1 同源）
- LLM 评估作者表达时常有 validates_confidence / flattery 偏差
- 把"实际支撑数"作为分数上限门阀：证据不足就不再让 LLM 推高分
- 代码层铁门 > 模型侪化偏差（v4.1 也是这套思路）
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .compute_config import get_compute_mode_config
from .depth_utils import clamp_float
from .json_utils import safe_json_parse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JSON 安全解析已迁移至 json_utils.py（统一实现，避免双份漂移）
# ---------------------------------------------------------------------------
# safe_json_parse / _escape_control_chars_in_strings 从 json_utils 导入，
# _INNER_QUOTE_RE / _INNER_QUOTE_PAIR_RE 内聚于 json_utils 模块。


# ---------------------------------------------------------------------------
# Reflection max_tokens 下限（防 LLM 输出被 token 限额裁断 → 引发 JSON 风暴）
# ---------------------------------------------------------------------------
# 源起：compute_mode.speed=256 / deep=512 均不足以输出含中文嵌套引号的完整
# reflection schema（claims×3-5 + evidence×3-5 + 4 维 + summary 约 1500-3000
# tokens）。硬下限 3000 匹配主流 provider 的 context budget（GPT-4 / Claude /
# GLM-4 均支持 4096+），可由 PAPERFORGE_REFLECTION_MAX_TOKENS 调高。
from .settings import get_settings

_REFLECTION_MAX_TOKENS_FLOOR = get_settings().reflection_max_tokens

# ---------------------------------------------------------------------------
# 硬编码阈值（与裁决语义对齐，调整请同步修改 docstring）
# ---------------------------------------------------------------------------

MIN_EVIDENCE_FOR_VALID_REVIEW = 2  # 至少 2 条有效证据
MIN_EVIDENCE_FOR_HIGH_SCORE = 5  # 5 条证据以上允许推高分（调严：4→5）

# snippet 真实性判定：6-gram 包含率阈值。
# 实测本地 Qwen 的「逐字引文」常有标点/个别字微差（包含率 0.98-1.00），
# 精确子串匹配会把真引文误杀 → R1 误伤整篇；编造引文通常 < 0.5。
_SNIPPET_GRAM_N = 6
_SNIPPET_CONTAINMENT_THR = 0.6


def _snippet_exists(snippet: str, full_text: str) -> bool:
    """校验 evidence snippet 是否真实出现在报告全文中。

    判定：去空白后取 snippet 的字符 6-gram，≥60% 命中报告全文即视为真实引文
    （容忍标点/个别字符差异）。过短 snippet（<6 字）退回精确子串匹配。

    修复点：prompt 要求「不能编造，必须真实出现在报告全文中」，但旧版硬校验
    只查 ID 交叉引用，LLM 编造的引文也能通过 R1。此检查补上确定性闸门。
    """
    s = re.sub(r"\s+", "", snippet or "")
    if not s:
        return False
    t = re.sub(r"\s+", "", full_text or "")
    if len(s) < _SNIPPET_GRAM_N:
        return s in t
    grams = {s[i : i + _SNIPPET_GRAM_N] for i in range(len(s) - _SNIPPET_GRAM_N + 1)}
    hits = sum(1 for g in grams if g in t)
    return hits / len(grams) >= _SNIPPET_CONTAINMENT_THR


MAX_SCORE_WHEN_EVIDENCE_INSUFFICIENT = 0.3  # 证据不足时分数上限
# R1.5 分级帽（2026-08-12 区分度修复）：有效证据 2-4 条按证据数分级封顶，
# 取代此前「证据 < 5 一律封顶 0.85」的统一帽——统一帽把 UA/ES 压成 36/41 篇同分、
# 抹平高分端区分度。2→0.75 / 3→0.80 / 4→0.85；5+ 条（MIN_EVIDENCE_FOR_HIGH_SCORE）不封顶。
# r15_graded_ab.py 离线 A/B 已验证分级帽能拉开 UA/ES 区分度。
GRADED_CAP_BY_EVIDENCE: dict[int, float] = {2: 0.75, 3: 0.80, 4: 0.85}
MAX_SCORE_WHEN_EVIDENCE_BELOW_HIGH = 0.85  # 分级帽上限（4 条证据）；未知证据数的兜底值
UNDERSTANDING_THRESHOLD_DEEP = 0.50  # 理解准确性 < 此即判 needs_depth（调严：0.40→0.50）
AVERAGE_SCORE_POOR = 0.65  # 平均 < 此即判 needs_evidence（调严：0.50→0.65）
INNOVATION_THRESHOLD = 0.45  # 创新见解 < 此即判 needs_depth（新增门阀：创新不足）

_INNOVATION_NO_MARKER_CAP = 0.50  # R4.5：无原创性标记时创新分封顶（防千问 validates_confidence）

# ======== ADR-014 P7：千问校准层（5-AI 交叉验证诊断所得） ========
# 千问偏松的三大盲区：① 对缺段报告仍打 0.85+ 高分；
# ② 对反思空洞（reflection 段 < 100 字）的 innovate_insights 仍在 0.7+；
# ③ 短篇（< 1200 字）又无证据支撑 → 但 avg 仍推高。
# 校准策略：在硬编码层（R4.5 之后）插入确定性的结构下限，防千问 validates_confidence。
# 环境开关 PAPERFORGE_QWEN_CALIBRATION=1（默认关闭，向后兼容）。
# 运行 5-AI 对比基线后，用户可用此开关调整千问评分使其接近多 AI 共识。
_QWEN_CALIBRATION_ENABLED = os.environ.get("PAPERFORGE_QWEN_CALIBRATION", "0").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
_QWEN_CALIBRATION_2SEC_CAP = 0.70  # R5：仅 2 段时全维封顶
_QWEN_CALIBRATION_3SEC_CAP = 0.50  # R5：仅 3 段时 ii 封顶
_QWEN_CALIBRATION_REFL_MIN_CHARS = 100  # R6：reflection 段字数下限
_QWEN_CALIBRATION_REFL_II_CAP = 0.40  # R6：reflection < 100 字 → ii 封顶
_QWEN_CALIBRATION_SHORT_CHARS = 1200  # R7：短篇阈值
_QWEN_CALIBRATION_SHORT_CAP = 0.70  # R7：短篇且 avg > 0.70 → 全维封顶

# 原创性标记词表。千问不看论文容易给照抄报告也打高创新分，
# 若全文独立思考痕迹不足则无论千问打多高分都封顶 0.5——
# 真正的独立思考不可能一句批判/个人判断都没有。
# 2026-08-12 收紧：拆分为「强标记」与「弱标记」。
# 强标记 = 表达个人立场/批判/质疑/对比/改进建议的词；
# 弱标记 = 描述性套话词（复述论文内容时也会自然出现，如"不足/缺点/应该/进一步"）。
# 旧规则"命中任一弱词即放行"会被复述式报告绕过（实测 41 篇中 II 高估最重的
# 10 篇全部命中弱词），故改为：强标记≥1 或 总标记≥2 才算独立思考痕迹充分。
_STRONG_ORIGINALITY_MARKERS = [
    "值得商榷",
    "有待改进",
    "值得怀疑",
    "不一定",
    "局限",
    "我认为",
    "我觉得",
    "在我看来",
    "我个人",
    "我的看法",
    "可以改进",
    "可以考虑",
    "不妨",
    "不同于",
    "与之相反",
    "换个角度",
    "另一种",
    "不同看法",
    "值得思考",
    "有意思的是",
    "令人惊讶",
    "意外",
    "没想到",
    "不认同",
    "不同意",
    "质疑",
    "商榷",
]

_WEAK_ORIGINALITY_MARKERS = [
    "不足",
    "缺点",
    "建议",
    "应该",
    "进一步",
    "后续",
    "未来",
    "下一步",
    "接下来",
]

# 兼容旧引用：全量词表 = 强 + 弱
_ORIGINALITY_MARKERS = _STRONG_ORIGINALITY_MARKERS + _WEAK_ORIGINALITY_MARKERS


def _has_originality_markers(full_text: str) -> bool:
    """报告全文的独立思考痕迹是否充分（强标记≥1 或 总标记≥2）。

    返回 False 表示全文纯复述/总结，仅靠描述性套话词（不足/缺点/应该/进一步）
    ——此时不应允许千问的 validates_confidence 偏差把创新分推高。
    2026-08-12 收紧：旧版"任一弱词即放行"被复述式报告绕过（II 高估篇全命中弱词）。
    """
    t = (full_text or "").lower()
    n_strong = sum(1 for m in _STRONG_ORIGINALITY_MARKERS if m in t)
    if n_strong >= 1:
        return True
    n_total = sum(1 for m in _ORIGINALITY_MARKERS if m in t)
    return n_total >= 2


_SECTION_MARKERS_CN = ["一、", "二、", "三、", "四、"]

# 字母格式段落标记（报告常见 a./b./c./d. 格式）
# 匹配规则：段首出现 "a." 或 "b." 等 + 紧随叙述句。
# 用完整词表而不仅仅看开头，防止误判英文摘要里的 a.。
_SECTION_MARKERS_EN = ["a. ", "b. ", "c. ", "d. "]


def _count_sections(raw_text: str) -> int:
    """统计报告含几大段标记（支持中文「一、」和英文「a.」两种格式）。

    优先中文格式；中文全 0 时启用英文格式匹配。
    """
    if not raw_text:
        return 0
    cn = sum(1 for m in _SECTION_MARKERS_CN if m in raw_text)
    if cn >= 2:
        return cn
    # 检查英文格式：a./b./c./d. 且后跟中文/英文描述（至少 5 个后续字）
    en_hits = 0
    for m in _SECTION_MARKERS_EN:
        idx = raw_text.find(m)
        if idx >= 0:
            # 确认标记后至少 5 个字符是叙述而非缩写
            rest = raw_text[idx + len(m) : idx + len(m) + 30]
            if any("\u4e00" <= c <= "\u9fff" for c in rest) or len(rest.strip()) >= 5:
                en_hits += 1
    return max(cn, en_hits)


def _reflection_section_length(raw_text: str) -> int:
    """估算 reflection 段（四、或 d. 之后）的字数。"""
    if not raw_text:
        return 0
    # 中文格式
    m = re.search(r"四、(.+)$", raw_text, re.DOTALL)
    if m:
        return len(m.group(1).strip())
    # 英文格式 d. 收获与感想
    m = re.search(r"d\.\s*(?:收获|感想|看[完后]).{0,20}\n(.+)$", raw_text, re.DOTALL)
    if m:
        return len(m.group(1).strip())
    return 0


VALID_VERDICTS = {"well_done", "needs_evidence", "needs_depth", "rewrite_required"}

# 文本截断（感悟报告通常比论文短）：超限时保留开头+结尾，中间丢弃并告知 LLM。
MAX_CHARS_FULL = 16000  # 兼容旧常量（<= 头+尾 时不截断）
MAX_CHARS_HEAD = 14000  # 保留的开头字符数
MAX_CHARS_TAIL = 2000  # 保留的结尾字符数（报告第四段感想通常在尾部）
MAX_CHARS_CLAIMS = 4000


# 原论文参考内容预览长度（2026-08 实验 B 校准口径：取论文开头 4000 字符，
# 通常含摘要+引言，足够判断报告理解是否准确、覆盖是否充分；对齐 exp_b_qwen_full.py）。
# 默认 4000（实验 B 校准口径）；可通过 PAPERFORGE_PAPER_PREVIEW_CHARS 调小以缩短
# 4 维打分 prompt、避免本机 LLM 在大论文上触发 watchdog 超时（重跑长论文批次时用
# 1500~2000 即可）。
def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """读取正整数环境变量；非法配置回退默认值，避免导入阶段崩溃。"""
    try:
        return max(int(os.getenv(name, str(default))), minimum)
    except (TypeError, ValueError):
        logger.warning("忽略非法环境变量 %s，使用默认值 %s", name, default)
        return default


def _env_ratio(name: str, default: float) -> float:
    """读取 [0,1] 比例环境变量；非法配置回退默认值。"""
    try:
        v = float(os.getenv(name, str(default)))
        return v if 0.0 <= v <= 1.0 else default
    except (TypeError, ValueError):
        logger.warning("忽略非法环境变量 %s，使用默认值 %s", name, default)
        return default


MAX_PAPER_PREVIEW_CHARS = _env_int("PAPERFORGE_PAPER_PREVIEW_CHARS", 4000)

# 动态预览预算（2026-08-11 用户需求）：感悟报告通常远短于截断上限
# （MAX_CHARS_HEAD + MAX_CHARS_TAIL = 16000 字），把报告「省下」的字数按比例
# 补给原论文预览，让 LLM 看到更多论文正文，从而更准确地判断报告的理解准确性与
# 核心要点覆盖（对论文读得越精 → 感悟报告打分越可信）。
#   - PAPERFORGE_PAPER_PREVIEW_BUDGET_RATIO：报告省下字数的补给比例
#     （默认 0.0 = 不补给，仅注入基础预览；设 0.5 可保守减半，1.0 = 全额补给）
#   - PAPERFORGE_PAPER_PREVIEW_MAX：补给后的预览硬上限（默认 16000 字，覆盖
#     一篇典型 2 万字论文的 80%）。上限须与 LLAMA_SERVER_CTX 匹配：预览+报告全文
#     +模板最坏 ≈ 25.6K 字符 ≈ 10.8K tokens，加 5000 token 输出共 ~15.8K tokens，
#     因此 ctx 默认 16384 保底；8GB 卡 + q4_0 KV 可到 24576（KV 约 0.9GB，余量充足）。
#
# 【默认 0.0 的原因（2026-08-12 实测）】本机 9B 模型（Ornstein）拿到长论文预览后
# 会把每篇报告都往高打（≥0.85），随后被 R1.5 硬帽钉住（旧版统一 0.85，现按证据数分级 0.75/0.80/0.85）→ 不同报告分数几乎
# 相同，区分度坍缩：同 4 篇报告、同温度 0.2 下，16000 字预览跨度 0.057、4000 字
# 基础预览跨度 0.150、无预览跨度 0.200。默认不再补给，仅注入基础 4000 字（保留
# UA 校准能力）；需要更高论文覆盖度时显式设 PAPERFORGE_PAPER_PREVIEW_BUDGET_RATIO=1.0。
MAX_REPORT_BUDGET_CHARS = MAX_CHARS_HEAD + MAX_CHARS_TAIL
PAPER_PREVIEW_BUDGET_RATIO = _env_ratio("PAPERFORGE_PAPER_PREVIEW_BUDGET_RATIO", 0.0)
PAPER_PREVIEW_MAX_CHARS = _env_int(
    "PAPERFORGE_PAPER_PREVIEW_MAX", 16000, minimum=MAX_PAPER_PREVIEW_CHARS
)

# II 维中位数采样（2026-08-13 措辞噪声抑制）：9B 模型（Ornstein）对同一报告的
# innovative_insights 打分有显著措辞噪声（同 prompt 不同 seed 下抖动，实测同一报告
# 差可达 0.05-0.10），单次采样会被一次随机措辞带偏 → 排名不稳定。
# 对 II 维额外跑 2-3 次（不同 seed）取中位数，只在 II 上做以控制成本（UA/AD/ES 单次）。
# PAPERFORGE_REFLECTION_II_SAMPLES=1 关闭（单次调用，向后兼容）。
REFLECTION_II_SAMPLES_DEFAULT = 3


def _reflection_ii_samples() -> int:
    """动态读取 II 中位数采样次数（默认 3，1=关闭）。每次调用现读环境变量，
    支持运行时/测试热切换（与 reflection_calibration 的开关模式一致）。"""
    return _env_int("PAPERFORGE_REFLECTION_II_SAMPLES", REFLECTION_II_SAMPLES_DEFAULT, minimum=1)


def _truncate_head_tail(
    text: str, head: int = MAX_CHARS_HEAD, tail: int = MAX_CHARS_TAIL
) -> tuple[str, bool]:
    """超限文本保留头+尾，中间丢弃。返回 (文本, 是否截断)。"""
    if not text or len(text) <= head + tail:
        return text or "", False
    return (
        text[:head] + f"\n……[报告过长，中间省略 {len(text) - head - tail} 字]……\n" + text[-tail:],
        True,
    )


# 报告类识别关键词（heuristic，老入库数据 fallback 用）
REFLECTION_SIGNAL_KEYWORDS = (
    "读后感",
    "感悟",
    "感想",
    "思考",
    "反思",
    "复现",
    "实验感悟",
    "笔记",
    "读后",
    "review",
    "thoughts",
    "notes",
    "reflection",
)


# ===========================================================================
# Pydantic 数据模型
# ===========================================================================


class ReflectionLLMOutput(BaseModel):
    """单次 LLM 调用的结构化输出。"""

    claims: list[dict] = Field(
        default_factory=list,
        description='列表，每项 {"id": "C1", "text": "...", "evidence_id": "E1"}',
    )
    evidence_pool: list[dict] = Field(
        default_factory=list,
        description='列表，每项 {"id": "E1", "snippet": "...", "claim_ref": "C1"}',
    )
    understanding_accuracy: float = Field(ge=0.0, le=1.0, default=0.5)
    analysis_depth: float = Field(ge=0.0, le=1.0, default=0.5)
    innovative_insights: float = Field(ge=0.0, le=1.0, default=0.5)
    evidence_support: float = Field(ge=0.0, le=1.0, default=0.5)
    summary: str = Field(default="", max_length=2000)
    verdict_suggestion: str = Field(default="needs_evidence")
    truncated: bool = False  # 报告超过输入上限被截断（头+尾）时 True


class ReflectionScoredOutput(BaseModel):
    """经硬编码层处理后写入数据库的最终结果。"""

    claims: list[dict]
    evidence_pool: list[dict]
    scores: dict  # 4 维 + average
    summary: str
    verdict: str
    verdict_reason: str
    effective_evidence_count: int = 0
    hardcoded_overrides: list[str] = Field(default_factory=list)


class ReflectionReviewResult(BaseModel):
    """Reflection 评审最终结果（task/DB 都使用此结构序列化）。"""

    paper_id: str
    title: str
    document_type: str = "report"
    has_content: bool = True  # 报告若无全文则 false（用于上层跳过）
    summary_short: str = ""
    # 【Layer C】LLM 输出了非空但 safe_json_parse 彻底失败 → 上游该置 status='failed'，
    # 免得 SSE 永远不发出终结事件前端陷入「卡在这里」。
    parse_failed: bool = False
    truncated: bool = False  # 报告超过输入上限，LLM 只看到头+尾
    # 本次评审实际注入 prompt 的原论文预览字数（动态预算后；0 = 未注入论文参考）。
    # 用于前端展示「分析依据了多少原文」与事后审计。
    paper_preview_chars: int = 0
    # 【可观测性】区分「LLM 故障」与「学生报告真没证据」——两者都会走到
    # R1 把四维压 0.3，但前者是系统问题、绝不能当成学生的分数。
    llm_calls: int = 0  # 实际发起的 LLM 调用次数
    llm_empty: int = 0  # 其中返回空/超时的次数（>0 即本篇分数不可信）
    # 经过硬校验后的最终结构
    claims: list[dict] = Field(default_factory=list)
    evidence_pool: list[dict] = Field(default_factory=list)
    scores: dict = Field(default_factory=dict)
    summary: str = ""
    verdict: str = "needs_evidence"
    verdict_reason: str = ""
    effective_evidence_count: int = 0
    hardcoded_overrides: list[str] = Field(default_factory=list)
    # 证据被判无效的原因分布：{"ok": n, "from_paper": n, "not_found": n}。
    # from_paper > 0 说明模型把注入的原论文当成了报告去引用（prompt 约束问题），
    # 而不是报告本身缺乏证据——两者不能都记在学生头上。
    evidence_rejections: dict = Field(default_factory=dict)
    node_logs: list[str] = Field(default_factory=list)
    # II 维中位数采样的原始样本（PAPERFORGE_REFLECTION_II_SAMPLES>1 时非空）。
    # 供离线 A/B 审计：对比单次采样 vs 中位数的措辞噪声；采样关闭时为空列表。
    ii_samples: list[float] = Field(default_factory=list)
    evaluated_at: str = ""
    # ADR-014 P2：分数不确定性（bootstrap 95% CI + 不确定门控）。解决 W4：
    # 此前只有点估计，不知道“这个分把握多大”。仅当 PAPERFORGE_UNCERTAINTY_GATE
    # 开启时填充，默认空 dict（零开销、向后兼容）。与论文侧 DepthV4Result 同构。
    score_uncertainty: dict = Field(default_factory=dict)


# ===========================================================================
# 分数不确定性（ADR-014 · P2，bootstrap 95% CI）
# ===========================================================================


def _reflection_uncertainty_report(scores: dict) -> dict:
    """ADR-014 P2：感悟报告分数不确定性（bootstrap 95% CI + 不确定门控）。

    用 4 维子分作为 bootstrap 样本，估计整体质量评分的置信区间；
    CI 过宽时（且开启 PAPERFORGE_UNCERTAINTY_GATE）标为 needs_human_review。

    仅当 PAPERFORGE_UNCERTAINTY_GATE 开启时计算，默认返回 {}（零开销、向后兼容）。
    fail-open：任何异常返回 {}，绝不改变原有 verdict（置信区间仅作附加信号）。
    与论文侧 depth_eval_v4._score_uncertainty_report 同构，复用 stats/bootstrap。
    """
    raw = os.environ.get("PAPERFORGE_UNCERTAINTY_GATE")
    if raw is None or raw.strip() == "":
        return {}
    gate = raw.strip().lower() in ("1", "true", "on", "yes")
    try:
        from .stats.bootstrap import uncertainty_gate

        dims = (
            "understanding_accuracy",
            "analysis_depth",
            "innovative_insights",
            "evidence_support",
        )
        sub_scores = [float(scores.get(d, 0.5)) for d in dims]
        avg = float(scores.get("average", 0.5))
        width = float(os.environ.get("PAPERFORGE_UNCERTAINTY_WIDTH", "0.15"))
        result = uncertainty_gate(avg, sub_scores, width_threshold=width, gate_enabled=gate)
        return result.to_dict()
    except Exception as e:  # noqa: BLE001 - 不确定门控异常隔离
        logger.warning("感悟报告不确定门控异常降级: %s", e)
        return {}


# ===========================================================================
# Prompt 模板
# ===========================================================================

PROMPT_REFLECTION = """你是一位资深学术评审人（ICLR/NeurIPS 级别），现需评审一篇学生撰写的论文感悟报告。
请像真人评审一样：先通读、形成整体印象、逐维度分析优劣，再给出分数。

报告类型：学生对某篇论文的读后感 / 复现实验的感悟 / 批判性思考（**非完整论文**）。

{paper_section}
报告全文：
{content}
{truncation_note}

【评审步骤】

第 1 步：**详细分析**（写在 JSON 之前，不限格式，自由发挥）
请从以下角度分析这篇报告的质量，引用报告中的具体内容佐证你的判断：
- 对原论文方法/结论的理解是否准确、具体？有没有关键错误或遗漏？
- 分析是否有深度？是停留在复述，还是有展开、有机制讨论、有权衡？
- 有无原创或批判性思考？是纯总结，还是有独立见解、质疑、跨领域联系？
- 观点是否有具体引述/数据/例子支撑？引述是否扎实（有具体数字、有逐条对应）？

第 2 步：**结构化输出**（在分析结束后，输出一个 JSON）
基于你上面的分析，提取核心观点和支撑证据，并按 4 维度打分。

JSON 字段说明：
- claims: 提取 3-5 个最核心的观点，每个含 id/text/evidence_id
- evidence_pool: 从报告原文中提取 3-5 段支撑片段（snippet 必须逐字复制自「报告全文」，
  禁止引用【原论文参考内容】、禁止拼接改写）。代码层会校验 snippet 是否真实出现在报告中，
  查不到的会被作废。
- understanding_accuracy(0-1): 对原论文理解的准确度与具体程度
- analysis_depth(0-1): 分析深度，对原文细节的展开与讨论程度
- innovative_insights(0-1): 原创/批判性思考的深度
- evidence_support(0-1): 观点是否有具体引述/数据/例子支撑
- summary: 1-2 句话总结报告核心内容
- verdict_suggestion: well_done | needs_evidence | needs_depth | rewrite_required

评分参照（0-1 全区间，不要集中在 0.8-0.95）：
- 差 (0.2-0.4): 明显缺陷（理解错误、无分析、无证据）
- 中 (0.5-0.7): 合格但平庸（正确但简略、有想法但未深入、有引述但零散）
- 好 (0.8-1.0): 优秀（准确具体、深入展开、独立见解、证据扎实）

输出格式（先写分析，再输出 JSON）：

[你的详细分析评语…]

{{
  "claims": [{{"id": "C1", "text": "…", "evidence_id": "E1"}}],
  "evidence_pool": [{{"id": "E1", "snippet": "…", "claim_ref": "C1"}}],
  "understanding_accuracy": 0.0,
  "analysis_depth": 0.0,
  "innovative_insights": 0.0,
  "evidence_support": 0.0,
  "summary": "…",
  "verdict_suggestion": "well_done"
}}
"""

# 原论文参考区块（仅当 review() 收到 paper_text 时注入）：
# 语义：论文仅作背景对照，用于判断报告理解是否准确、覆盖是否充分；
#       evidence snippet 仍必须来自报告本身（硬校验在 R2 强制）。
# paper_supplement：全文补充（全局摘要 + 关键句），由 reflection_pipeline 构建；
#       非空时插入 paper_preview 前，提供论文全文高密度上下文。
PAPER_SECTION_TEMPLATE = """【原论文参考内容】（⚠️ 只读背景材料，用于判断报告的理解是否准确、
覆盖是否充分。**本区块的任何句子都不得作为 evidence snippet 引用**——
它不是学生写的内容，引用它会被代码层判定为无效证据）：
{paper_supplement}
{paper_preview}
"""


# ===========================================================================
# 重试辅助
# ===========================================================================

# 证据不足重试：LLM 首次只产出 < MIN_EVIDENCE_FOR_VALID_REVIEW 条有效证据
# （本地量化模型常见波动：3-5 条要求却只给 1-2 条 → R1 把整篇压到 0.3 的误伤）。
# 仅对「解析成功但证据不足」的报告追加一次定向重试，要求补齐 3-5 条逐字引文。
RETRY_EVIDENCE_TEMPLATE_FROM_PAPER = (
    "\n\n【引证来源修正要求】你的证据片段有一部分摘自【原论文参考内容】而不是学生报告"
    "——那是背景材料，不能当证据。"
    "{diagnosis}"
    "请重新输出完整 JSON：claims 3-5 个、evidence_pool 3-5 段（snippet 必须与报告原文逐字一致，"
    "包括标点，不得改写或编造）、4 维分数、summary、verdict_suggestion。不要任何解释，直接输出 JSON。"
)
RETRY_EVIDENCE_TEMPLATE = (
    "\n\n【证据不足修正要求】你上一次只输出了 {n} 条有效证据（snippet 必须逐字出现在报告原文中）。"
    "{diagnosis}"
    "请重新输出完整 JSON：claims 3-5 个、evidence_pool 3-5 段（snippet 必须与报告原文逐字一致，"
    "包括标点，不得改写或编造）、4 维分数、summary、verdict_suggestion。不要任何解释，直接输出 JSON。"
)

# 逐条告诉模型「上次错在哪」，比只报一个数字有效得多。
# 2026-08-05 实测：113 号报告首轮把【原论文参考内容】里的英文句子当证据引用
# （报告全文命中率 0.00）→ 有效证据 1 条 → R1 把四维压到 0.3；
# 同样输入重试一次却拿到 0.85——纯粹是采样掷硬币。定向反馈就是为了压住这个抖动。
_REJECT_HINTS = {
    "from_paper": (
        "其中 {n} 条摘自【原论文参考内容】而不是学生报告"
        "（例：{sample}）——那是背景材料，绝对不能当证据。"
    ),
    "not_found": (
        "其中 {n} 条在报告全文里逐字查不到（例：{sample}）——多半是被你改写或拼接过，请原样复制。"
    ),
}

RETRY_SUFFIX_TEMPLATE = (
    "\n\n【JSON 格式修正要求 — 第 {retry_num} 次重试】\n"
    "你上次的输出无法被解析为合法 JSON。请严格遵循以下格式规则：\n"
    "1. 输出必须是**纯 JSON**，不要用 ```json 包裹，JSON 结束后不要有任何解释文字。\n"
    '2. 所有字符串值内的 ASCII 双引号必须转义为 \\"（例如：他说\\"你好\\"）。\n'
    '3. 连续双引号 "" 是非法 JSON，必须写成 \\\\"\\\\"。\n'
    "4. 确保所有花括号 {{}} 和方括号 [] 成对匹配，最后不要有尾随逗号。\n"
    "请重新输出**纯 JSON 结果**，不要添加任何前缀或后缀。"
)


def _build_retry_prompt(original_prompt: str, retry_num: int) -> str:
    """在原始 prompt 末尾拼接 JSON 格式修正要求。"""
    suffix = RETRY_SUFFIX_TEMPLATE.format(retry_num=retry_num)
    return original_prompt.rstrip() + suffix


def diagnose_evidence_rejections(
    evidence_pool: list[dict], full_text: str, paper_text: str = ""
) -> dict:
    """逐条判断 evidence 为什么被判无效，区分「引错来源」和「凭空编造」。

    - ok：snippet 确实出现在报告里
    - from_paper：报告里没有，但出现在原论文里 → 模型把背景材料当成了报告
    - not_found：两边都查不到 → 改写、拼接或编造

    这两种失败的处置完全不同：前者是 prompt 没约束住模型，重试给出明确
    反馈基本能纠正；后者才可能是报告本身的问题。以前它们在日志里长得一模一样。
    """
    counts = {"ok": 0, "from_paper": 0, "not_found": 0}
    samples: dict[str, str] = {}
    for ev in evidence_pool or []:
        snippet = (ev.get("snippet") or "").strip()
        if not snippet:
            continue
        if full_text and _snippet_exists(snippet, full_text):
            counts["ok"] += 1
            continue
        kind = "from_paper" if paper_text and _snippet_exists(snippet, paper_text) else "not_found"
        counts[kind] += 1
        samples.setdefault(kind, snippet[:40])
    return {"counts": counts, "samples": samples}


def build_evidence_diagnosis_text(diag: dict) -> str:
    """把拒绝原因拼成一句给模型看的中文反馈；无可报告内容时返回空串。"""
    counts = diag.get("counts") or {}
    samples = diag.get("samples") or {}
    parts = [
        _REJECT_HINTS[kind].format(n=counts[kind], sample=samples.get(kind, ""))
        for kind in ("from_paper", "not_found")
        if counts.get(kind)
    ]
    return ("".join(parts) + "\n") if parts else ""


# ===========================================================================
# ReflectionReviewer —— 单次调用 + 硬校验
# ===========================================================================


class ReflectionReviewer:
    """感悟报告轻量评审器。

    使用方式：
        reviewer = ReflectionReviewer()
        result = reviewer.review(paper_id, title, full_text)
    """

    def __init__(self, llm_func: Callable[[str], str] | None = None):
        self._raw_llm = llm_func or call_llm
        self._logs: list[str] = []
        self._llm_calls = 0
        self._llm_empty = 0
        # 证据被判无效的原因分布（{"ok","from_paper","not_found"} → 条数）。
        # 只在触发证据不足重试时填充，用于事后区分「模型引错来源」和「报告真没料」。
        self._evidence_rejections: dict[str, int] = {}
        # II 维中位数采样的原始样本（每次 review 重置；采样关闭时为空）。
        self._ii_samples: list[float] = []
        # 注：_temperature / _max_tokens 不在 __init__ 里缓存 —— call_llm 直接从
        # compute_mode 读 cfg 并与 _REFLECTION_MAX_TOKENS_FLOOR 合并。

    def _log(self, msg: str) -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        entry = f"[{ts}] {msg}"
        self._logs.append(entry)
        logger.info(msg)

    def _llm(self, prompt: str) -> str:
        """LLM 调用埋点：统计次数与空返回，供上层判定分数是否可信。

        call_llm 在超时/失败时返回 None（fail-open），若不统计，
        「LLM 挂了」与「学生报告确实没证据」在结果里长得一模一样
        —— 两者都是 R1 触发、四维 0.3。
        """
        self._llm_calls += 1
        raw = self._raw_llm(prompt) or ""
        if not raw:
            self._llm_empty += 1
        return raw

    def _sample_ii_median(self, base_prompt: str, primary_ii: float) -> float:
        """对 II 维做多次采样取中位数，抑制 9B 模型的措辞噪声。

        用不同 seed 重跑 base_prompt（与主调用同一 prompt），仅取每次的
        innovative_insights 原始分。空返回/解析失败的样本跳过（fail-open：
        样本不足时退回主调用值，绝不因采样把分数变成假值）。

        只有 II 维采样：UA/AD/ES 仍单次调用，控制成本（II 是措辞噪声最重、
        且权重最高 0.35 的维度）。
        """
        from statistics import median

        from .llm.reproducibility import eval_seed_override, get_eval_seed

        n = _reflection_ii_samples()
        if n <= 1:
            return primary_ii
        base_seed = get_eval_seed()
        ii_vals = [float(primary_ii)]
        for i in range(1, n):
            # 有全局种子则在其上递增；无种子则用显式递增种子（强制采样确定性）。
            sample_seed = (base_seed + i) if base_seed is not None else i
            with eval_seed_override(sample_seed):
                raw = self._llm(base_prompt)
            if not raw:
                self._log(f"[II采样] #{i + 1} LLM 空返回，跳过")
                continue
            data = safe_json_parse(raw, logger) or {}
            if "innovative_insights" not in data:
                self._log(f"[II采样] #{i + 1} 解析失败，跳过")
                continue
            try:
                ii_vals.append(clamp_float(data.get("innovative_insights", primary_ii)))
            except (TypeError, ValueError):
                self._log(f"[II采样] #{i + 1} 非法 II 值，跳过")
                continue
        med = round(float(median(ii_vals)), 4)
        # 持久化原始样本（含主调用值），供 orn_review 落盘与离线 A/B 审计
        self._ii_samples = [round(float(v), 4) for v in ii_vals]
        if len(ii_vals) > 1:
            self._log(
                f"[II采样] n={len(ii_vals)}/{n}，innovative_insights 原始样本={ii_vals}，"
                f"取中位数={med}"
            )
        return med

    # ------------------------------------------------------------------
    # 硬校验（核心逻辑）
    # ------------------------------------------------------------------
    @staticmethod
    def _cross_validate_evidence(
        claims: list[dict], evidence_pool: list[dict], full_text: str = ""
    ) -> tuple[int, list[dict], list[dict]]:
        """校验 claims 与 evidence_pool 的交叉引用（+ snippet 真实性）。

        full_text 非空时额外校验：每条 evidence 的 snippet 必须真实出现在报告
        全文中（去空白后的子串匹配），编造的引文不计为有效证据。

        Returns:
            (effective_evidence_count, validated_claims, validated_pool)
        """
        valid_eids = set()
        for ev in evidence_pool:
            eid = (ev.get("id") or "").strip()
            if not eid:
                continue
            if full_text and not _snippet_exists(ev.get("snippet", ""), full_text):
                continue  # 引文未真实出现在报告中 → 无效
            valid_eids.add(eid)
        valid_cids = {c.get("id", "").strip() for c in claims if c.get("id")}

        # effective = 被至少一个 claim 引用的、snippet 真实出现的去重 evidence 条数。
        # 旧版用 max(forward, backward) —— LLM 把 5 个 claim 全指向同一条真 evidence
        # 时 effective=5，可绕过 R1 / R1.5 门阀。现改为去重计数，一条真证据就是一条。
        effective_eids = {
            c.get("evidence_id", "").strip()
            for c in claims
            if c.get("evidence_id", "").strip() in valid_eids
        }
        effective = len(effective_eids)

        # validated_pool 只保留被至少一个 claim 正向引用的 evidence（对齐 effective 口径）。
        # 旧版 includes backward-only evidence（claim_ref 匹配但 claim 不引用该 evidence_id），
        # 会导致 evidence_pool 里有条目但 effective=0 的口径撕裂。
        validated_claims = [c for c in claims if c.get("evidence_id", "").strip() in valid_eids]
        validated_pool = [
            ev
            for ev in evidence_pool
            if (ev.get("claim_ref") or "").strip() in valid_cids
            and (ev.get("id") or "").strip() in valid_eids
            and (ev.get("id") or "").strip() in effective_eids
        ]
        return effective, validated_claims, validated_pool

    @classmethod
    def _apply_hardcoded_validation(
        cls,
        claims: list[dict],
        evidence_pool: list[dict],
        scores: dict[str, float],
        llm_verdict: str,
        summary: str,
        full_text: str = "",
        *,
        override_section_count: int | None = None,
        override_reflection_length: int | None = None,
    ) -> ReflectionScoredOutput:
        """应用硬编码校验规则，输出最终结果。

        规则优先级（从上到下，后面的规则可叠加，但 verdict 一旦被强制就锁死）：
        1. (R1) effective_evidence_count < MIN_EVIDENCE_FOR_VALID_REVIEW (2)
           → 4 维分数全部上限 CAP 到 0.3 + verdict=rewrite_required
        2. (R1.5) effective_evidence 2-4 条 → 分级封顶（2→0.75 / 3→0.80 / 4→0.85）
        3. (R2) understanding < 0.50 → verdict=needs_depth
        4. (R3) 4 维平均 < 0.65 → verdict=needs_evidence
        5. (R4) innovative < 0.45 → verdict=needs_depth
        6. (R4.5) innovative > 0.50 但无原创标记 → 封顶 0.50
        7. (P7 - 千问校准层，需 PAPERFORGE_QWEN_CALIBRATION=1)：
           R5: 报告仅 2 段 → 全维封顶 0.70；3 段 → ii 封顶 0.50
           R6: reflection 段 < 100 字 → ii 封顶 0.40
           R7: 短篇 (< 1200 字) 且 avg > 0.70 → 全维封顶 0.70
        """
        overrides: list[str] = []
        effective, v_claims, v_pool = cls._cross_validate_evidence(claims, evidence_pool, full_text)

        out_scores: dict[str, float] = {
            k: round(max(0.0, min(1.0, float(v))), 4)
            for k, v in scores.items()
            if k
            in (
                "understanding_accuracy",
                "analysis_depth",
                "innovative_insights",
                "evidence_support",
            )
        }

        verdict = llm_verdict if llm_verdict in VALID_VERDICTS else "needs_evidence"
        rule_1_fired = False

        # ── 规则 1：硬性证据门阀 ──
        if effective < MIN_EVIDENCE_FOR_VALID_REVIEW:
            for k in list(out_scores.keys()):
                out_scores[k] = min(out_scores[k], MAX_SCORE_WHEN_EVIDENCE_INSUFFICIENT)
            verdict = "rewrite_required"
            overrides.append(
                f"R1: 有效证据数 {effective} < 阈值 {MIN_EVIDENCE_FOR_VALID_REVIEW}，"
                f"所有分数 CAP 至 {MAX_SCORE_WHEN_EVIDENCE_INSUFFICIENT}, verdict=rewrite_required"
            )
            rule_1_fired = True

        # ── 规则 1.5：证据不足以推高分（分级帽）──
        # 有效证据 >= 2 但 < 5 → 4 维分数按证据数分级封顶（2→0.75/3→0.80/4→0.85），
        # 防止证据不足却拿满分，同时避免统一 0.85 帽把高分端压平（UA/ES 曾 36/41 篇同分）。
        if not rule_1_fired and effective < MIN_EVIDENCE_FOR_HIGH_SCORE:
            cap = GRADED_CAP_BY_EVIDENCE.get(effective, MAX_SCORE_WHEN_EVIDENCE_BELOW_HIGH)
            capped_any = False
            for k in list(out_scores.keys()):
                if out_scores[k] > cap:
                    out_scores[k] = cap
                    capped_any = True
            if capped_any:
                overrides.append(f"R1.5: 有效证据数 {effective} 条，分数分级 CAP 至 {cap}")

        # ── 规则 2：理解深度不足（仅在规则 1 未触发时，因为 R1 会把 understanding 也降到 0.3）──
        # 注意 R1 触发后 understanding 已被压到 ≤ 0.3，必然 < 0.4；为避免重复触发，
        # 仅当 R1 未触发时才检查 R2
        if not rule_1_fired and (
            out_scores.get("understanding_accuracy", 0.5) < UNDERSTANDING_THRESHOLD_DEEP
        ):
            verdict = "needs_depth"
            overrides.append(
                f"R2: 理解准确性 {out_scores.get('understanding_accuracy', 0):.2f} "
                f"< 阈值 {UNDERSTANDING_THRESHOLD_DEEP}, verdict=needs_depth"
            )

        # ── 规则 3：平均分过低（信息性提示，不强制覆盖 verdict，因为 R2/R1 可能已经设了）──
        avg = sum(out_scores.values()) / max(len(out_scores), 1) if out_scores else 0.0
        if avg < AVERAGE_SCORE_POOR and verdict not in ("rewrite_required", "needs_depth"):
            verdict = "needs_evidence"
            overrides.append(
                f"R3: 平均分 {avg:.2f} < 阈值 {AVERAGE_SCORE_POOR}, verdict=needs_evidence"
            )

        # ── 规则 4：创新见解不足（新增门阀：创新是独立思考的核心指标）──
        # 创新见解 < 0.45 → verdict=needs_depth（即使其他维度高，缺乏创新也应降级）
        if not rule_1_fired and verdict not in ("rewrite_required", "needs_depth"):
            innovation = out_scores.get("innovative_insights", 0.5)
            if innovation < INNOVATION_THRESHOLD:
                verdict = "needs_depth"
                overrides.append(
                    f"R4: 创新见解 {innovation:.2f} < 阈值 {INNOVATION_THRESHOLD}, "
                    f"verdict=needs_depth（创新不足，缺乏独立思考）"
                )
        # ── 规则 4.5：创新分高但无原创性标记 → 封顶 0.5（千问 validates_confidence 偏差防护）──
        # 千问不看论文原文，容易把照抄/空泛报告的创新分也给到 0.7+。
        # 若报告全文没有任何独立见解标记（批判性词/个人判断/局限讨论/改进建议），
        # 即使千问给分高也封顶——真正的独立思考不可能隐身在纯粹复述中。
        innovation = out_scores.get("innovative_insights", 0.5)
        if not rule_1_fired and innovation > _INNOVATION_NO_MARKER_CAP and full_text:
            if not _has_originality_markers(full_text):
                out_scores["innovative_insights"] = _INNOVATION_NO_MARKER_CAP
                overrides.append(
                    f"R4.5: 创新分 {innovation:.2f} 但全文无原创性标记（批判/判断/局限/改进），"
                    f"封顶至 {_INNOVATION_NO_MARKER_CAP}"
                )

        # ── ADR-014 P7：千问校准层（5-AI 交叉验证诊断）──
        # 当 PAPERFORGE_QWEN_CALIBRATION=1 时启用，在 R4.5 之后施加结构性下限。
        # 这些规则**不覆盖 R1**（证据门阀优先），但可与 R1.5/R2/R3/R4/R4.5 叠加。
        # 宗旨：千问对「缺段/空反思/短篇」的 validates_confidence 偏差过高，
        # 用确定性规则封顶使其接近多 AI 中位数基线。
        # 优先使用调用方预解析的 section_count / ref_len（docx parser 更可靠）；
        # 未提供时 fallback 扫描 raw_text。
        if _QWEN_CALIBRATION_ENABLED and full_text:
            section_count = (
                override_section_count
                if override_section_count is not None
                else _count_sections(full_text)
            )
            ref_len = (
                override_reflection_length
                if override_reflection_length is not None
                else _reflection_section_length(full_text)
            )
            total_chars = len(full_text)
            calib_any = False

            # R5：结构完整性约束。报告应含 4 段（一、~ 四、）。
            #   - 仅 2 段：回退到多 AI 基线观察（千问在此给 0.85+、基线 ~0.63）→ 全维封顶 0.70。
            #   - 仅 3 段（通常缺 reflection）：ii 封顶 0.50（不触发时 R4 已有 ii 门阀）。
            if not rule_1_fired:
                if section_count <= 1 and total_chars > 100:
                    # 无任何段落标记：极不规范的报告
                    for k in ("understanding_accuracy", "analysis_depth"):
                        if out_scores.get(k, 0) > _QWEN_CALIBRATION_2SEC_CAP:
                            out_scores[k] = _QWEN_CALIBRATION_2SEC_CAP
                            calib_any = True
                    overrides.append(
                        f"R5(P7): 报告无段落标记（非标报告），understanding & analysis 封顶至 {_QWEN_CALIBRATION_2SEC_CAP}"
                    )
                elif section_count <= 2:
                    for k in list(out_scores.keys()):
                        if out_scores[k] > _QWEN_CALIBRATION_2SEC_CAP:
                            out_scores[k] = _QWEN_CALIBRATION_2SEC_CAP
                            calib_any = True
                    if calib_any:
                        overrides.append(
                            f"R5(P7): 报告仅 {section_count} 段（应 4 段），"
                            f"所有维度封顶至 {_QWEN_CALIBRATION_2SEC_CAP}"
                        )
                        # 缺段 → 证据也必然不足 → verdict 至少 needs_evidence
                        if verdict == "well_done":
                            verdict = "needs_evidence"
                elif section_count <= 3:
                    ii_val = out_scores.get("innovative_insights", 0)
                    if ii_val > _QWEN_CALIBRATION_3SEC_CAP:
                        out_scores["innovative_insights"] = _QWEN_CALIBRATION_3SEC_CAP
                        calib_any = True
                        overrides.append(
                            f"R5(P7): 报告仅 {section_count} 段（缺少完整反思），"
                            f"innovative_insights 封顶至 {_QWEN_CALIBRATION_3SEC_CAP}"
                        )

            # R6：reflection 段质量约束。reflection 是报告的核心价值所在；
            #   字数 < 100 → 基本是空的；封顶 ii 到 0.40。
            if (
                not rule_1_fired
                and ref_len >= 0
                and ref_len < _QWEN_CALIBRATION_REFL_MIN_CHARS
                and section_count >= 4
            ):
                ii_val = out_scores.get("innovative_insights", 0)
                if ii_val > _QWEN_CALIBRATION_REFL_II_CAP:
                    out_scores["innovative_insights"] = _QWEN_CALIBRATION_REFL_II_CAP
                    calib_any = True
                    overrides.append(
                        f"R6(P7): reflection段仅 {ref_len} 字（< {_QWEN_CALIBRATION_REFL_MIN_CHARS}），"
                        f"innovative_insights 封顶至 {_QWEN_CALIBRATION_REFL_II_CAP}"
                    )
                    if verdict == "well_done":
                        verdict = "needs_depth"

            # R7：结构-篇幅不匹配约束。
            #   短篇（< 1200 字）且 avg > 0.70 → 千问只看字数没读结构 → 全维封顶。
            if not rule_1_fired and total_chars < _QWEN_CALIBRATION_SHORT_CHARS:
                if avg > _QWEN_CALIBRATION_SHORT_CAP:
                    for k in list(out_scores.keys()):
                        if out_scores[k] > _QWEN_CALIBRATION_SHORT_CAP:
                            out_scores[k] = _QWEN_CALIBRATION_SHORT_CAP
                            calib_any = True
                    if calib_any:
                        overrides.append(
                            f"R7(P7): 报告仅 {total_chars} 字（< {_QWEN_CALIBRATION_SHORT_CHARS}），"
                            f"所有维度封顶至 {_QWEN_CALIBRATION_SHORT_CAP}"
                        )

        # 【修复】原代码中还存在「verdict=needs_evidence 但平均尚可 → 升回 well_done」的逆向逻辑。
        # 该路径不可复现、非幂等（取决于 overrides 是否为空），会迷惑后续维护者。
        # 现已删除：硬编码层仅负责向下封闭，不反向覆盖 LLM 的合理建议。

        out_scores["average"] = round(avg, 4)

        verdict_reason = _build_verdict_reason(verdict, out_scores, effective, overrides)

        return ReflectionScoredOutput(
            claims=v_claims,
            evidence_pool=v_pool,
            scores=out_scores,
            summary=summary.strip()[:1000],
            verdict=verdict,
            verdict_reason=verdict_reason,
            effective_evidence_count=effective,
            hardcoded_overrides=overrides,
        )

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def review(
        self,
        paper_id: str,
        title: str,
        full_text: str,
        student_id: str = "",
        paper_text: str = "",
        *,
        paper_supplement: str = "",
        override_section_count: int | None = None,
        override_reflection_length: int | None = None,
    ) -> ReflectionReviewResult:
        """评审单篇报告。

        Args:
            student_id: 学号（可选，用于交叉验证加分查 _VERIFIED_PAIRS）
            paper_text: 原论文全文（可选）。非空时把论文开头 preview 字符作为
                【原论文参考内容】注入 prompt，供千问对照核验报告理解准确性
                （2026-08 实验 B：千问拿到论文后 understanding/evidence 判得更准，
                但分数整体上浮——权重重构已把区分度不足维度降权以吸收该效应）。
                预览长度 = 基础 MAX_PAPER_PREVIEW_CHARS（4000）+ 报告省下的字数 ×
                PAPER_PREVIEW_BUDGET_RATIO（默认 0.0 = 不补给，仅基础预览；
                2026-08-12 实测 16000 字长预览压死本机 9B 模型判断力 → 区分度坍缩，
                见模块顶部注释），硬上限 PAPER_PREVIEW_MAX_CHARS（16000），
                均可经环境变量调整；上限需与 LLAMA_SERVER_CTX 匹配（见 ADR-014 显存预算表）。
                不传时 prompt 与旧版完全一致。
            paper_supplement: 论文全文补充文本（可选）。非空时插入【原论文参考内容】前，
                包含全文全局摘要、关键句等（由 build_fulltext_context 产出）。
                paper_text 仍用于 diagnose_evidence_rejections 的 snippet 来源判断。
            override_section_count: 预解析的段落数（来自 docx parser），
                传 None 时 fallback 扫描 raw_text。
            override_reflection_length: 预解析的 reflection 段字数（来自 docx parser）。
        """
        self._logs = []
        self._llm_calls = 0
        self._llm_empty = 0
        self._evidence_rejections = {}
        self._ii_samples = []
        self._log(f"======== DEPTH reflection 评审开始: {paper_id} ========")

        if not full_text or not full_text.strip():
            self._log("报告 full_text 为空，跳过评审")
            return ReflectionReviewResult(
                paper_id=paper_id,
                title=title,
                has_content=False,
                summary_short="报告内容为空，无法评审",
                verdict="rewrite_required",
                verdict_reason="报告内容为空（full_text 为空或不可用）",
                evaluated_at=datetime.now().isoformat(timespec="seconds"),
                node_logs=list(self._logs),
            )

        # 1. LLM 调用 + JSON 解析（含重试：初始 + 1 次重试，共 2 次）
        MAX_RETRIES = 1  # 初始调用后最多重试 1 次
        content, truncated = _truncate_head_tail(full_text)
        truncation_note = (
            "\n【注意】报告超过输入上限已被截断为「开头+结尾」两部分，"
            "请仅基于可见内容评审，且 evidence snippet 必须来自可见部分。"
            if truncated
            else ""
        )
        # 原论文参考（可选注入）：取论文开头 preview_chars 字符。
        # 动态预算（2026-08-11 引入；2026-08-12 默认改为 0.0）：报告通常远短于
        # 16000 字截断上限，可把报告「省下」的字数按 PAPER_PREVIEW_BUDGET_RATIO
        # 补给论文预览（硬上限 PAPER_PREVIEW_MAX_CHARS）；但实测长预览会压死本机
        # 9B 模型判断力（区分度坍缩），故默认不补给、仅注入基础预览；有全文补充
        # （paper_supplement）时仍保留 2000 字基础预览，避免与 supplement
        # （≈7000 字）叠加后 prompt 过长。（snippet 仍强制来自报告）
        paper_section = ""
        paper_preview_chars = 0  # 实际注入的原论文预览字数（0 = 未注入论文参考）
        if paper_text and paper_text.strip():
            supp = (paper_supplement or "").strip()
            base_preview = min(MAX_PAPER_PREVIEW_CHARS, 2000) if supp else MAX_PAPER_PREVIEW_CHARS
            saved = max(0, MAX_REPORT_BUDGET_CHARS - len(content))
            preview_chars = min(
                PAPER_PREVIEW_MAX_CHARS,
                base_preview + int(saved * PAPER_PREVIEW_BUDGET_RATIO),
            )
            paper_preview_chars = preview_chars
            self._log(
                f"原论文预览注入: preview={preview_chars} 字"
                f"（报告 {len(content)} 字，省下 {saved} 字 × "
                f"{PAPER_PREVIEW_BUDGET_RATIO:.0%}"
                f"{'，含全文补充' if supp else ''}）"
            )
            paper_section = PAPER_SECTION_TEMPLATE.format(
                paper_supplement=supp,
                paper_preview=paper_text[:preview_chars],
            )
        base_prompt = PROMPT_REFLECTION.format(
            content=content,
            truncation_note=truncation_note,
            paper_section=paper_section,
        )
        prompt = base_prompt
        data: dict[str, Any] = {}
        parse_failed = False

        for attempt in range(MAX_RETRIES + 1):
            raw = self._llm(prompt)
            if not raw:
                self._log(f"[R1] LLM 返回空 (attempt {attempt + 1}/{MAX_RETRIES + 1})")
                data = {}
                parse_failed = True
                if attempt < MAX_RETRIES:
                    prompt = _build_retry_prompt(base_prompt, attempt + 1)
                    self._log(f"[R1] 触发重试 {attempt + 1}/{MAX_RETRIES}")
                    continue
                break

            data = safe_json_parse(raw, logger)
            has_useful = bool(
                data.get("claims")
                or data.get("evidence_pool")
                or data.get("summary")
                or ("understanding_accuracy" in data)
            )
            if has_useful:
                parse_failed = False
                self._log(f"[R1] 解析成功 (attempt {attempt + 1}), 字段: {list(data.keys())}")
                break

            self._log(
                f"[R1] safe_json_parse 解析失败 (attempt {attempt + 1}/{MAX_RETRIES + 1}, "
                f"raw={len(raw)} chars)"
            )
            if attempt < MAX_RETRIES:
                prompt = _build_retry_prompt(base_prompt, attempt + 1)
                self._log(f"[R1] 触发重试 {attempt + 1}/{MAX_RETRIES}")
            else:
                parse_failed = True
                self._log("[R1] 已达最大重试次数，标记 parse_failed=True")

        # 安全提取 / 防御性默认值
        llm_output = ReflectionLLMOutput(
            claims=data.get("claims") or [],
            evidence_pool=data.get("evidence_pool") or [],
            understanding_accuracy=clamp_float(data.get("understanding_accuracy", 0.5)),
            analysis_depth=clamp_float(data.get("analysis_depth", 0.5)),
            innovative_insights=clamp_float(data.get("innovative_insights", 0.5)),
            evidence_support=clamp_float(data.get("evidence_support", 0.5)),
            summary=str(data.get("summary", "")).strip(),
            verdict_suggestion=str(data.get("verdict_suggestion", "needs_evidence")),
        )

        self._log(
            f"R1 返回: claims={len(llm_output.claims)}, "
            f"evidence={len(llm_output.evidence_pool)}, "
            f"verdict_suggestion={llm_output.verdict_suggestion}"
        )

        # 1.5 II 维中位数采样（抑制 9B 模型措辞噪声）：仅解析成功时额外采样。
        # median_ii 同时用于覆盖后续证据重试轮次的 II 原始分，保证同一篇报告的
        # II 始终来自同一中位数口径，而非「首轮中位数 / 重试单次」混用。
        median_ii: float | None = None
        if not parse_failed and _reflection_ii_samples() > 1:
            median_ii = self._sample_ii_median(base_prompt, llm_output.innovative_insights)
            llm_output.innovative_insights = median_ii

        # 2. 硬编码校验
        scores_in = {
            "understanding_accuracy": llm_output.understanding_accuracy,
            "analysis_depth": llm_output.analysis_depth,
            "innovative_insights": llm_output.innovative_insights,
            "evidence_support": llm_output.evidence_support,
        }
        scored = self._apply_hardcoded_validation(
            claims=llm_output.claims,
            evidence_pool=llm_output.evidence_pool,
            scores=scores_in,
            llm_verdict=llm_output.verdict_suggestion,
            summary=llm_output.summary,
            full_text=full_text,
            override_section_count=override_section_count,
            override_reflection_length=override_reflection_length,
        )

        # 2.1 证据不足定向重试（本地量化模型波动：好报告也常只给 1-2 条证据。
        # 2026-08 补：模型引错来源（from_paper>0）也触发重试——证据来自原论文而非
        # 学生报告，不纠正等于把系统问题算成学生证据不足。）
        _pre_rej = diagnose_evidence_rejections(llm_output.evidence_pool, full_text, paper_text)
        _retry_ev_count = scored.effective_evidence_count < MIN_EVIDENCE_FOR_VALID_REVIEW
        _retry_from_paper = _pre_rej["counts"].get("from_paper", 0) > 0
        _need_retry = not parse_failed and (_retry_ev_count or _retry_from_paper)
        if _need_retry:
            # 先弄清楚上一轮到底错在哪，把具体原因写进重试提示。
            # 只报「你才给了 1 条」模型往往原样再来一遍；
            # 告诉它「你那条摘自原论文」才纠得回来。
            rej = _pre_rej
            self._evidence_rejections = rej["counts"]
            # from_paper 优先用来源修正模板（即使同时也证据不足——根源是引错来源，
            # 告诉模型"你那条不是报告里的"比"你只给了 N 条"更能纠偏）。
            _retry_tmpl = (
                RETRY_EVIDENCE_TEMPLATE_FROM_PAPER if _retry_from_paper else RETRY_EVIDENCE_TEMPLATE
            )
            retry_ev = base_prompt.rstrip() + _retry_tmpl.format(
                n=scored.effective_evidence_count,
                diagnosis=build_evidence_diagnosis_text(rej),
            )
            self._log(
                f"[R1-retry] 有效证据 {scored.effective_evidence_count} < "
                f"{MIN_EVIDENCE_FOR_VALID_REVIEW}（拒绝原因 {rej['counts']}），"
                f"定向重试要求补齐 claims/evidence"
            )
            raw_ev = self._llm(retry_ev)
            data_ev = safe_json_parse(raw_ev, logger) if raw_ev else {}
            if data_ev.get("claims") or data_ev.get("evidence_pool"):
                llm_ev = ReflectionLLMOutput(
                    claims=data_ev.get("claims") or [],
                    evidence_pool=data_ev.get("evidence_pool") or [],
                    understanding_accuracy=clamp_float(data_ev.get("understanding_accuracy", 0.5)),
                    analysis_depth=clamp_float(data_ev.get("analysis_depth", 0.5)),
                    innovative_insights=clamp_float(data_ev.get("innovative_insights", 0.5)),
                    evidence_support=clamp_float(data_ev.get("evidence_support", 0.5)),
                    summary=str(data_ev.get("summary", llm_output.summary)).strip(),
                    verdict_suggestion=str(data_ev.get("verdict_suggestion", "needs_evidence")),
                )
                # 重试轮次的 II 原始分同样用中位数口径覆盖（措辞噪声抑制一致）
                if median_ii is not None:
                    llm_ev.innovative_insights = median_ii
                scored_ev = self._apply_hardcoded_validation(
                    claims=llm_ev.claims,
                    evidence_pool=llm_ev.evidence_pool,
                    scores={
                        "understanding_accuracy": llm_ev.understanding_accuracy,
                        "analysis_depth": llm_ev.analysis_depth,
                        "innovative_insights": llm_ev.innovative_insights,
                        "evidence_support": llm_ev.evidence_support,
                    },
                    llm_verdict=llm_ev.verdict_suggestion,
                    summary=llm_ev.summary,
                    full_text=full_text,
                    override_section_count=override_section_count,
                    override_reflection_length=override_reflection_length,
                )
                if scored_ev.effective_evidence_count > scored.effective_evidence_count:
                    scored = scored_ev
                    # 重试已纠正来源并达到更高有效证据数；首轮拒绝不能继续
                    # 污染最终报告，否则健康结果仍会被标成“模型引错来源”。
                    retry_diag = diagnose_evidence_rejections(
                        llm_ev.evidence_pool, full_text, paper_text
                    )
                    self._evidence_rejections = retry_diag["counts"]
                    self._log(
                        f"[R1-retry] 重试后有效证据 {scored_ev.effective_evidence_count}，"
                        f"verdict={scored_ev.verdict}"
                    )
                else:
                    # 两轮都没凑够证据：记录第二轮的拒绝原因，用来区分
                    # 「模型死活引错来源」和「报告确实没有可引的具体内容」。
                    rej2 = diagnose_evidence_rejections(llm_ev.evidence_pool, full_text, paper_text)
                    self._evidence_rejections = {
                        k: rej["counts"].get(k, 0) + rej2["counts"].get(k, 0)
                        for k in ("ok", "from_paper", "not_found")
                    }
                    self._log(
                        f"[R1-retry] 重试未改善（{scored_ev.effective_evidence_count} "
                        f"<= {scored.effective_evidence_count}），"
                        f"两轮累计拒绝原因 {self._evidence_rejections}"
                    )
            elif not raw_ev:
                # 重试调用本身空返回（超时/连接失败）→ 这一篇的 0.3 是基础设施
                # 故障的产物，不是评审结论。_llm 已计入 llm_empty，上层会作废分数。
                self._log("[R1-retry] 重试 LLM 空返回，本篇分数不可信（llm_empty+1）")

        self._log(
            f"硬校验完成: effective_evidence={scored.effective_evidence_count}, "
            f"verdict={scored.verdict}, "
            f"avg_score={scored.scores.get('average', 0):.3f}"
        )
        if scored.hardcoded_overrides:
            for ov in scored.hardcoded_overrides:
                self._log(f"  [override] {ov}")

        # 3. 交叉验证加分（自动扫描全文找出处 → arXiv API 验证 → 配对成功则加分）
        final_scores = scored.scores
        final_overrides = list(scored.hardcoded_overrides)
        # 模型引错来源导致的证据作废要单独记一笔：这是系统侧的 prompt 问题，
        # 复核时不该和「学生报告没证据」混为一谈。
        _from_paper = int(self._evidence_rejections.get("from_paper", 0) or 0)
        if _from_paper:
            final_overrides.append(
                f"R1-diag: {_from_paper} 条证据摘自原论文而非报告（模型引错来源，非学生问题）"
            )
        if student_id:
            final_scores, bonus, cv_reason = apply_crossval_bonus(
                final_scores, student_id, full_text
            )
            if cv_reason:
                self._log(f"  [crossval] {cv_reason}")
                final_overrides.append(cv_reason)
                # 加分后可能改变 verdict（average 重算）：若原 verdict 是 well_done 但加分后
                # average 仍 < AVERAGE_SCORE_POOR，则保持原 verdict（不因加分降级）；
                # 若原 verdict 是 needs_evidence 但加分后 average >= 0.50，升级为 well_done
                new_avg = final_scores.get("average", 0)
                if scored.verdict == "needs_evidence" and new_avg >= AVERAGE_SCORE_POOR:
                    scored = scored.model_copy(update={"verdict": "well_done"})
                    self._log(
                        f"  [crossval] 加分后 average={new_avg:.3f} >= {AVERAGE_SCORE_POOR}, verdict 升级为 well_done"
                    )

        self._log(f"======== DEPTH reflection 评审完成: {paper_id} ========")

        return ReflectionReviewResult(
            paper_id=paper_id,
            title=title,
            claims=scored.claims,
            evidence_pool=scored.evidence_pool,
            scores=final_scores,
            summary=scored.summary,
            verdict=scored.verdict,
            verdict_reason=scored.verdict_reason,
            effective_evidence_count=scored.effective_evidence_count,
            hardcoded_overrides=final_overrides,
            summary_short=scored.summary[:200],
            parse_failed=parse_failed,
            truncated=truncated,
            paper_preview_chars=paper_preview_chars,
            llm_calls=self._llm_calls,
            llm_empty=self._llm_empty,
            evidence_rejections=dict(self._evidence_rejections),
            ii_samples=list(self._ii_samples),
            evaluated_at=datetime.now().isoformat(timespec="seconds"),
            node_logs=list(self._logs),
            score_uncertainty=_reflection_uncertainty_report(final_scores),
        )


def _build_verdict_reason(
    verdict: str,
    scores: dict[str, float],
    effective_evidence: int,
    overrides: list[str],
) -> str:
    """组装人类可读的 verdict 解释。"""
    parts: list[str] = []
    if overrides:
        parts.append("硬编码规则触发: " + " | ".join(overrides))
    parts.append(
        f"evidence_id 有效锚定数={effective_evidence}; 4 维平均={scores.get('average', 0):.2f}"
    )
    score_str = ", ".join(f"{k}={v:.2f}" for k, v in scores.items() if k != "average")
    parts.append(f"各维度: {score_str}")
    if verdict == "well_done":
        parts.append("结论: 报告证据充实、分析有深度、观点有支撑")
    elif verdict == "needs_evidence":
        parts.append("结论: 报告分数尚可但证据不足，需补充具体引用/数据")
    elif verdict == "needs_depth":
        parts.append("结论: 报告对原文理解不够深，需进一步精读并展开分析")
    elif verdict == "rewrite_required":
        parts.append("结论: 报告几乎无有效证据支撑，建议大幅重写")
    return " | ".join(parts)


# ===========================================================================
# 交叉验证加分（论文出处 → arXiv 原论文配对验证）
# ===========================================================================
# 自动扫描报告全文找论文出处（arXiv ID/DOI/标题/作者），用 arXiv API 在线验证，
# 配对成功则给 understanding_accuracy 加分。
# 加分幅度 = 综合准确度(标题0.4+作者0.3+arxiv_id 0.3) × 0.05，封顶 +0.05。
# 无出处报告不加分（不惩罚无出处，只奖励明确出处）。

_CROSSVAL_BONUS_MAX = 0.05  # 最高加 5 分
_CROSSVAL_BONUS_DIM = "understanding_accuracy"  # 映射到 R1（理解准确性）

# 出处扫描正则模式（按优先级：arXiv ID > DOI > 标题 > 作者）
_SOURCE_PATTERNS = [
    # arXiv ID：如 "arXiv:2307.07633" / "arxiv 2307.07633" / "arXiv ID: 2307.07633"
    (r"arXiv(?:\s*ID)?[:\s]+(\d{4}\.\d{4,5})", "arxiv_id"),
    # arXiv URL：如 "https://arxiv.org/abs/2307.07633"
    (r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", "arxiv_id"),
    # DOI：如 "doi:10.1016/j.softx.2023.101532"
    (r"doi[:\s]+(10\.\d{4,}/[^\s,;\"']+)", "doi"),
    # 论文题目标签：如 "论文题目：XXX" / "标题：XXX"
    (r"(?:论文题目|论文标题|标题|题目)[:：]\s*([^\n]{8,150})", "title"),
    # 论文书名号：如 "论文《XXX》"
    (r"论文[:：\s]*《([^》]{5,150})》", "title"),
    # 作者标签：如 "作者：XXX"（排除"作者（包括）"这类正文）
    (r"(?:论文作者|原作者|作者)[:：]\s*([^\n，,。.（(]{2,80})", "author"),
]

# arXiv API 查询委托给项目的 arxiv_crawler.get_arxiv_by_id（复用既有 XML 解析逻辑）

# 已验证配对缓存（学号 → 原论文元数据）。
# 种子数据：已通过 arXiv API 验证的 8 篇，避免每次跑都联网。
_VERIFIED_CACHE: dict[str, dict] = {
    "999900000018": {  # 学生18
        "arxiv_id": "1405.3094",
        "title": "The inverted Pendulum: A fundamental Benchmark in Control Theory and Robotics",
        "authors": ["Olfa Boubaker"],
        "student_title": "The Inverted Pendulum: A fundamental Benchmark in Control Theory and Robotics",
        "student_author": "Olfa Boubaker",
        "student_arxiv": "1405.3094",
    },
    "999900000043": {  # 学生41
        "arxiv_id": "2307.07633",
        "title": "Taming the Panda with Python: A Powerful Duo for Seamless Robotics Programming and Integration",
        "authors": ["Jean Elsner"],
        "student_title": "Taming the Panda with Python: A Powerful Duo for Seamless Robotics Programming and Integration",
        "student_author": "Jean Elsner",
        "student_arxiv": "2307.07633",
    },
    "999900000030": {  # 学生29
        "arxiv_id": "2202.12391",
        "title": "HeRo 2.0: A Low-Cost Robot for Swarm Robotics Research",
        "authors": ["Paulo Rezeck", "Hector Azpurua", "Mauricio FS Correa", "Luiz Chaimowicz"],
        "student_title": "HeRo 2.0: A Low-Cost Robot for Swarm Robotics Research",
        "student_author": None,  # 学生把标题误填入作者栏
        "student_arxiv": None,
    },
    "999900000035": {  # 学生33
        "arxiv_id": "2202.08406",
        "title": "The Unboxing Experience: Exploration and Design of Initial Interactions Between Children and Social Robots",
        "authors": ["Christine P Lee", "Bengisu Cagiltay", "Bilge Mutlu"],
        "student_title": "开箱体验：儿童与社交机器人初始交互的探索与设计",
        "student_author": "Christine P. Lee, Bengisu Cagiltay, Bilge Mutlu",
        "student_arxiv": None,
    },
    "999900000040": {  # 学生38
        "arxiv_id": "2105.04642",
        "title": "SUPR-GAN: SUrgical PRediction GAN for Event Anticipation in Laparoscopic and Robotic Surgery",
        "authors": [
            "Yutong Ban",
            "Guy Rosman",
            "Jennifer A. Eckhoff",
            "Thomas M. Ward",
            "Daniel A. Hashimoto",
            "Taisei Kondo",
            "Hidekazu Iwaki",
            "Ozanan R. Meireles",
            "Daniela Rus",
        ],
        "student_title": "SUPR-GAN: SUrgical PRediction GAN for Event Anticipation in Laparoscopic and Robotic Surgery",
        "student_author": None,
        "student_arxiv": None,
    },
    "999900000004": {  # 学生05
        "arxiv_id": "2401.14478",
        "title": "Toward Family-Robot Interactions: A Family-Centered Framework in HRI",
        "authors": ["Bengisu Cagiltay", "Bilge Mutlu"],
        "student_title": "Toward Family-Robot Interactions",
        "student_author": "Bengisu Cagiltay, Bilge Mutlu",
        "student_arxiv": None,
    },
    "999900000019": {  # 学生19
        "arxiv_id": "2403.18721",
        "title": "PhysicsAssistant: An LLM-Powered Interactive Learning Robot for Physics Lab Investigations",
        "authors": ["Ehsan Latif", "Ramviyas Parasuraman", "Xiaoming Zhai"],
        "student_title": "PhysicsAssistant：面向物理实验探究的大语言模型驱动交互式学习机器人",
        "student_author": None,
        "student_arxiv": None,
    },
    "999900000001": {  # 学生01
        "arxiv_id": "0706.2974",
        "title": "Remote laboratories: new technology and standard based architecture",
        "authors": ["Hcene Benmohamed", "Arnaud Leleve", "Patrick Prévot"],
        "student_title": "Remote Laboratories: New Technology and Standard Based Architecture",
        "student_author": "Hcene BENMOHAMED, Arnaud LELEVE, Patrick PREVOT",
        "student_arxiv": None,
    },
}


def _scan_paper_source(full_text: str) -> dict[str, str | None]:
    """从报告全文扫描论文出处信息。

    Returns:
        {"arxiv_id": "...", "doi": "...", "title": "...", "author": "..."}
        未找到的字段为 None。
    """
    import re as _re

    found: dict[str, str | None] = {
        "arxiv_id": None,
        "doi": None,
        "title": None,
        "author": None,
    }
    # 只扫前 2000 字（出处通常在头部）+ 末尾 500 字（参考文献可能在末尾）
    scan_zone = (full_text[:2000] + "\n" + full_text[-500:]) if len(full_text) > 2500 else full_text

    for pat, field in _SOURCE_PATTERNS:
        if found.get(field):
            continue  # 已找到，跳过同类
        m = _re.search(pat, scan_zone, _re.IGNORECASE)
        if m:
            val = m.group(1).strip().strip(".,;:\"'")
            # 过滤误判：标题/作者太短或太长
            if field in ("title", "author") and (len(val) < 3 or len(val) > 150):
                continue
            # 过滤"作者（包括初学者）"这类正文误判
            if field == "author" and any(
                w in val for w in ["包括", "例如", "如", "等", "students"]
            ):
                continue
            found[field] = val
    return found


def _query_arxiv(arxiv_id: str) -> dict | None:
    """用 arXiv API 查询单篇论文元数据（复用项目 arxiv_crawler 模块）。

    Returns:
        {"arxiv_id", "title", "authors": [...]} 或 None（查询失败/未找到）
    """
    from .arxiv_crawler import get_arxiv_by_id

    try:
        paper = get_arxiv_by_id(arxiv_id)
    except Exception as e:
        logger.warning(f"arXiv API 查询失败 ({arxiv_id}): {e}")
        return None
    if not paper:
        return None
    return {
        "arxiv_id": paper.get("id", arxiv_id),
        "title": paper.get("title", ""),
        "authors": paper.get("authors", []),
    }


def _resolve_paper_pair(student_id: str, full_text: str) -> dict | None:
    """解析学生报告对应的原论文配对。

    流程：
    1. 查缓存（_VERIFIED_CACHE）—— 已验证过的直接返回
    2. 扫描全文找 arXiv ID/DOI/标题/作者
    3. 有 arXiv ID → arXiv API 按 ID 验证 → 缓存 + 返回
    4. 无 arXiv ID 但有标题 → arXiv API 按标题搜索 → 相似度≥阈值才算配对
    5. 都没有 → 返回 None（不加分）

    Returns:
        配对 dict（含 arxiv_id, title, authors, student_title, student_author, student_arxiv）
        或 None（无出处或验证失败）
    """
    # 1. 缓存命中
    if student_id in _VERIFIED_CACHE:
        # 缓存仅存 arXiv 元数据——student_title/author 必须每次从当前 full_text 现扫，
        # 避免「学生首次报告匹配论文 A、缓存污染，此后所有报告都按 A 的引用准确度加分」。
        cached = _VERIFIED_CACHE[student_id]
        sources = _scan_paper_source(full_text)
        return {
            "arxiv_id": cached["arxiv_id"],
            "title": cached["title"],
            "authors": cached["authors"],
            "student_title": sources.get("title"),
            "student_author": sources.get("author"),
            "student_arxiv": sources.get("arxiv_id") or cached.get("arxiv_id", ""),
        }

    # 2. 扫描全文
    sources = _scan_paper_source(full_text)
    if not any(sources.values()):
        return None  # 无任何出处信息

    student_title = sources.get("title")
    student_author = sources.get("author")

    # 3. 有 arXiv ID → 按 ID 精确查询
    arxiv_id = sources.get("arxiv_id")
    if arxiv_id:
        real = _query_arxiv(arxiv_id)
        if real:
            pair = {
                "arxiv_id": real["arxiv_id"],
                "title": real["title"],
                "authors": real["authors"],
                "student_title": student_title,
                "student_author": student_author,
                "student_arxiv": arxiv_id,
            }
            _VERIFIED_CACHE[student_id] = pair
            logger.info(f"学生 {student_id} 按ID验证成功: arXiv:{arxiv_id} - {real['title'][:50]}")
            return pair
        logger.warning(f"学生 {student_id} 的 arXiv ID {arxiv_id} 查询失败，尝试标题搜索")

    # 4. 无 arXiv ID（或ID查询失败）但有标题 → 按标题搜索
    if student_title and len(student_title) >= 8:
        pair = _search_by_title(student_id, student_title, student_author)
        if pair:
            _VERIFIED_CACHE[student_id] = pair
            return pair
        logger.info(f"学生 {student_id} 标题搜索无匹配: {student_title[:50]}")
    else:
        logger.info(f"学生 {student_id} 有出处但无可用标识（arXiv ID/标题均缺）: {sources}")

    return None


def _search_by_title(
    student_id: str, student_title: str, student_author: str | None
) -> dict | None:
    """用 arXiv API 按标题搜索并匹配。

    策略：取 search_arxiv 返回的第一条结果，用标题相似度校验：
    - Jaccard 字符相似度 ≥ 0.50（防误匹配）
    - 或学生标题包含真实标题前 20 字（中文译题情况）

    Args:
        student_title: 学生报告里写的标题（可能是中文译题）
        student_author: 学生报告里写的作者（可能为 None）

    Returns:
        配对 dict 或 None（未找到/相似度不足）
    """
    from .arxiv_crawler import search_arxiv

    try:
        results = search_arxiv(student_title, max_results=3)
    except Exception as e:
        logger.warning(f"标题搜索 arXiv 失败 ({student_title[:30]}): {e}")
        return None

    if not results:
        return None

    for paper in results:
        real_title = paper.get("title", "")
        if not real_title:
            continue
        sim = _title_match_score(student_title, real_title)
        # 匹配条件（满足任一即可）：
        # 1. Jaccard 字符相似度 ≥ 0.50（英文长标题对长标题）
        # 2. 学生标题是真实标题的子串（短标题情况，如 "SUPR-GAN" 匹配 "SUPR-GAN: SUrgical..."）
        # 3. 真实标题前 20 字在学生标题里（中文译题偏差，如"开箱体验"匹配"The Unboxing Experience..."）
        norm_student = _normalize_for_match(student_title)
        norm_real = _normalize_for_match(real_title)
        student_in_real = len(norm_student) >= 4 and norm_student in norm_real
        prefix_hit = len(real_title) >= 20 and _normalize_for_match(real_title[:20]) in norm_student
        if sim >= 0.50 or student_in_real or prefix_hit:
            return {
                "arxiv_id": paper.get("id", ""),
                "title": real_title,
                "authors": paper.get("authors", []),
                "student_title": student_title,
                "student_author": student_author,
                "student_arxiv": None,  # 标题搜索时学生未提供 arxiv_id
            }
    return None


def _normalize_for_match(s: str) -> str:
    """归一化字符串用于比对：小写、去标点空格。"""
    import re as _re

    return _re.sub(r"[\s：:，,。.！!？?（()）\"'《》\-—_]", "", (s or "").lower())


def _title_match_score(student_title: str, real_title: str) -> float:
    """标题匹配度（字符 Jaccard 相似度）。"""
    a, b = _normalize_for_match(student_title), _normalize_for_match(real_title)
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb)


def _author_match_score(student_author: str | None, real_authors: list[str]) -> float:
    """作者匹配度：学生描述里是否包含任一真实作者。"""
    if not student_author:
        return 0.0
    s = _normalize_for_match(student_author)
    for a in real_authors:
        an = _normalize_for_match(a)
        if an and an in s:
            return 1.0
    # 部分匹配（姓）
    for a in real_authors:
        surname = a.split()[0].lower() if a.split() else ""
        if surname and len(surname) > 2 and surname in s:
            return 0.5
    return 0.0


def _compute_crossval_accuracy(pair: dict) -> tuple[float, dict]:
    """计算单篇配对的综合准确度。

    Returns:
        (accuracy, detail) 其中 detail 是各项匹配详情
    """
    tm = _title_match_score(pair.get("student_title"), pair.get("title", ""))
    am = _author_match_score(pair.get("student_author"), pair.get("authors", []))
    has_arxiv = 1.0 if pair.get("student_arxiv") else 0.0
    accuracy = tm * 0.4 + am * 0.3 + has_arxiv * 0.3
    return accuracy, {"title": tm, "author": am, "arxiv": has_arxiv}


def apply_crossval_bonus(
    scores: dict[str, float], student_id: str, full_text: str = ""
) -> tuple[dict[str, float], float, str]:
    """对已验证出处的报告，按引用准确度给 understanding_accuracy 加分。

    自动流程：扫描全文找出处 → arXiv API 验证 → 配对成功则加分。
    无出处或验证失败 → 不加分（不惩罚无出处）。

    Args:
        scores: 硬编码校验后的分数 dict
        student_id: 学号（用于查缓存）
        full_text: 报告全文（用于扫描出处）

    Returns:
        (new_scores, bonus, reason) - 加分后的分数、加分值、原因说明

    环境变量 PAPERFORGE_REFLECTION_SKIP_CROSSVAL=1 时跳过在线验证
    （离线/沙箱环境 arXiv API 不可达，避免每次 30s 超时拖慢批量评测）。
    """
    if os.environ.get("PAPERFORGE_REFLECTION_SKIP_CROSSVAL") == "1":
        return scores, 0.0, ""
    pair = _resolve_paper_pair(student_id, full_text)
    if not pair:
        return scores, 0.0, ""

    accuracy, detail = _compute_crossval_accuracy(pair)
    bonus = round(accuracy * _CROSSVAL_BONUS_MAX, 4)

    # 加分后封顶 1.0
    old = scores.get(_CROSSVAL_BONUS_DIM, 0.0)
    new_val = min(1.0, old + bonus)
    actual_bonus = round(new_val - old, 4)

    new_scores = dict(scores)
    new_scores[_CROSSVAL_BONUS_DIM] = new_val
    # 重算 average
    vals = [v for k, v in new_scores.items() if k != "average"]
    if vals:
        new_scores["average"] = round(sum(vals) / len(vals), 4)

    reason = (
        f"交叉验证加分: arXiv:{pair['arxiv_id']} 配对成功，"
        f"准确度={accuracy:.2f}(标题{detail['title']:.2f}+作者{detail['author']:.2f}"
        f"+arxiv{detail['arxiv']:.1f})，{_CROSSVAL_BONUS_DIM} +{actual_bonus:.4f}"
    )
    return new_scores, actual_bonus, reason


# ===========================================================================
# 简易 LLM 调用封装（复用 depth_eval_v4 的 watchdog 能力）
# ===========================================================================
def call_llm(prompt: str, system_prompt: str = "") -> str:
    """同步 LLM 调用封装，委托 depth_eval_v4.call_llm 统一使用 watchdog 超时。

    通过 v4 的 watchdog executor 确保 LLM 调用不会无限期阻塞。
    max_tokens 走【reflection 专用下限】`_REFLECTION_MAX_TOKENS_FLOOR`，不会低于
    3000（除非 env REFLECTION_MAX_TOKENS=0 显式压低）。
    """
    from .depth_eval_v4 import call_llm as v4_call_llm

    cfg = get_compute_mode_config()
    cfg_max_tokens = int(cfg.get("max_tokens", 512))
    max_tokens = max(cfg_max_tokens, _REFLECTION_MAX_TOKENS_FLOOR)
    return v4_call_llm(prompt=prompt, system_prompt=system_prompt, max_tokens=max_tokens)


# ===========================================================================
# 便捷函数
# ===========================================================================
def review_reflection(
    paper_id: str,
    title: str,
    full_text: str,
    paper_text: str = "",
) -> dict[str, Any]:
    """便捷函数：直接评审，返回扁平 dict 便于序列化。

    DepthReviewV4.reflection_result 列存此 dict。

    注意：paper_text（原论文全文）默认为空 = 旧版行为（不注入论文参考）。
    需要千问对照论文核验时请传入（主流程 reflection_pipeline 已传；
    直接调用本函数若不传则保持旧行为）。
    """
    reviewer = ReflectionReviewer()
    result = reviewer.review(paper_id, title, full_text, paper_text=paper_text)
    return result.model_dump()
