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
  R2 → 代码层硬校验：
    1. claims 与 evidence_pool 交叉引用 → 计算 effective_evidence_count
    2. effective_evidence_count < MIN_EVIDENCE_FOR_VALID_REVIEW (2)
       → 4 维评分上限 CAP 到 0.3 + verdict="rewrite_required"
    3. understanding_accuracy < UNDERSTANDING_THRESHOLD_DEEP (0.40)
       → verdict="needs_depth"（理解不够深）
    4. 4 维平均 < 0.50 → verdict="needs_evidence"（证据不足）
    5. 否则 → 通过 + verdict="well_done"，并附 verdict_reason 说明

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
MAX_SCORE_WHEN_EVIDENCE_BELOW_HIGH = 0.85  # 证据 < 5 条时分数上限（调严新增）
UNDERSTANDING_THRESHOLD_DEEP = 0.50  # 理解准确性 < 此即判 needs_depth（调严：0.40→0.50）
AVERAGE_SCORE_POOR = 0.65  # 平均 < 此即判 needs_evidence（调严：0.50→0.65）
INNOVATION_THRESHOLD = 0.45  # 创新见解 < 此即判 needs_depth（新增门阀：创新不足）

VALID_VERDICTS = {"well_done", "needs_evidence", "needs_depth", "rewrite_required"}

# 文本截断（感悟报告通常比论文短）：超限时保留开头+结尾，中间丢弃并告知 LLM。
MAX_CHARS_FULL = 16000  # 兼容旧常量（<= 头+尾 时不截断）
MAX_CHARS_HEAD = 14000  # 保留的开头字符数
MAX_CHARS_TAIL = 2000  # 保留的结尾字符数（报告第四段感想通常在尾部）
MAX_CHARS_CLAIMS = 4000


# 原论文参考内容预览长度（2026-08 实验 B 校准口径：取论文开头 4000 字符，
# 通常含摘要+引言，足够判断报告理解是否准确、覆盖是否充分；对齐 exp_b_qwen_full.py）。
# 原论文参考内容预览长度。默认 4000（实验 B 校准口径）；
# 可通过 PAPERFORGE_PAPER_PREVIEW_CHARS 调小以缩短 4 维打分 prompt、
# 避免本机 LLM 在大论文上触发 watchdog 超时（重跑长论文批次时用 1500~2000 即可）。
def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    """读取正整数环境变量；非法配置回退默认值，避免导入阶段崩溃。"""
    try:
        return max(int(os.getenv(name, str(default))), minimum)
    except (TypeError, ValueError):
        logger.warning("忽略非法环境变量 %s，使用默认值 %s", name, default)
        return default


MAX_PAPER_PREVIEW_CHARS = _env_int("PAPERFORGE_PAPER_PREVIEW_CHARS", 4000)


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
    evaluated_at: str = ""


# ===========================================================================
# Prompt 模板
# ===========================================================================

