"""P1-2 GRIM + P1-3 p-curve 统计造假指纹（纯规则，零外部依赖，离线可用）。

GRIM（Granularity-Related Inconsistency of Means，Brown & Heathers 2017）：
n 个整数观测值的均值只能是 k/n 的四舍五入（k 为整数）。因此给定报告
精度（小数位数 d），均值必须落在某个 k/n 的 d 位舍入区间内；否则该均值
在数学上不可能存在。

  - mean=4.56, n=30：4.56×30=136.8 非整数，且 136/30=4.533→4.53、
    137/30=4.567→4.57，两者都舍不进 4.56 → GRIM 违例（经典示例）。
  - mean=0.333, n=3：1/3=0.3333…→0.333，**可达** → 不违例。
    （0.333 是 1/3 的正确舍入，GRIM 必须按「舍入区间」而非「均值×n 恰为
    整数」判定，否则会把正确舍入误报为造假。n=3 报 0.35 才违例。）

p-curve（p 值分布）：p-hacking / 选择性汇报的经典指纹是 p 值恰好挤在
0.04~0.05（勉强显著）而 (0.05, 0.1] 出现断崖。

两者都是「造假线索」而非定罪证据，Finding 均 needs_human_review=True。
"""

from __future__ import annotations

import re
from collections import Counter

from .metrics import split_sentences
from .schemas import make_finding

# ── GRIM 抽取正则 ────────────────────────────────────────────────
# mean/average [+ ≤2 个描述词] [+ =/:] + 小数（负向 lookahead 排除百分比，
# 因为 accuracy/比例是连续量，GRIM 只适用于整数测量值）
_MEAN_RE = re.compile(
    r"\b(?:mean|average)\b"
    r"(?:\s+[a-zA-Z]+){0,2}"
    r"\s*[=:]?\s*"
    r"(\d+\.\d+)(?!\s*%)",
    re.IGNORECASE,
)

# M = 4.56（心理学描述统计的标准写法，是 GRIM 的强信号）
_M_EQ_RE = re.compile(r"\bM\s*=\s*(\d+\.\d+)(?!\s*%)")

# 样本量：n = 30 / N = 30
_N_RE = re.compile(r"\b[nN]\s*=\s*(\d{1,6})\b")

# 整数测量值的语境词：计数/离散评分/整年数等。连续量（weight/enzyme
# activity/reaction time/accuracy）不在其中，避免对 ML 指标均值大量误报。
_INTEGER_CONTEXT_RE = re.compile(
    r"\b(?:participants?|subjects?|patients?|volunteers?|respondents?|users?"
    r"|students?|items?|questions?|responses?|trials?|years?|months?|days?"
    r"|weeks?|hours?|minutes?|ages?|scores?|ratings?|correct|errors?|mistakes?"
    r"|faults?|defects?|bugs?|counts?|cells?|neurons?|animals?|mice|rats?"
    r"|specimens?|words?|tokens?|sentences?|paragraphs?|characters?)\b",
    re.IGNORECASE,
)

_MAX_GRIM_FINDINGS = 10


