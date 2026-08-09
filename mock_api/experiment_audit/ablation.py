"""P0-3 Ablation 结论检查。

两段式（确定性优先 + LLM 辅助定位）：
1. 确定性核心：若 ablation 表中存在「移除组件行」，且该行指标 ≥ 全模型行，
   而正文又声称 "each/every component contributes" → ABLATION_UNSUPPORTED。
   这是纯数学事实，不依赖 LLM。
2. LLM 辅助：claims 中的 ablation 类论断与表格趋势比对（needs_human_review）。
"""

from __future__ import annotations

import logging
import re

from ..figure_claims import _parse_number
from .metrics import split_sentences
from .schemas import make_finding
from .tables import ExtractedTable

logger = logging.getLogger(__name__)

# 「每个组件都有贡献」类声称（保守：只匹配明确的 each/every/all 形式）
_UNIVERSAL_CLAIM_RE = re.compile(
    r"(each|every|all)\s+(of\s+the\s+)?"
    r"(components?|modules?|parts?)\s+(contributes?|helps?|improves?|is\s+effective"
    r"|plays?\s+a\s+(role|part))",
    re.IGNORECASE,
)

# 移除组件行识别："w/o X" / "without X" / "-X" / "Ours - X"
_REMOVAL_ROW_RE = re.compile(r"(?:w/?o|without|no)\s+\w+|^\s*-\s*\w+", re.IGNORECASE)

_FULL_ROW_RE = re.compile(r"\b(ours?|full|complete|proposed|full model)\b", re.IGNORECASE)


def find_ablation_tables(tables: list[ExtractedTable]) -> list[ExtractedTable]:
    """定位 ablation 表：caption/表头含 ablation 关键词，或含 w/o 行。"""
    result = []
    for t in tables:
        caption_hit = t.table_id and "ablation" in (t.table_id or "").lower()
        header_hit = any("ablation" in (h or "").lower() for h in t.header)
        row_hit = any(_REMOVAL_ROW_RE.search(row[0] if row else "") for row in t.body)
        if header_hit or row_hit or caption_hit:
            result.append(t)
    return result


def _first_numeric_column(table: ExtractedTable) -> int | None:
    """第一个数值列的索引（跳过行名列）。"""
    n_cols = max((len(r) for r in table.rows), default=0)
    for c in range(1, n_cols):
        vals = [_parse_number(row[c]) for row in table.body if c < len(row)]
        if any(v is not None for v in vals):
            return c
    return None


def check_ablation_consistency(full_text: str, tables: list[ExtractedTable]) -> list[dict]:
    """确定性检查：移除组件行不差于全模型 + 通用贡献声称 → Finding。"""
    ablation_tables = find_ablation_tables(tables)
    if not ablation_tables:
        return []

    universal_claims = [s for s in split_sentences(full_text) if _UNIVERSAL_CLAIM_RE.search(s)]

    findings: list[dict] = []
    for t in ablation_tables:
        col = _first_numeric_column(t)
        if col is None:
            continue
        full_val: float | None = None
        for row in t.body:
            name = (row[0] if row else "") or ""
            v = _parse_number(row[col]) if col < len(row) else None
            if v is None:
                continue
            if _FULL_ROW_RE.search(name) and full_val is None:
                full_val = v
        if full_val is None:
            continue
        # 移除行指标不低于全模型（含 0.1 抖动容差）
        bad_rows = []
        for row in t.body:
            name = ((row[0] if row else "") or "").strip()
            v = _parse_number(row[col]) if col < len(row) else None
            if v is None or not _REMOVAL_ROW_RE.search(name):
                continue
            if v >= full_val - 0.1:
                bad_rows.append(f"{name}={v:g}")
        if not bad_rows:
            continue
        evidence = [{"type": "table", "table_id": t.label(), "page": t.page}]
        if universal_claims:
            evidence.append({"type": "text", "snippet": universal_claims[0].strip()})
            title = f"ablation 数据不支持通用贡献声称（{t.label()}）"
            explanation = (
                "正文声称每个组件均有贡献，但移除组件后指标未下降；"
                "可能是该组件冗余或其收益在其他指标上"
            )
        else:
            title = f"ablation 移除组件未造成指标下降（{t.label()}）"
            explanation = (
                "移除组件后指标不降反升/持平，该组件的贡献存疑；也可能指标列选择有误，需人工核对"
            )
        findings.append(
            make_finding(
                "ABLATION_UNSUPPORTED",
                title=title,
                page=t.page,
                claim="、".join(universal_claims[:1]) or "（正文无显式通用贡献声称）",
                computed=f"全模型={full_val:g}，移除行: {'; '.join(bad_rows[:5])}",
                method="移除组件行指标 ≥ 全模型行指标（容差 0.1）",
                evidence_sources=evidence,
                normal_explanation=explanation,
                needs_human_review=True,
            )
        )
    return findings