PROMPT_REFLECTION = """你是一位严谨的阅读笔记评审专家。请评审以下"读后感/感悟/复现报告"，
识别其核心观点，并从报告本体内提取原文片段作为支撑证据，按 4 维度严格打分。

报告类型判断标准：
- 用户对某篇论文的读后感 / 复现实验的感悟 / 批判性思考（**非完整论文**）。

{paper_section}
报告全文：
{content}
{truncation_note}

【要求】

1. **核心观点 claims**：提取 3-5 个最核心的观点（如果有的话）。每个 claim 必须有支撑片段。
   每项结构：{{"id": "C1", "text": "观点陈述，不超过80字", "evidence_id": "E1"}}

2. **evidence_pool**：从报告原文中提取 3-5 段支撑性原文片段（不能编造，必须真实出现在报告全文中）。
   每项结构：{{"id": "E1", "snippet": "原文直接引用片段，不超过100字", "claim_ref": "C1"}}
   ⚠️ **snippet 的唯一合法来源是上方「报告全文」**。
   - 禁止引用【原论文参考内容】里的任何句子——那是背景材料，不是学生写的。
   - 禁止把报告里分散的几句话拼接、改写、翻译成一句。
   - 逐字复制，标点也要一致；宁可摘短句，也不要凑字数。
   代码层会逐条比对：凡是在「报告全文」里查不到的 snippet 一律作废，
   作废后有效证据不足 2 条，整篇会被强制判为 rewrite_required。

3. **4 维评分**（每个分数必须有 evidence_id 引用）：
   - `understanding_accuracy`：报告对论文核心方法/结论的复述是否具体、准确、自洽
     （若上方提供了【原论文参考内容】可对照核验；未提供则仅基于报告文本，0-1）
   - `analysis_depth`：分析深度，对原文细节的展开程度（0-1）
   - `innovative_insights`：是否有原创/批判性思考（0-1）
   - `evidence_support`：观点是否有具体引述/数据/例子支撑（0-1）

【评分参照系】请严格使用 0-1 全区间，**不要集中在 0.85-0.95**。
各维度三档锚点（对照报告实际内容判断落在哪档，允许中间值）：

- `understanding_accuracy`：
  差(0.2-0.4)=复述含糊、张冠李戴、关键概念错误；中(0.5-0.7)=复述正确但简略、缺关键细节；
  好(0.8-1.0)=方法/结论复述具体准确，关键数字与术语齐全。
- `analysis_depth`：
  差(0.2-0.4)=只复述无展开；中(0.5-0.7)=有少量分析但停留在表面；
  好(0.8-1.0)=对原文细节深入展开、有机制解释或权衡讨论。
- `innovative_insights`：
  差(0.2-0.4)=无独立观点、纯总结；中(0.5-0.7)=有零星想法但未深入；
  好(0.8-1.0)=有原创批判、独立见解、跨领域联系。
- `evidence_support`：
  差(0.2-0.4)=观点无引用/数据支撑；中(0.5-0.7)=有引述但零散、缺乏数据/数字；
  好(0.8-1.0)=多数观点有独立数据/数字/例子逐条支撑（引述≥3 处、含具体数值）。
  **自检**：若报告只有概括性引述而缺少具体数字/数据，evidence_support 不应超过 0.7。

**评分前自检**：给分时优先依据各维锚点的行为描述判断落在哪档，而不是凭总体印象；
若某维达不到「好」档描述的全部标准，应相应下调分数并给出与锚点一致的档位。

4. **summary**：用 1-2 句话总结报告的核心内容。

5. **verdict_suggestion**：你的初步建议，但最终判决由代码层硬校验决定（证据不足时会被强制降级）。

【严格输出 JSON，无多余字符】：
{{
  "claims": [...],
  "evidence_pool": [...],
  "understanding_accuracy": 0.0~1.0,
  "analysis_depth": 0.0~1.0,
  "innovative_insights": 0.0~1.0,
  "evidence_support": 0.0~1.0,
  "summary": "...",
  "verdict_suggestion": "well_done|needs_evidence|needs_depth|rewrite_required"
}}
"""

# 原论文参考区块（仅当 review() 收到 paper_text 时注入）：
# 语义：论文仅作背景对照，用于判断报告理解是否准确、覆盖是否充分；
#       evidence snippet 仍必须来自报告本身（硬校验在 R2 强制）。
PAPER_SECTION_TEMPLATE = """【原论文参考内容】（⚠️ 只读背景材料，用于判断报告的理解是否准确、
覆盖是否充分。**本区块的任何句子都不得作为 evidence snippet 引用**——
它不是学生写的内容，引用它会被代码层判定为无效证据）：
{paper_preview}
"""


# ===========================================================================
# 重试辅助
# ===========================================================================

