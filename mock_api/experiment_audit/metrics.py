"""P0-1 正文-表格数字一致性 + P0-2 指标数学自洽性（纯 Python，无 GPU）。

设计（确定性优先，宁缺毋滥）：
- P0-2：只有同一句/同一表格行内同时出现 P、R、F1（或混淆矩阵四元组）
  才做公式验证，跨句配对会引入假阳性，不做。
- P0-1：正文句子显式引用 'Table N' 且含指标+数值时才比对；表内找不到
  对应单元格 → 跳过（不猜测、不误报）。
- 百分点 vs 相对百分比：改进幅度声称与表内差值两种口径都核对，
  仅相对口径吻合时产出「建议明确标注」的低严重度 Finding。
"""

from __future__ import annotations

import re

from ..figure_claims import _normalize_metric, _parse_number
from .schemas import make_finding
from .tables import ExtractedTable, find_table_by_id

# ── 容差默认值（指南第 2 节示例用 0.5）──────────────────────────
DEFAULT_TOLERANCE = 0.5
RELATIVE_TOLERANCE = 1.0


# ---------------------------------------------------------------------------
# P0-2 指标数学自洽性
# ---------------------------------------------------------------------------
def compute_f1(precision: float, recall: float) -> float:
    """F1 = 2PR/(P+R)，除零保护返回 0。"""
    if precision + recall <= 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def check_f1_consistency(
    precision: float,
    recall: float,
    reported_f1: float,
    tolerance: float = DEFAULT_TOLERANCE,
    *,
    page: int | None = None,
    evidence: list[dict] | None = None,
) -> dict | None:
    """验证 F1 = 2PR/(P+R)。不一致返回 Finding，一致返回 None。"""
    computed = compute_f1(precision, recall)
    diff = abs(computed - reported_f1)
    if diff <= tolerance:
        return None
    return make_finding(
        "METRIC_INCONSISTENCY",
        title="F1 数值与 Precision/Recall 不自洽",
        page=page,
        claim=f"P={precision}, R={recall}, 报告 F1={reported_f1}",
        computed=f"{computed:.2f}",
        tolerance=tolerance,
        method=(
            f"F1 = 2 × {precision} × {recall} / ({precision} + {recall}) "
            f"= {computed:.2f}，与报告值差 {diff:.2f} > 容差 {tolerance}"
        ),
        evidence_sources=evidence or [],
        normal_explanation=("可能是四舍五入差异，或 Precision/Recall 与 F1 来自不同实验设置"),
        needs_human_review=True,
    )


