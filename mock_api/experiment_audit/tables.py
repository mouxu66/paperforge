"""表格提取与交叉比对基础设施（P0 各数值检测共用）。

技术选型（ADR：替代指南中的 camelot）：
- camelot 依赖 Ghostscript，Windows 安装极不稳定；PyMuPDF 1.28 的
  ``page.find_tables()`` 纯 Python 即可完成网格/无线表格识别，零新依赖。
- 表号（Table N）通过「表格 bbox 邻近文本块匹配 caption」定位，
  供 metrics.py 做正文-表格交叉比对时引用。
- P0-8（标准差/显著性缺失）的确定性部分也在本模块：结果表无 ± 值
  且全文无统计检验描述 → STD_OR_SIGNIFICANCE_MISSING。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..figure_claims import _normalize_metric, _parse_number
from .schemas import make_finding

logger = logging.getLogger(__name__)

_TABLE_CAPTION_RE = re.compile(r"\bTable\s+(\d+)\s*[:.]?", re.IGNORECASE)


def pymupdf_available() -> bool:
    """PyMuPDF 是否可导入（表格提取的硬依赖）。

    供编排层在运行前探测：缺失时显式记 skipped，避免把「提取不到表」
    和「根本没装提取器」混淆。
    """
    try:
        import fitz  # noqa: F401

        return True
    except ImportError:
        return False


# P0-8：全文统计检验线索（任一命中即视为「已报告某种不确定度/显著性」）
_SIGNIFICANCE_TEXT_RE = re.compile(
    r"standard deviation|standard error|confidence interval|error bar"
    r"|statistical(ly)?\s+significan|p\s*[-=]\s*0?\.\d|t-test|paired test"
    r"|bootstrap|±|\bstd\b",
    re.IGNORECASE,
)


@dataclass
class ExtractedTable:
    """一张提取出的表格。"""

    page: int  # 1-based 页码
    table_index: int  # 页内序号（0-based）
    table_id: str | None  # "Table 2"（caption 匹配到时），否则 None
    bbox: list[float] = field(default_factory=list)  # PDF 坐标 [x0,y0,x1,y1]
    rows: list[list[str]] = field(default_factory=list)  # 原始单元格文本（None→""）

    @property
    def header(self) -> list[str]:
        return self.rows[0] if self.rows else []

    @property
    def body(self) -> list[list[str]]:
        return self.rows[1:] if len(self.rows) > 1 else []

    def label(self) -> str:
        return self.table_id or f"Table(p{self.page}#{self.table_index})"

    def numeric_cells(self) -> list[float]:
        """表内所有可解析数值（供密度判定）。"""
        vals: list[float] = []
        for row in self.rows:
            for cell in row:
                v = _parse_number(cell or "")
                if v is not None:
                    vals.append(v)
        return vals

    def header_metrics(self) -> list[str]:
        """表头中的规范指标名（accuracy/f1/bleu...），用于判定结果表。"""
        out: list[str] = []
        for cell in self.header:
            for token in re.split(r"[\s/()]+", (cell or "").lower()):
                m = _normalize_metric(token)
                if m and m not in out:
                    out.append(m)
        return out

    def has_uncertainty_values(self) -> bool:
        """是否含 ± 形式的不确定度值（82.1±0.3 / 82.1 ± 0.3）。"""
        return any("±" in (cell or "") for row in self.rows for cell in row)


def extract_tables_from_pdf(pdf_bytes: bytes) -> list[ExtractedTable]:
    """从 PDF 字节流提取全部表格（pymupdf find_tables）。

    提取策略：优先 lines 策略（网格/实线表），单页 0 结果时回退
    text 策略（三线表/booktabs 无线表格常见，lines 会漏）。
    失败 fail-open 返回空列表——表格不可得时数值类检测自动跳过，
    由编排层在 checks_run 标注，不中断审计。
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("表格提取不可用：未安装 pymupdf")
        return []

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:  # noqa: BLE001 - 表格提取 fail-open
        logger.warning("表格提取打开 PDF 失败: %s", e)
        return []

    tables: list[ExtractedTable] = []
    try:
        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            try:
                finder = page.find_tables()
            except Exception as e:  # noqa: BLE001 - 单页失败不影响其他页
                logger.warning("find_tables 第 %d 页失败: %s", page_idx + 1, e)
                continue
            # caption 候选文本块：(bbox, text)，用于表号匹配
            blocks = [
                (b[:4], b[4])
                for b in page.get_text("blocks")
                if len(b) >= 5 and isinstance(b[4], str) and b[4].strip()
            ]
            # 三线表/无线表格 lines 策略常漏：单页无结果时回退 text 策略
            if not finder.tables and page.get_text().strip():
                try:
                    finder = page.find_tables(strategy="text")
                except Exception:  # noqa: BLE001 - 回退失败不影响 lines 结果
                    pass
            for t_idx, tab in enumerate(finder.tables):
                try:
                    raw = tab.extract()
                except Exception:  # noqa: BLE001 - 单表失败跳过
                    continue
                rows = [[(c or "").strip() for c in row] for row in raw if row]
                if not rows:
                    continue
                tables.append(
                    ExtractedTable(
                        page=page_idx + 1,
                        table_index=t_idx,
                        table_id=_match_table_caption(list(tab.bbox), blocks),
                        bbox=list(tab.bbox),
                        rows=rows,
                    )
                )
    finally:
        doc.close()
    return tables