# 证据不足重试：LLM 首次只产出 < MIN_EVIDENCE_FOR_VALID_REVIEW 条有效证据
# （本地量化模型常见波动：3-5 条要求却只给 1-2 条 → R1 把整篇压到 0.3 的误伤）。
# 仅对「解析成功但证据不足」的报告追加一次定向重试，要求补齐 3-5 条逐字引文。
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

        # 正向：claim 引用了存在的 evidence
        forward = sum(1 for c in claims if c.get("evidence_id", "").strip() in valid_eids)
        # 反向：evidence 引用了存在的 claim（且自身 snippet 有效）
        backward = sum(
            1
            for ev in evidence_pool
            if (ev.get("claim_ref") or "").strip() in valid_cids
            and (ev.get("id") or "").strip() in valid_eids
        )
        effective = max(forward, backward)

        validated_claims = [c for c in claims if c.get("evidence_id", "").strip() in valid_eids]
        validated_pool = [
            ev
            for ev in evidence_pool
            if (ev.get("claim_ref") or "").strip() in valid_cids
            and (ev.get("id") or "").strip() in valid_eids
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
    ) -> ReflectionScoredOutput:
        """应用硬编码校验规则，输出最终结果。

        规则优先级（从上到下，后面的规则可叠加，但 verdict 一旦被强制就锁死）：
        1. effective_evidence_count < MIN_EVIDENCE_FOR_VALID_REVIEW (2)
           → 4 维分数全部上限 CAP 到 MAX_SCORE_WHEN_EVIDENCE_INSUFFICIENT (0.3)
           → verdict 锁死为 rewrite_required
        2. understanding_accuracy < UNDERSTANDING_THRESHOLD_DEEP (0.40)
           → verdict 锁死为 needs_depth（理解不够深）
        3. 4 维平均 < AVERAGE_SCORE_POOR (0.50)
           → verdict 锁死为 needs_evidence
        4. 其余 → 通过，verdict 使用 LLM 的 verdict_suggestion
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

        # ── 规则 1.5：证据不足以推高分（调严新增）──
        # 有效证据 >= 2 但 < 5 → 4 维分数 CAP 到 0.85，防止证据不足却拿满分
        if not rule_1_fired and effective < MIN_EVIDENCE_FOR_HIGH_SCORE:
            capped_any = False
            for k in list(out_scores.keys()):
                if out_scores[k] > MAX_SCORE_WHEN_EVIDENCE_BELOW_HIGH:
                    out_scores[k] = MAX_SCORE_WHEN_EVIDENCE_BELOW_HIGH
                    capped_any = True
            if capped_any:
                overrides.append(
                    f"R1.5: 有效证据数 {effective} < {MIN_EVIDENCE_FOR_HIGH_SCORE}，"
                    f"分数 CAP 至 {MAX_SCORE_WHEN_EVIDENCE_BELOW_HIGH}"
                )

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
    ) -> ReflectionReviewResult:
        """评审单篇报告。

        Args:
            student_id: 学号（可选，用于交叉验证加分查 _VERIFIED_PAIRS）
            paper_text: 原论文全文（可选）。非空时把论文开头 MAX_PAPER_PREVIEW_CHARS
                字符作为【原论文参考内容】注入 prompt，供千问对照核验报告理解准确性
                （2026-08 实验 B：千问拿到论文后 understanding/evidence 判得更准，
                但分数整体上浮——权重重构已把区分度不足维度降权以吸收该效应）。
                不传时 prompt 与旧版完全一致。
        """
        self._logs = []
        self._llm_calls = 0
        self._llm_empty = 0
        self._evidence_rejections = {}
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
        # 原论文参考（可选注入）：取论文开头 MAX_PAPER_PREVIEW_CHARS 字符
        # （实验 B 校准：摘要+引言足够判断理解准确性；snippet 仍强制来自报告）
        paper_section = ""
        if paper_text and paper_text.strip():
            paper_section = PAPER_SECTION_TEMPLATE.format(
                paper_preview=paper_text[:MAX_PAPER_PREVIEW_CHARS]
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
            full_text=full_text,  # snippet 真实性校验（原始全文，非截断版）
        )

        # 2.1 证据不足定向重试（本地量化模型波动：好报告也常只给 1-2 条证据）
        if not parse_failed and scored.effective_evidence_count < MIN_EVIDENCE_FOR_VALID_REVIEW:
            # 先弄清楚上一轮到底错在哪，把具体原因写进重试提示。
            # 只报「你才给了 1 条」模型往往原样再来一遍；
            # 告诉它「你那条摘自原论文」才纠得回来。
            rej = diagnose_evidence_rejections(llm_output.evidence_pool, full_text, paper_text)
            self._evidence_rejections = rej["counts"]
            retry_ev = base_prompt.rstrip() + RETRY_EVIDENCE_TEMPLATE.format(
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
            llm_calls=self._llm_calls,
            llm_empty=self._llm_empty,
            evidence_rejections=dict(self._evidence_rejections),
            evaluated_at=datetime.now().isoformat(timespec="seconds"),
            node_logs=list(self._logs),
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
        return _VERIFIED_CACHE[student_id]

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