def check_confusion_matrix_consistency(
    tp: float,
    fp: float,
    fn: float,
    reported: dict[str, float],
    tolerance: float = DEFAULT_TOLERANCE,
    *,
    page: int | None = None,
    evidence: list[dict] | None = None,
) -> list[dict]:
    """由 TP/FP/FN 推导 P/R/F1 并与报告值比对。返回 Finding 列表。"""
    findings: list[dict] = []
    if tp + fp <= 0 or tp + fn <= 0:
        return findings
    expected = {
        "precision": tp / (tp + fp),
        "recall": tp / (tp + fn),
    }
    expected["f1"] = compute_f1(expected["precision"], expected["recall"])
    for name, exp in expected.items():
        rep = reported.get(name)
        if rep is None:
            continue
        # 尺度自适应：推导值为 0~1 比例，报告值 >1 时按百分制比对
        exp_scaled = exp * 100 if rep > 1.0 else exp
        if abs(exp_scaled - rep) > tolerance:
            findings.append(
                make_finding(
                    "METRIC_INCONSISTENCY",
                    title=f"{name} 与混淆矩阵不自洽",
                    page=page,
                    claim=f"TP={tp}, FP={fp}, FN={fn}, 报告 {name}={rep}",
                    computed=f"{exp_scaled:.2f}",
                    tolerance=tolerance,
                    method=f"由混淆矩阵推导 {name}={exp_scaled:.2f}",
                    evidence_sources=evidence or [],
                    normal_explanation="混淆矩阵与指标可能来自不同划分/阈值",
                    needs_human_review=True,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# 句子级 P/R/F1 共现扫描（P0-2 入口）
# ---------------------------------------------------------------------------
_METRIC_VALUE_RE = re.compile(
    r"\b(precision|recall|f1|f-measure|accuracy)\b\s*(?:[=:]|\bis\b|\bof\b)\s*"
    r"(\d{1,3}(?:\.\d+)?)\s*%?",
    re.IGNORECASE,
)

_CM_VALUE_RE = re.compile(r"\b(TP|FP|FN|TN)\b\s*[=:]\s*(\d+(?:\.\d+)?)", re.IGNORECASE)


def check_sentence_metric_consistency(sentence: str, page: int | None = None) -> list[dict]:
    """对单个句子做 P/R/F1 与混淆矩阵自洽性检查。"""
    findings: list[dict] = []
    values: dict[str, float] = {}
    for m in _METRIC_VALUE_RE.finditer(sentence):
        name = m.group(1).lower()
        name = "f1" if name in ("f1", "f-measure") else name
        values.setdefault(name, float(m.group(2)))
    if {"precision", "recall", "f1"} <= values.keys():
        f = check_f1_consistency(
            values["precision"],
            values["recall"],
            values["f1"],
            page=page,
            evidence=[{"type": "text", "page": page, "snippet": sentence.strip()}],
        )
        if f:
            findings.append(f)

    cm: dict[str, float] = {}
    for m in _CM_VALUE_RE.finditer(sentence):
        cm.setdefault(m.group(1).upper(), float(m.group(2)))
    if {"TP", "FP", "FN"} <= cm.keys():
        reported = {k: v for k, v in values.items() if k in ("precision", "recall", "f1")}
        findings.extend(
            check_confusion_matrix_consistency(
                cm["TP"],
                cm["FP"],
                cm["FN"],
                reported,
                page=page,
                evidence=[{"type": "text", "page": page, "snippet": sentence.strip()}],
            )
        )
    return findings


# ---------------------------------------------------------------------------
# P0-1 正文-表格数字交叉比对
# ---------------------------------------------------------------------------
_TABLE_REF_RE = re.compile(r"\bTable\s+(\d+)\b", re.IGNORECASE)
# 提升类声称：captures/improves/gains of X (percentage points|%)
_IMPROVEMENT_RE = re.compile(
    r"(?:improv\w+|gain\w*|increas\w+|boost\w*)[^.\n]{0,80}?"
    r"(\d{1,3}(?:\.\d+)?)\s*(percentage\s*points?|points?|pp|%)",
    re.IGNORECASE,
)


# 缩写保护：这些缩写后的句号不作为句子边界（固定长度 lookbehind）
_ABBREV_LOOKBEHINDS = (
    r"(?<!Fig\.)(?<!Figs\.)(?<!fig\.)(?<!figs\.)"
    r"(?<!Tab\.)(?<!Eq\.)(?<!Sec\.)(?<!No\.)"
    r"(?<!e\.g\.)(?<!i\.e\.)(?<!et al\.)(?<!vs\.)"
)
_SPLIT_RE = re.compile(_ABBREV_LOOKBEHINDS + r"(?<=[.!?])\s+(?=[A-Z0-9(])")


def split_sentences(text: str) -> list[str]:
    """轻量句子切分，保留 Fig./et al./e.g. 等缩写不误切。"""
    text = re.sub(r"\s+", " ", text or "")
    parts = _SPLIT_RE.split(text)
    return [p.strip() for p in parts if len(p.strip()) >= 12]


def check_percentage_claim(
    claimed: float,
    table_before: float,
    table_after: float,
    abs_tolerance: float = DEFAULT_TOLERANCE,
    rel_tolerance: float = RELATIVE_TOLERANCE,
) -> str:
    """判定提升声称口径：'absolute' 一致 / 'relative_only' 仅相对口径
    吻合（建议标注）/ 'mismatch' 两种口径都不符 / 'ok_zero' 无实际提升。"""
    absolute_diff = table_after - table_before
    if table_before == 0:
        return "mismatch" if abs(claimed) > abs_tolerance else "absolute"
    relative_diff = (absolute_diff / abs(table_before)) * 100
    if abs(claimed - absolute_diff) <= abs_tolerance:
        return "absolute"
    if abs(claimed - relative_diff) <= rel_tolerance:
        return "relative_only"
    return "mismatch"


def check_numeric_claims_vs_tables(
    full_text: str,
    tables: list[ExtractedTable],
    page_index: dict[int, int] | None = None,
) -> list[dict]:
    """P0-1 入口：扫描显式引用 Table N 的句子，与表内数值交叉比对。

    page_index 可选：句子序号 → 页码映射（编排层由分页文本构造）；
    缺省时 Finding 不带页码。
    """
    findings: list[dict] = []
    if not tables:
        return findings
    sentences = split_sentences(full_text)
    for s_idx, sent in enumerate(sentences):
        table_refs = _TABLE_REF_RE.findall(sent)
        if not table_refs:
            continue
        page = (page_index or {}).get(s_idx)
        findings.extend(_check_sentence_against_tables(sent, table_refs, tables, page))
    return findings


def _check_sentence_against_tables(
    sentence: str,
    table_refs: list[str],
    tables: list[ExtractedTable],
    page: int | None,
) -> list[dict]:
    findings: list[dict] = []
    evidence = [{"type": "text", "page": page, "snippet": sentence.strip()}]

    # ── 形态 1：句内有指标+数值，且能定位到表内单元格 → 直接比对 ──
    for m in _METRIC_VALUE_RE.finditer(sentence):
        metric_raw, value = m.group(1), float(m.group(2))
        metric = _normalize_metric(metric_raw) or metric_raw.lower()
        for ref in table_refs:
            table = find_table_by_id(tables, f"Table {int(ref)}")
            if table is None:
                continue
            hit = _match_any_row_value(table, metric, value)
            if hit == "mismatch":
                found = _best_row_value(table, metric)
                findings.append(
                    make_finding(
                        "NUMERIC_MISMATCH",
                        title=f"正文 {metric} 数值与 {table.label()} 不一致",
                        page=page or table.page,
                        claim=f"{metric} = {value}（正文）",
                        computed=f"{table.label()} 中最接近值 {found}"
                        if found is not None
                        else None,
                        tolerance=DEFAULT_TOLERANCE,
                        method="正文声称值 vs 表格同指标单元格，超出容差",
                        evidence_sources=evidence
                        + [{"type": "table", "table_id": table.label(), "page": table.page}],
                        normal_explanation=(
                            "可能是不同实验设置/数据集切分下的结果，或正文引用了另一版本表格"
                        ),
                        needs_human_review=True,
                    )
                )

    # ── 形态 2：提升幅度声称（improvement of X%）vs 表内首尾行差值 ──
    for m in _IMPROVEMENT_RE.finditer(sentence):
        claimed = float(m.group(1))
        for ref in table_refs:
            table = find_table_by_id(tables, f"Table {int(ref)}")
            if table is None:
                continue
            verdict = _check_improvement_against_table(table, claimed)
            if verdict is None:
                continue
            kind, before, after = verdict
            if kind == "relative_only":
                findings.append(
                    make_finding(
                        "NUMERIC_MISMATCH",
                        severity="low",
                        title=f"提升幅度口径不明（{table.label()}）",
                        page=page or table.page,
                        claim=f"正文声称提升 {claimed}",
                        computed=f"百分点差 {after - before:.2f}，"
                        f"相对提升 {(after - before) / before * 100:.2f}%"
                        if before
                        else None,
                        method="声称值仅与相对百分比口径吻合，建议明确标注",
                        evidence_sources=evidence
                        + [{"type": "table", "table_id": table.label(), "page": table.page}],
                        normal_explanation="部分领域惯例使用相对提升，但应显式标注",
                        needs_human_review=False,
                    )
                )
            elif kind == "mismatch":
                findings.append(
                    make_finding(
                        "NUMERIC_MISMATCH",
                        title=f"提升幅度与 {table.label()} 数据不符",
                        page=page or table.page,
                        claim=f"正文声称提升 {claimed}",
                        computed=f"表内差值 {after - before:.2f}"
                        f"（相对 {(after - before) / before * 100:.2f}%）"
                        if before
                        else None,
                        tolerance=DEFAULT_TOLERANCE,
                        method="百分点与相对百分比两种口径均不吻合",
                        evidence_sources=evidence
                        + [{"type": "table", "table_id": table.label(), "page": table.page}],
                        normal_explanation="可能比较的是表中非首尾行，或不同指标列",
                        needs_human_review=True,
                    )
                )
    return findings


def _match_any_row_value(table: ExtractedTable, metric: str, value: float) -> str | None:
    """句内指标值 vs 表内该指标列所有行：'match'/'mismatch'/None(无此列)。"""
    header = [(h or "").lower() for h in table.header]
    col_idx = next((i for i, h in enumerate(header) if metric in h.replace("-", " ")), None)
    if col_idx is None:
        return None
    values: list[float] = []
    for row in table.body:
        if col_idx < len(row):
            v = _parse_number(row[col_idx])
            if v is not None:
                values.append(v)
    if not values:
        return None
    return "match" if any(abs(v - value) <= DEFAULT_TOLERANCE for v in values) else "mismatch"


def _best_row_value(table: ExtractedTable, metric: str) -> float | None:
    header = [(h or "").lower() for h in table.header]
    col_idx = next((i for i, h in enumerate(header) if metric in h.replace("-", " ")), None)
    if col_idx is None:
        return None
    for row in table.body:
        if col_idx < len(row):
            v = _parse_number(row[col_idx])
            if v is not None:
                return v
    return None


def _check_improvement_against_table(
    table: ExtractedTable, claimed: float
) -> tuple[str, float, float] | None:
    """用表内第一个数值列的首行/末行差值验证提升声称。

    首行视为 baseline、末行视为本方法（ML 论文常见布局）。
    无法提取数值时返回 None（跳过）。
    """
    # 找第一个数值密度最高的列
    col_vals: list[tuple[float, float]] = []
    n_cols = max((len(r) for r in table.rows), default=0)
    for c in range(n_cols):
        vals: list[float] = []
        for row in table.body:
            if c < len(row):
                v = _parse_number(row[c])
                if v is not None:
                    vals.append(v)
        if len(vals) >= 2:
            col_vals.append((vals[0], vals[-1]))
    if not col_vals:
        return None
    # 取「差值最接近声称值」的列作为比对对象（防挑错列）
    best_kind = None
    best_pair = None
    for before, after in col_vals:
        kind = check_percentage_claim(claimed, before, after)
        if kind == "absolute":
            return ("absolute", before, after)
        if kind in ("relative_only", "mismatch") and best_kind is None:
            best_kind, best_pair = kind, (before, after)
    if best_kind is None:
        return None
    return (best_kind, best_pair[0], best_pair[1])