def grim_consistent(mean_str: str, n: int) -> bool:
    """判断报告均值 mean_str（含报告小数位）能否由 n 个整数观测值得到。

    精确整数运算实现（避免浮点误差）：是否存在整数 k 满足
    |k/n - m/10^d| ≤ 0.5/10^d，其中 m、d 由 mean_str 的字符串表示确定
    （含末尾 0 —— 报告 33.20 比 33.2 声称更高精度，GRIM 会分别判定）。
    """
    if n < 1:
        return True  # 无法判定，视为一致（不误报）
    if "." not in mean_str:
        return True  # 整数均值必然可达
    whole, _, frac = mean_str.partition(".")
    d = len(frac)
    if d == 0:
        return True
    scale = 10**d
    try:
        m = int(whole) * scale + int(frac or "0")
    except ValueError:
        return True
    # 需要整数 k 满足 (m - 0.5)*n/scale ≤ k ≤ (m + 0.5)*n/scale
    k_low = -(-(2 * m - 1) * n // (2 * scale))  # ceil
    k_high = (2 * m + 1) * n // (2 * scale)  # floor
    return 0 <= k_low <= k_high


def extract_mean_n_pairs(text: str) -> list[tuple[str, int, str]]:
    """抽取 (mean_str, n, 句子片段)。只在同句内配对，避免跨段误连。

    判定门槛（宁缺毋滥）：
    1. 句内有 mean/average 或 M= 引入的小数均值；
    2. 句内有 n=/N= 样本量；
    3. 句内有整数测量语境词，或 M= 写法（心理学描述统计的强信号）。
    三条同时满足才纳入 GRIM 检验。
    """
    pairs: list[tuple[str, int, str]] = []
    for sentence in split_sentences(text or ""):
        ns = [int(x) for x in _N_RE.findall(sentence)]
        if not ns:
            continue
        # 门槛 3：整数语境词或 M= 写法
        has_m_eq = _M_EQ_RE.search(sentence) is not None
        if not has_m_eq and not _INTEGER_CONTEXT_RE.search(sentence):
            continue
        means = [m.group(1) for m in _MEAN_RE.finditer(sentence)]
        means += [m.group(1) for m in _M_EQ_RE.finditer(sentence)]
        if not means:
            continue
        for mean_str in means:
            for n in ns:
                pairs.append((mean_str, n, sentence.strip()))
    return pairs


def check_grim(full_text: str) -> list[dict]:
    """P1-2 入口：扫描全文，报告 GRIM 违例的均值。"""
    pairs = extract_mean_n_pairs(full_text)
    findings: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for mean_str, n, snippet in pairs:
        if (mean_str, n) in seen:
            continue
        seen.add((mean_str, n))
        if n < 2 or grim_consistent(mean_str, n):
            continue
        findings.append(
            make_finding(
                "GRIM_INCONSISTENCY",
                title=f"均值 {mean_str} 无法由 n={n} 的整数样本推出",
                claim=f"报告均值 {mean_str}（n = {n}）",
                computed=(
                    f"n={n} 个整数观测值的均值只能是 k/{n} 的四舍五入，"
                    f"但 {mean_str} 落在任何 k/{n} 的舍入区间之外（GRIM 违例）"
                ),
                method="GRIM：均值×样本量必须落在整数 k/n 的舍入区间内",
                evidence_sources=[{"type": "text", "snippet": snippet[:300]}],
                normal_explanation=(
                    "可能是四舍五入/抄写错误，或该均值并非整数测量值的均值"
                    "（GRIM 仅适用于整数计数/离散评分的均值），需人工核对原始数据"
                ),
                needs_human_review=True,
            )
        )
        if len(findings) >= _MAX_GRIM_FINDINGS:
            break
    return findings


# ── p-curve 抽取与分析 ───────────────────────────────────────────
_PVALUE_RE = re.compile(
    r"\bp\s*(?:-?\s*value\b)?\s*([=<>≤≥])\s*0?\.(\d+)",
    re.IGNORECASE,
)


def extract_pvalues(text: str) -> list[tuple[str, float]]:
    """抽取 (operator, pvalue) 列表；仅保留 0<p≤1 的合法值。"""
    out: list[tuple[str, float]] = []
    for m in _PVALUE_RE.finditer(text or ""):
        op, digits = m.group(1), m.group(2)
        pv = float("0." + digits)
        if 0.0 < pv <= 1.0:
            out.append((op, pv))
    return out


def analyze_pvalues(pvalues: list[tuple[str, float]]) -> list[str]:
    """对 p 值序列做 p-hacking 线索分析，返回线索字符串列表（无则空）。

    只对精确 p 值（p = 0.0xx）做分布分析：p-hacking 的指纹是「勉强显著」
    的聚集，而 p < 0.05 这类关系式无法提供分布信息。
    """
    exact = [pv for _op, pv in pvalues if _op == "="]
    if len(exact) < 5:
        return []
    flags: list[str] = []
    marginal = [pv for pv in exact if 0.04 <= pv <= 0.05]
    strong = [pv for pv in exact if pv < 0.01]
    above = [pv for pv in exact if 0.05 < pv <= 0.10]

    # 指纹 1：勉强显著聚集，强显著反而少（深度评审零 LLM helper 同款判据）
    if len(marginal) >= 3 and len(marginal) > len(strong) * 2:
        flags.append(
            f"[p值聚集] {len(exact)} 个精确 p 值中 {len(marginal)} 个恰好落在 "
            f"0.04~0.05 勉强显著区间，而强显著(<0.01)仅 {len(strong)} 个，"
            f"符合 p-hacking 选择性汇报指纹"
        )

    # 指纹 2：同一勉强显著 p 值反复出现（如 3 个 0.045）
    repeats = Counter(f"{pv:.3f}" for pv in marginal)
    repeated = [v for v, c in repeats.items() if c >= 3]
    if repeated:
        flags.append(
            f"[p值重复] 勉强显著区间的同一 p 值反复出现："
            f"{'、'.join(f'p={v}' for v in sorted(repeated))}"
            f"（各 ≥3 次），独立检验精确 p 值高度重合，疑似编造"
        )

    # 指纹 3：0.05 右侧断崖（≥3 个勉强显著但 (0.05,0.1] 一个都没有）
    if len(marginal) >= 3 and not above:
        flags.append(
            f"[p曲线断崖] {len(marginal)} 个 p 值挤在 0.04~0.05，"
            f"(0.05, 0.1] 区间为 0，p 值分布恰在显著性阈值处截断"
        )
    return flags


def check_pcurve(full_text: str) -> list[dict]:
    """P1-3 入口：整篇论文 p 值分布异常聚集检测（最多 1 条 Finding）。"""
    pvalues = extract_pvalues(full_text)
    flags = analyze_pvalues(pvalues)
    if not flags:
        return []
    exact_n = sum(1 for op, _ in pvalues if op == "=")
    return [
        make_finding(
            "PCURVE_ANOMALY",
            title="p 值分布异常聚集（p-hacking 线索）",
            claim=f"全文共 {len(pvalues)} 处 p 值报告（其中精确值 {exact_n} 个）",
            computed="\n".join(flags),
            method="p 值抽取 + 勉强显著聚集 / 重复 p 值 / 0.05 右侧断崖检测",
            evidence_sources=[{"type": "text", "snippet": f"{len(pvalues)} 个 p 值"}],
            normal_explanation=(
                "可能是选择性汇报、多次比较未校正，或研究领域惯例只报显著结果；"
                "p 值聚集≠造假，需结合原始统计量与实验设计判断"
            ),
            needs_human_review=True,
        )
    ]