def _match_table_caption(bbox: list[float], blocks: list[tuple[tuple, str]]) -> str | None:
    """在表格 bbox 上下方寻找 'Table N' caption，返回规范表号。

    取表格正上方或正下方（垂直距离 ≤ 表格高度、水平有重叠）最近的
    含 'Table N' 文本块；轻微优先上方（多数会议模板 caption 在表上）。
    水平无重叠的引用句（"as shown in Table 3"）会被排除。
    """
    x0, y0, x1, y1 = bbox
    table_h = max(y1 - y0, 1.0)
    best: tuple[float, str] | None = None  # (distance, "Table N")
    for (bx0, by0, bx1, by1), text in blocks:
        m = _TABLE_CAPTION_RE.search(text)
        if not m:
            continue
        if bx1 < x0 or bx0 > x1:  # 水平无重叠 → 不是本表 caption
            continue
        if by1 <= y0:  # 表格上方
            dist = y0 - by1
        elif by0 >= y1:  # 表格下方
            dist = by0 - y1 + table_h * 0.1
        else:
            continue  # 与表格重叠的行通常是表体内容
        if dist <= table_h and (best is None or dist < best[0]):
            best = (dist, f"Table {int(m.group(1))}")
    return best[1] if best else None


def is_results_table(table: ExtractedTable, min_numeric: int = 4) -> bool:
    """是否为实验结果表：表头含已知指标 或 数值密度足够高。"""
    if table.header_metrics():
        return True
    return len(table.numeric_cells()) >= min_numeric


def find_table_by_id(tables: list[ExtractedTable], table_id: str) -> ExtractedTable | None:
    """按 'Table N' 查找表格（忽略大小写）。"""
    want = table_id.strip().lower()
    for t in tables:
        if t.table_id and t.table_id.lower() == want:
            return t
    return None


def find_cell_value(
    table: ExtractedTable, row_match: str, col_match: str
) -> tuple[float, str] | None:
    """按行首关键词 + 列头关键词定位单元格数值。

    用于正文-表格交叉比对：如行 'Ours'、列 'F1' → 82.3。
    命中返回 (数值, 单元格原文)；未命中返回 None（不猜测）。
    """
    header = [(h or "").lower() for h in table.header]
    col_idx: int | None = None
    for i, h in enumerate(header):
        if col_match.lower() in h:
            col_idx = i
            break
    if col_idx is None:
        return None
    row_key = row_match.strip().lower()
    for row in table.body:
        first = (row[0] if row else "").strip().lower()
        if not first or row_key not in first:
            continue
        if col_idx < len(row):
            v = _parse_number(row[col_idx])
            if v is not None:
                return v, row[col_idx]
    return None


def detect_significance_missing(tables: list[ExtractedTable], full_text: str) -> list[dict]:
    """P0-8：检测结果表缺标准差/显著性报告。

    判定（保守，防误报）：
    1. 存在至少一张结果表（表头含已知指标）；
    2. 所有结果表均无 ± 值；
    3. 全文无任何统计检验/不确定度描述线索。
    三条同时满足 → 每条无 ± 的结果表产出一条 Finding。
    """
    results_tables = [t for t in tables if t.header_metrics()]
    if not results_tables:
        return []
    if any(t.has_uncertainty_values() for t in results_tables):
        return []
    if _SIGNIFICANCE_TEXT_RE.search(full_text or ""):
        return []

    findings = []
    for t in results_tables:
        metrics = ", ".join(t.header_metrics()[:5])
        findings.append(
            make_finding(
                "STD_OR_SIGNIFICANCE_MISSING",
                title=f"{t.label()} 只报告均值，缺少标准差/显著性",
                page=t.page,
                bbox=t.bbox or None,
                claim=f"表格报告指标: {metrics}，无 ± 值",
                computed="全文未发现 std/error bar/p-value 描述",
                method="结果表单元格无 ± 值 且 全文无统计检验关键词",
                evidence_sources=[{"type": "table", "table_id": t.label(), "page": t.page}],
                normal_explanation=(
                    "单 seed 结果在部分场景可接受，但指标差异较小时"
                    "无法排除随机性，建议补充多次运行统计"
                ),
                needs_human_review=True,
            )
        )
    return findings
