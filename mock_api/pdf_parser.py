"""PDF/DOCX 解析与上传入库工具。

负责：
- PDF 元数据提取（标题 / 作者 / 年份 / 摘要）
- DOCX 文档元数据提取
- 加密 / 损坏文件检测
- OCR 降级（扫描版 PDF 自动识别）
- 文本分块切割（chunk → chunk_count / index_size）
- 基于内容哈希生成 paper_id
- PDF 文件存储到磁盘（支持断点续解析）
- 单文件解析入库完整流程
- ZIP 压缩包批量解析入库（PDF + DOCX）
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from . import crud
from .schemas import UploadedPaper, ZipUploadResponse
from .utils.tempfiles import safe_write

logger = logging.getLogger(__name__)

# 单次批量上传上限
MAX_BATCH_SIZE = 20
# ZIP 批量上传限制
MAX_ZIP_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_ZIP_PDF_COUNT = 100  # 单次最多处理 100 个文件
# 支持的文档类型
SUPPORTED_EXTENSIONS = {".pdf", ".docx"}  # .doc (旧格式) 不支持，需先转换为 .docx

# chunk 切割参数
CHUNK_SIZE = 512  # 每块的最大字符数
CHUNK_OVERLAP = 64  # 相邻块之间的重叠字符数

# PDF 存储目录（相对于项目根）
UPLOADS_DIR_NAME = "uploads"


class PDFEncryptedError(Exception):
    """PDF 已加密，需要密码才能读取。"""


class PDFParseError(Exception):
    """PDF 解析失败（文件损坏、格式错误等）。"""


def _get_uploads_dir() -> Path:
    """获取 PDF 存储目录（项目根下的 uploads/），不存在则创建。"""
    project_root = Path(__file__).resolve().parent.parent
    uploads = project_root / UPLOADS_DIR_NAME
    uploads.mkdir(parents=True, exist_ok=True)
    return uploads


def _save_pdf_to_disk(content: bytes, paper_id: str) -> str | None:
    """将 PDF 文件保存到 uploads/ 磁盘目录。

    Returns:
        文件绝对路径；保存失败返回 None。
    """
    try:
        uploads_dir = _get_uploads_dir()
        pdf_path = uploads_dir / f"{paper_id}.pdf"
        if not safe_write(pdf_path, content):
            logger.warning("保存 PDF 到磁盘失败 [%s]", paper_id)
            return None
        return str(pdf_path)
    except Exception as e:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        logger.warning("保存 PDF 到磁盘失败 [%s]: %s", paper_id, e)
        return None


def split_into_chunks(
    text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """将文本切割成多个 chunk。

    确保至少返回 1 个 chunk 块（即使文本为空也返回 [""]）。
    使用滑动窗口切割，相邻块之间保留 overlap 个字符的重叠。

    Args:
        text: 待切割的文本。
        chunk_size: 每块的最大字符数。
        overlap: 相邻块之间的重叠字符数。

    Returns:
        chunk 字符串列表。
    """
    text = text.strip()
    if not text:
        return [""]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start += chunk_size - overlap

    return chunks if chunks else [""]


def _extract_pdf_metadata(content: bytes, filename: str) -> dict[str, Any]:
    """从 PDF 二进制内容中提取元数据。

    Returns:
        {"title": str, "authors": list[str], "year": int, "abstract": str}
    """
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise PDFParseError("未安装 pypdf") from e

    try:
        reader = PdfReader(BytesIO(content))
    except Exception as e:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        # pypdf 对加密文件抛出的异常类型不统一，按信息判断
        msg = str(e).lower()
        if "encrypt" in msg or "password" in msg:
            raise PDFEncryptedError(filename) from e
        raise PDFParseError(str(e)) from e

    # 再次检查加密标记
    if reader.is_encrypted:
        try:
            # decrypt("") 不抛异常不代表解密成功，需检查返回值（0=未解密）
            result = reader.decrypt("")
        except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
            raise PDFEncryptedError(filename) from None
        if not result:
            raise PDFEncryptedError(filename)

    # 1. 元数据（标题 / 作者 / 年份）
    title = ""
    authors: list[str] = []
    year = 0
    try:
        meta = reader.metadata
        if meta:
            raw_title = meta.title if hasattr(meta, "title") else None
            title = (raw_title or "").strip()
            raw_author = meta.author if hasattr(meta, "author") else None
            if raw_author:
                authors = [a.strip() for a in re.split(r"[;,&]", raw_author) if a.strip()]
            raw_date = meta.creation_date if hasattr(meta, "creation_date") else None
            if raw_date:
                m = re.search(r"(\d{4})", str(raw_date))
                if m:
                    year = int(m.group(1))
    except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        pass

    # 从全文第一页提取年份（PDF metadata 经常缺失年份）
    if not year:
        try:
            first_page_text = ""
            for page in reader.pages[:3]:
                try:
                    first_page_text += page.extract_text() or ""
                except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
                    continue
            # 匹配常见的年份模式：Submitted to XXX, YYYY / (YYYY) / YYYY年
            year_patterns = [
                r"(?:19|20)\d{2}",  # 四位年份
                r"submitted\s+(?:to\s+\w+[\s,]*)?(?:on\s+)?(?:19|20)\d{2}",  # Submitted ... YYYY
                r"(?:19|20)\d{2}[./\s]?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)",
            ]
            years_found = []
            for pattern in year_patterns:
                for m in re.finditer(
                    r"\b(19|20)\d{2}\b", first_page_text[:2000]
                ):  # 只看前 2000 字符
                    y = int(m.group(0))
                    if 1990 <= y <= 2030:
                        years_found.append(y)
            if years_found:
                # 取出现最频繁的合理年份
                from collections import Counter

                year = Counter(years_found).most_common(1)[0][0]
        except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
            pass

    # 从文件名提取年份（仅当 PDF metadata 和全文都提取失败时）
    if not year:
        try:
            m = re.search(r"\b(19|20)\d{2}\b", filename)
            if m:
                y = int(m.group(0))
                if 1990 <= y <= 2030:
                    year = y
        except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
            pass

    # 标题为空时用文件名兜底
    if not title:
        title = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE).strip() or filename

    # 2. 摘要：优先从 PDF metadata 提取，降级到正文关键词匹配
    abstract = ""
    content_status = "empty"

    # 2a：从 PDF metadata 的 subject / keywords 提取
    try:
        if meta:
            subject = getattr(meta, "subject", None) or ""
            _keywords = getattr(meta, "keywords", None) or ""
            if subject.strip():
                abstract = subject.strip()
                content_status = "metadata"
    except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        pass

    # 2b：从前 3 页正文中搜索 Abstract / 摘要关键词后的段落
    if not abstract:
        try:
            pages_text: list[str] = []
            for i, page in enumerate(reader.pages):
                if i >= 3:
                    break
                try:
                    txt = page.extract_text() or ""
                    if txt:
                        pages_text.append(txt)
                except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
                    continue
            full_text = "\n".join(pages_text).strip()

            if full_text:
                # 搜索 Abstract 关键词
                abs_match = re.search(
                    r"(?:^|\n)\s*(?:Abstract|ABSTRACT|摘要)\b[\s\n]*[—–:：\n]+?\s*(.{100,2000}?)(?=\n\s*(?:\d+\.?\s+)?(?:Introduction|INTRO|1\.|I\.|Keywords|KEYWORDS|关键词|$))",
                    full_text,
                    re.DOTALL | re.IGNORECASE,
                )
                if abs_match:
                    abstract = abs_match.group(1).strip()[:800]
                    content_status = "abstract_section"
                else:
                    # 降级：取前 500 字符
                    abstract = full_text[:500].strip()
                    content_status = "body_fallback" if abstract else "empty"
        except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
            pass

    # 2c：如果 OCR 降级后仍为空，记录警告
    if not abstract:
        logger.warning("[摘要提取] 失败: %s — 未找到任何可用文本", filename)
    else:
        logger.info(
            "[摘要提取] 成功: %s — %d chars, 来源=%s", filename, len(abstract), content_status
        )

    return {"title": title, "authors": authors, "year": year, "abstract": abstract}


def _gen_paper_id(content: bytes, title: str) -> str:
    """基于文件内容哈希 + 标题生成 paper_id（去重）。

    对标题做严格 sanitize：只保留字母、数字、下划线，避免中文、括号、
    空格等特殊字符导致的路径解析/URL 问题。若标题过短，则截取内容哈希
    避免 paper_id 重复。
    """
    h = hashlib.sha1(content).hexdigest()[:12]
    # 严格 sanitize：仅保留 A-Za-z0-9_，连续特殊字符合并为单个下划线，首尾去下划线
    safe_title = re.sub(r"[^A-Za-z0-9_]", "_", title)
    safe_title = re.sub(r"_+", "_", safe_title).strip("_")
    # 保留足够长度以便可读，但不超过 40 字符
    safe_title = (safe_title or f"doc_{h}")[:40]
    return f"upload_{h}_{safe_title}"


def extract_full_text(content: bytes) -> str:
    """提取 PDF 全文文本（覆盖所有页面）。

    B1：将 PDF 全文内容纳入 FTS5 索引，提升搜索/问答检索颗粒度。
    - 使用 pypdf 遍历所有页面并拼接文本。
    - 加密/损坏文件或提取失败时返回空字符串（不阻塞入库流程）。
    - 不复用 _extract_pdf_metadata 中前 3 页的提取逻辑，因为这里需要全量文本。

    Args:
        content: PDF 文件二进制内容。

    Returns:
        全文文本（去尾空白）；失败时返回空字符串。
    """
    if not content:
        return ""
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""

    try:
        reader = PdfReader(BytesIO(content))
    except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        return ""

    # 加密文件：尝试空密码解密；失败则返回空
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                return ""
        except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
            return ""

    page_count = len(reader.pages)
    pages_text: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            txt = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
            continue
        if txt:
            pages_text.append(txt)
    full_text = "\n".join(pages_text).strip()

    # 诊断日志：记录每页字符数
    total_chars = sum(len(t) for t in pages_text)
    logger.info(
        "extract_full_text: %d pages, %d chars total (page chars: %s)",
        len(pages_text),
        total_chars,
        [len(t) for t in pages_text],
    )
    if not full_text and page_count > 0:
        logger.warning("extract_full_text: pypdf 提取为空 (%d pages)，将触发 OCR 降级", page_count)

    return full_text


def _vector_render_enabled() -> bool:
    """Return whether vector figure rendering is enabled by configuration."""
    try:
        from .settings import get_settings

        return bool(get_settings().depth_vector_render_enabled)
    except Exception:  # noqa: BLE001 - extraction should remain fail-soft
        return True


def _extract_captions(page) -> list[tuple[tuple[float, float, float, float], str, int | None]]:
    """从单页文本块中识别图注。

    返回: [(bbox, caption_text, figure_number), ...]
    bbox 为 fitz.Rect 坐标 (x0, y0, x1, y1)；figure_number 从图注文字中解析，如 Fig. 3 → 3。
    """
    import re

    try:
        blocks = page.get_text("blocks")
    except Exception:  # noqa: BLE001
        return []

    caption_re = re.compile(r"^(Figure|Fig\.?)(\s*)(\d+)", re.IGNORECASE)
    captions: list[tuple[tuple[float, float, float, float], str, int | None]] = []
    for block in blocks:
        # blocks 格式: (x0, y0, x1, y1, text, block_no, block_type)
        if len(block) < 6:
            continue
        x0, y0, x1, y1, text, *_ = block
        text = (text or "").strip()
        if not text:
            continue
        m = caption_re.match(text)
        if m:
            fig_num = int(m.group(3)) if m.group(3) else None
            captions.append(((float(x0), float(y0), float(x1), float(y1)), text, fig_num))
    return captions


# ---------------------------------------------------------------------------
# P0-B: 纯文本层"图文一致性"证据抽取（不依赖渲染 / 不依赖 PaperFigure 表）
# 直接在已抽取的 full_text 上解析图注 + 数值断言，供 DEPTH QF 节点在
# 没有渲染图时也能产出非中性的图文一致性信号（baseline 对照用）。
# ---------------------------------------------------------------------------

_TEXT_FIG_CAPTION_RE = re.compile(r"(?im)^(?:figure|fig\.?)\s*(\d+)\s*[:.\-]?\s*(.*)$")
_TEXT_NEWFIG_RE = re.compile(r"(?im)^(?:figure|fig\.?|table|tab\.?)\s*\d+\b")
_TEXT_SECTION_RE = re.compile(r"(?im)^[0-9]+(?:\.[0-9]+)*\s+[A-Z][A-Za-z]")
_METRIC_RE = re.compile(
    r"(?i)\b(accuracy|f1|auc|precision|recall|bleu|rouge(?:[-_ ]?[a-z])?|"
    r"loss|error|score|map|ap|iou|dice|perplexity|throughput|speedup|"
    r"improvement|gain|top[-\s]?1|top[-\s]?5|params?|flops?)\b"
)
_NUMBER_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3})?\s*%|\b\d{1,3}\.\d{1,4}\b|\b\d{2,4}\b")


def extract_text_figure_evidence(text: str, max_figures: int = 12) -> list[dict[str, Any]]:
    """从 full_text 抽取图注 + 数值断言（纯文本层，无需渲染 / 图抽取）。

    与 _extract_captions（基于 PDF 文本块、需 figure bbox 才能落库）不同，
    本函数直接在已抽取的论文正文字符串上工作：识别 "Figure/Fig N" 开头的图注，
    抓取图注中的数值断言，并到正文其余部分核验该数值是否出现
    （source_text_span）。返回结构化证据供 QF 节点做确定性图文一致性评分。

    Returns:
        [{figure_number, caption_text, caption_numbers:[{value,metric}],
          body_matches:[{value,metric,found,spans}]}, ...]
        仅计入能识别且含可校验数值的图注。
    """
    if not text or not text.strip():
        return []
    lines = text.splitlines()
    n = len(lines)
    # 每行在原文中的起始字符偏移（用于精确排除"图注自身出现"的误判）
    offsets: list[int] = []
    acc = 0
    for line in lines:
        offsets.append(acc)
        acc += len(line) + 1  # +1 兼容换行符

    evidences: list[dict[str, Any]] = []
    i = 0
    while i < n and len(evidences) < max_figures:
        m = _TEXT_FIG_CAPTION_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        fig_num = int(m.group(1))
        caption_parts = [m.group(2)] if m.group(2).strip() else []
        j = i + 1
        # 续接图注行：直到空行 / 新图或表 / 章节标题（如 "3.3 Heading"）
        while j < n and len(caption_parts) < 6:
            nxt = lines[j].strip()
            if not nxt:
                break
            if _TEXT_NEWFIG_RE.match(nxt):
                break
            if _TEXT_SECTION_RE.match(nxt):
                break
            caption_parts.append(nxt)
            j += 1
        caption = " ".join(p for p in caption_parts if p).strip()
        if len(caption) < 8:
            i = j if j > i else i + 1
            continue
        # 提取图注数值，优先带度量关键字（accuracy/F1/...）的
        caption_numbers: list[dict[str, Any]] = []
        for num_m in _NUMBER_RE.finditer(caption):
            val = num_m.group(0).strip()
            ctx = caption[max(0, num_m.start() - 25) : num_m.end() + 5]
            metric = _METRIC_RE.search(ctx)
            caption_numbers.append(
                {"value": val, "metric": metric.group(1).lower() if metric else None}
            )
        if not caption_numbers:
            i = j if j > i else i + 1
            continue
        cap_start = offsets[i]
        cap_end = (offsets[j - 1] + len(lines[j - 1])) if j - 1 < n else cap_start
        evidences.append(
            {
                "figure_number": fig_num,
                "caption_text": caption[:400],
                "caption_numbers": caption_numbers,
                "_cap_start": cap_start,
                "_cap_end": cap_end,
            }
        )
        i = j if j > i else i + 1

    # 把每个图注数值与正文核验：排除图注自身出现，仅在正文其余部分命中才算一致
    for ev in evidences:
        cap_start = ev.pop("_cap_start", 0)
        cap_end = ev.pop("_cap_end", 0)
        matches: list[dict[str, Any]] = []
        for cn in ev["caption_numbers"]:
            val = cn["value"]
            found = False
            body_span = ""
            try:
                for fm in re.finditer(re.escape(val), text):
                    s, e = fm.span()
                    if cap_start <= s < cap_end:
                        continue  # 图注自身出现，跳过
                    found = True
                    body_span = text[max(0, s - 35) : e + 35].replace("\n", " ")
                    break
            except Exception:  # noqa: BLE001
                pass
            matches.append(
                {
                    "value": val,
                    "metric": cn["metric"],
                    "found": found,
                    "spans": [body_span] if body_span else [],
                }
            )
        ev["body_matches"] = matches
    return evidences


# M1: 硬匹配阈值（可调）
HARD_MATCH_PROXIMITY_THRESHOLD = 0.5
HARD_MATCH_OVERLAP_THRESHOLD = 0.3
HARD_MATCH_CONFIDENCE = 0.95
GRAY_ZONE_MIN_CONFIDENCE = 0.6


def _compute_caption_match_confidence(
    fig: dict[str, Any],
    caption: tuple[tuple[float, float, float, float], str, int | None],
    page_height: float,
    fig_num: int | None,
) -> tuple[float, str]:
    """M1: 确定性硬匹配层 —— 根据多信号计算 figure-caption 配对置信度与标签。

    返回 (confidence, label)。
    - label == "hard": 高置信度确定性匹配（精确图号 + 空间邻近）。
    - label == "gray": 中等置信度，需要 VLM/规则二次校验。
    - label == "unmatched": 无法匹配。
    """
    if page_height <= 0:
        return 0.0, "unmatched"

    bbox, text, cap_num = caption
    fx0, fy0, fx1, fy1 = fig["bbox"]
    fig_center_y = (fy0 + fy1) / 2
    cx0, cy0, cx1, cy1 = bbox
    cap_center_y = (cy0 + cy1) / 2
    vertical_dist = abs(cap_center_y - fig_center_y)
    normalized_dist = min(vertical_dist / page_height, 1.0)

    # 信号 1：精确图号匹配
    exact_num_match = fig_num is not None and cap_num == fig_num
    # 信号 2：垂直邻近（距离越近越可信）
    proximity_score = max(0.0, 1.0 - normalized_dist * 2.0)
    # 信号 3：水平重叠（caption 与 figure 在 x 轴上有重叠更可信）
    horizontal_overlap = max(0.0, min(fx1, cx1) - max(fx0, cx0)) / max(fx1 - fx0, 1e-6)
    overlap_score = min(horizontal_overlap, 1.0)

    # 硬匹配：精确图号 + 垂直邻近 + 一定水平重叠
    if (
        exact_num_match
        and proximity_score >= HARD_MATCH_PROXIMITY_THRESHOLD
        and overlap_score >= HARD_MATCH_OVERLAP_THRESHOLD
    ):
        return HARD_MATCH_CONFIDENCE, "hard"

    # 灰带：精确图号但空间较远，或邻近但图号不一致，需 VLM 二次判断
    confidence = (
        0.4 + (0.3 if exact_num_match else 0.0) + proximity_score * 0.2 + overlap_score * 0.1
    )
    if confidence >= GRAY_ZONE_MIN_CONFIDENCE:
        return confidence, "gray"
    return confidence, "unmatched"


def _associate_captions(
    page,
    figures: list[dict[str, Any]],
) -> None:
    """Associate captions to figures by number and vertical proximity.

    Rules:
    - figures and captions are sorted by vertical center;
    - exact number match first (figure["figure_number"] == caption_num);
    - remaining items are matched by nearest vertical distance;
    - one caption is assigned to at most one figure.
    Each figure also gets match_confidence and match_label (M1 hard matching).
    """
    if not figures:
        return
    captions = _extract_captions(page)
    if not captions:
        for fig in figures:
            fig["match_confidence"] = 0.0
            fig["match_label"] = "unmatched"
        return

    # ensure every figure has a figure_number key
    for fig in figures:
        if "figure_number" not in fig:
            fig["figure_number"] = None

    try:
        page_height = float(page.rect.height) if page.rect.height > 0 else 1.0
    except Exception:  # noqa: BLE001 - 兼容测试中的 mock page
        page_height = 1.0

    # sort figures top-to-bottom by vertical center
    fig_order = sorted(
        range(len(figures)),
        key=lambda i: (figures[i]["bbox"][1] + figures[i]["bbox"][3]) / 2,
    )

    used_fig: set[int] = set()
    used_cap: set[int] = set()

    # Pre-step: populate figure_number by document-order pairing.
    cap_order = sorted(
        range(len(captions)),
        key=lambda i: (captions[i][0][1] + captions[i][0][3]) / 2,
    )
    for k, fi in enumerate(fig_order):
        if k < len(cap_order):
            assigned = captions[cap_order[k]][2]
            if assigned is not None:
                figures[fi]["figure_number"] = assigned

    # Pass 1: exact number match with M1 confidence
    for fi in fig_order:
        fig = figures[fi]
        fig_num = fig.get("figure_number")
        best_conf = 0.0
        best_label = "unmatched"
        best_ci: int | None = None
        for ci, caption in enumerate(captions):
            if ci in used_cap:
                continue
            conf, label = _compute_caption_match_confidence(fig, caption, page_height, fig_num)
            if conf > best_conf and label in ("hard", "gray"):
                best_conf = conf
                best_label = label
                best_ci = ci
        if best_ci is not None:
            _bbox, text, cap_num = captions[best_ci]
            fig["caption_text"] = text
            fig["match_confidence"] = round(best_conf, 4)
            fig["match_label"] = best_label
            if fig.get("figure_number") is None:
                fig["figure_number"] = cap_num
            used_fig.add(fi)
            used_cap.add(best_ci)

    # Pass 2: greedy proximity match for remaining figures/captions
    candidates: list[tuple[float, int, int]] = []
    for fi in fig_order:
        if fi in used_fig:
            continue
        fig = figures[fi]
        fx0, fy0, fx1, fy1 = fig["bbox"]
        fig_center_y = (fy0 + fy1) / 2
        for ci, (bbox, _text, _num) in enumerate(captions):
            if ci in used_cap:
                continue
            cx0, cy0, cx1, cy1 = bbox
            cap_center_y = (cy0 + cy1) / 2
            dist = abs(cap_center_y - fig_center_y)
            candidates.append((dist, fi, ci))
    candidates.sort(key=lambda x: x[0])

    for _dist, fi, ci in candidates:
        if fi in used_fig or ci in used_cap:
            continue
        fig = figures[fi]
        caption = captions[ci]
        _bbox, text, cap_num = caption
        conf, label = _compute_caption_match_confidence(
            fig, caption, page_height, fig.get("figure_number")
        )
        fig["caption_text"] = text
        fig["match_confidence"] = round(conf, 4)
        fig["match_label"] = label
        if fig.get("figure_number") is None:
            fig["figure_number"] = cap_num
        used_fig.add(fi)
        used_cap.add(ci)

    # Mark any unmatched figures
    for fi in fig_order:
        if fi not in used_fig:
            figures[fi].setdefault("match_confidence", 0.0)
            figures[fi].setdefault("match_label", "unmatched")


def route_vlm_for_figure(figure: dict[str, Any], *, ocr_text: str = "") -> dict[str, Any]:
    """M2: 灰带 VLM 路由 —— 根据 figure 元数据决定是否需要 VLM 解读。

    返回 {"decision": "vlm" | "rule_only" | "skip", "reason": str}。
    - "vlm": 需要调用 VLM 生成摘要。
    - "rule_only": 用规则/OCR 文本作为摘要，跳过 VLM。
    - "skip": 信息过少，不生成摘要。

    当 figure_type 可用时，优先基于结构化类型做路由：
    - 表格/示意图：规则摘要优先（数值校验/空间关系）。
    - 折线/柱状/散点/热力图：使用 VLM 生成的语义摘要。
    """
    match_label = figure.get("match_label") or "unmatched"
    match_confidence = figure.get("match_confidence") or 0.0
    caption_text = (figure.get("caption_text") or "").strip()
    ocr_text_clean = (ocr_text or figure.get("ocr_text") or "").strip()
    ocr_len = len(ocr_text_clean)
    figure_type = (figure.get("figure_type") or "other").strip().lower()

    # 结构化类型路由
    if figure_type in ("table", "diagram"):
        return {"decision": "rule_only", "reason": f"{figure_type}_rule_first"}

    if figure_type in ("line_chart", "bar_chart", "scatter", "heatmap"):
        return {"decision": "vlm", "reason": f"{figure_type}_semantic_summary"}

    # 兼容旧逻辑：硬匹配 + 有 caption + OCR 不太长 -> 规则摘要足够
    if match_label == "hard" and caption_text and 0 < ocr_len <= 300:
        return {"decision": "rule_only", "reason": "hard_match_with_short_ocr"}

    # 灰带或置信度不足 -> VLM
    if match_label in ("gray", "unmatched") or match_confidence < 0.6:
        return {"decision": "vlm", "reason": "gray_zone_or_low_confidence"}

    # OCR 文本很长或很短的边界情况：VLM
    if ocr_len > 300 or ocr_len == 0:
        return {"decision": "vlm", "reason": "ocr_length_boundary"}

    return {"decision": "skip", "reason": "insufficient_signals"}


# ---------------------------------------------------------------------------

# 图状区域最小面积占比（占页面面积）。矢量与位图两条抽取路径共用此阈值，
# 低于该占比的区域视为图标/logo/装饰/碎图，不抽为 figure —— 既避免过分割，
# 也避免下游消费层对每个碎片跑 VLM 导致显存/时间爆炸。
FIGURE_MIN_AREA_FRAC = 0.02
# 位图最大面积占比：高于该占比（占满整页）的位图是整页快照/页面背景图
# （如排版引擎把整页渲染成一张底图），不是 figure；若照抽会让所有页面共享
# 同一张整页图，跨页互相 pHash/SIFT 匹配，产生 FIGURE_REUSE 假阳性。
FIGURE_MAX_AREA_FRAC = 0.9
# 装饰性横幅过滤（矢量路径）：横跨整页宽度、高度极小的矢量簇（期刊刊头/logo
# 横幅）是装饰而非 figure，跳过。真实图表不会横跨 >80% 页宽且高度 <5% 页高。
FIGURE_BANNER_WIDTH_FRAC = 0.8
FIGURE_BANNER_HEIGHT_FRAC = 0.05

# 矢量簇文本主导判定：正文/摘要/版权等文本块会被 cluster_drawings 聚成
# 一个「矢量簇」，但其文字词数与 ink 覆盖率远高于真实图表。超过任一阈值
# 即判定为文本块而非 figure，跳过抽取（否则摘要页会被当成图，污染 P0-9/P0-11）。
VECTOR_CLUSTER_MAX_WORDS = 50
VECTOR_CLUSTER_TEXT_INK_FRAC = 0.2


def _vector_cluster_text_stats(page, rect) -> tuple[int, float]:
    """统计矢量簇区域内的文本词数与文字 ink 覆盖率（0~1）。

    用于区分「正文文本块」与「真实图表」：文本块的词数多且文字 bbox
    覆盖簇面积比例高；图表的文字稀疏（仅轴标签/图例）。get_text 失败时
    fail-open 返回 (0, 0.0)（不拦截，保持原行为）。
    """
    try:
        words = page.get_text("words", clip=rect)
    except Exception:  # noqa: BLE001 - 文本统计失败不影响抽取
        return 0, 0.0
    if not words:
        return 0, 0.0
    area = rect.width * rect.height
    if area <= 0:
        return len(words), 0.0
    ink = 0.0
    for w in words:
        try:
            x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
        except (IndexError, TypeError):
            continue
        ix0 = max(x0, rect.x0)
        iy0 = max(y0, rect.y0)
        ix1 = min(x1, rect.x1)
        iy1 = min(y1, rect.y1)
        if ix1 > ix0 and iy1 > iy0:
            ink += (ix1 - ix0) * (iy1 - iy0)
    return len(words), ink / area


def _is_decorative_banner(rect, page_width: float, page_height: float) -> bool:
    """装饰性横幅判定：横跨整页宽、极矮的矢量簇（期刊刊头/logo 横幅）。

    真实图表不会横跨 >80% 页宽且高度 <5% 页高；这类「无文字、跨整页宽
    的细长条」是封面页的装饰刊头而非 figure，应跳过抽取（否则会被 VLM
    当实验图、被 P0-11 当数据表读）。
    """
    if page_width <= 0 or page_height <= 0:
        return False
    return (
        rect.width > page_width * FIGURE_BANNER_WIDTH_FRAC
        and rect.height < page_height * FIGURE_BANNER_HEIGHT_FRAC
    )


def extract_figures_for_paper(
    content: bytes,
    paper_id: str,
    *,
    skip_ocr: bool = False,
    vector_render_enabled: bool | None = None,
) -> list[dict[str, Any]]:
    """从 PDF 抽取 figure：位图 XObject + 矢量渲染兜底 + 图注关联。

    M0/Rec4:
    - 位图路径：保持原有 page.get_images() + extract_image 逻辑，服务于照片/扫描类 figure。
    - 矢量路径：page.cluster_drawings() 取绘图簇，get_pixmap(clip=rect, dpi=150) 渲染成 PNG；
      按面积过滤（<2%页面面积丢弃），与位图 bbox 重叠则去重。
    - 图注：page.get_text("blocks") 识别 Figure/Fig N 文本块，按空间邻近关联到 figure。

    Args:
        skip_ocr: 为 True 时跳过 OCR（门禁/批量统计场景，节省显存和时间）。
        vector_render_enabled: 显式开关矢量渲染；None 时读取系统设置。

    Returns:
        [{page, figure_index, figure_path, ocr_text, caption_text, source, bbox}, ...]
        source ∈ {"bitmap", "vector"}
    """
    try:
        import fitz
    except ImportError:
        logger.warning("figure 抽取不可用：未安装 pymupdf")
        return []

    uploads = _get_uploads_dir() / "figures" / paper_id
    uploads.mkdir(parents=True, exist_ok=True)

    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as e:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        logger.warning("figure 抽取打开 PDF 失败 [%s]: %s", paper_id, e)
        return []

    try:
        if vector_render_enabled is None:
            vector_render_enabled = _vector_render_enabled()
        results: list[dict[str, Any]] = []
        for page_no, page in enumerate(doc, start=1):
            page_results: list[dict[str, Any]] = []
            try:
                page_area = page.rect.width * page.rect.height
            except Exception:  # noqa: BLE001
                page_area = 0

            # ---- 1. 位图抽取 ----------------------------------------------------
            try:
                img_infos = page.get_images(full=True)
            except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断全文档
                img_infos = []
            for img_idx, img in enumerate(img_infos):
                xref = img[0]
                try:
                    base = doc.extract_image(xref)
                except Exception as e:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
                    logger.warning("提取图片 xref=%s 失败: %s", xref, e)
                    continue
                img_bytes = base.get("image")
                if not img_bytes:
                    continue

                # 尝试获取位图在页面上的放置 bbox
                bbox: tuple[float, float, float, float] | None = None
                try:
                    # fitz 图片出现位置近似：page.get_image_rects(xref)
                    rects = page.get_image_rects(xref)
                    if rects:
                        bbox = (rects[0].x0, rects[0].y0, rects[0].x1, rects[0].y1)
                except Exception:  # noqa: BLE001
                    pass

                # 面积过滤（与矢量路径共用最小阈值）：丢弃小于页面阈值的位图
                # （图标/logo/装饰/碎图），避免过分割 + 下游消费层对每个碎片跑 VLM 致显存/时间爆炸。
                # 同时丢弃占满整页的位图（整页快照/背景），防止跨页同图误报为 figure 复用。
                if bbox is not None and page_area > 0:
                    bw = bbox[2] - bbox[0]
                    bh = bbox[3] - bbox[1]
                    area = bw * bh
                    if area < page_area * FIGURE_MIN_AREA_FRAC:
                        continue
                    if area > page_area * FIGURE_MAX_AREA_FRAC:
                        continue
                if bbox is None:
                    # 未知 bbox 时用一个页面中心小矩形占位，避免把整张页面当作 figure
                    # 导致后续矢量簇被错误去重
                    w, h = page.rect.width, page.rect.height
                    bbox = (w * 0.25, h * 0.25, w * 0.75, h * 0.75)

                ext = base.get("ext", "png")
                fig_path = uploads / f"p{page_no}_i{img_idx}.{ext}"
                if not safe_write(fig_path, img_bytes):
                    logger.warning("保存 figure 失败 %s", fig_path)
                    continue

                page_results.append(
                    {
                        "page": page_no,
                        "figure_index": img_idx,
                        "figure_path": str(fig_path.as_posix()),
                        "ocr_text": "",
                        "caption_text": "",
                        "source": "bitmap",
                        "bbox": bbox,
                        "figure_number": None,
                        "img_bytes": img_bytes,
                        "ext": ext,
                    }
                )

            # ---- 2. 矢量渲染兜底 ------------------------------------------------
            if vector_render_enabled and page_area > 0:
                try:
                    clusters = page.cluster_drawings()
                except Exception:  # noqa: BLE001
                    clusters = []
                # clusters 可能是 (clusters, flags) 或 list[Rect]；兼容两种返回形式
                if isinstance(clusters, tuple):
                    cluster_rects = clusters[0]
                else:
                    cluster_rects = clusters

                for v_idx, rect in enumerate(cluster_rects):
                    try:
                        rect = fitz.Rect(rect)
                    except Exception:  # noqa: BLE001
                        continue
                    if rect.is_empty or rect.width <= 0 or rect.height <= 0:
                        continue
                    # 面积过滤：丢弃小于页面阈值的碎片（与位图路径共用 FIGURE_MIN_AREA_FRAC）
                    if rect.width * rect.height < page_area * FIGURE_MIN_AREA_FRAC:
                        continue
                    # 文本主导簇过滤：摘要页/正文页的矢量文字块会被 cluster_drawings
                    # 聚成「figure」，但正文不是图（词数多、文字 ink 覆盖率高），跳过。
                    # 真实图表只有少量文字（轴标签/图例），不会命中这两个阈值。
                    n_text_words, text_ink_frac = _vector_cluster_text_stats(page, rect)
                    if (
                        n_text_words >= VECTOR_CLUSTER_MAX_WORDS
                        or text_ink_frac >= VECTOR_CLUSTER_TEXT_INK_FRAC
                    ):
                        continue
                    # 装饰性横幅过滤：横跨整页宽、极矮的刊头/logo 横幅是装饰
                    # 而非 figure（如封面页顶部期刊刊头），跳过，避免被当成图。
                    if _is_decorative_banner(rect, page.rect.width, page.rect.height):
                        continue
                    # 与已有位图 bbox 高度重叠则跳过（避免同一张图被抽两次）
                    overlapped = False
                    for fig in page_results:
                        if fig["source"] != "bitmap":
                            continue
                        fx0, fy0, fx1, fy1 = fig["bbox"]
                        ix0 = max(rect.x0, fx0)
                        iy0 = max(rect.y0, fy0)
                        ix1 = min(rect.x1, fx1)
                        iy1 = min(rect.y1, fy1)
                        if ix1 > ix0 and iy1 > iy0:
                            inter = (ix1 - ix0) * (iy1 - iy0)
                            # 若矢量簇与位图 bbox 重叠面积 > 50% 矢量面积，认为是同一张图
                            if inter > rect.width * rect.height * 0.5:
                                overlapped = True
                                break
                    if overlapped:
                        continue
                    try:
                        pix = page.get_pixmap(clip=rect, dpi=150)
                    except Exception:  # noqa: BLE001
                        continue
                    img_bytes = pix.tobytes("png")
                    fig_path = uploads / f"p{page_no}_v{v_idx}.png"
                    if not safe_write(fig_path, img_bytes):
                        logger.warning("保存矢量 figure 失败 %s", fig_path)
                        continue
                    page_results.append(
                        {
                            "page": page_no,
                            "figure_index": v_idx + len(img_infos),
                            "figure_path": str(fig_path.as_posix()),
                            "ocr_text": "",
                            "caption_text": "",
                            "source": "vector",
                            "bbox": (rect.x0, rect.y0, rect.x1, rect.y1),
                            "figure_number": None,
                            "img_bytes": img_bytes,
                            "ext": "png",
                        }
                    )

            # ---- 3. 图注关联 ----------------------------------------------------
            _associate_captions(page, page_results)

            # ---- 4. 视觉分析由独立 Qwen3-VL HTTP pipeline 负责 --------------------
            # 本层只抽取图像/矢量资产和图注，不加载或调用任何 OCR 引擎。
            for fig in page_results:
                fig["ocr_text"] = ""
                fig["figure_type"] = "other"
                fig["axis"] = {}
                fig["axis_info"] = None
                fig["trend"] = ""
                fig["conclusion"] = ""
                fig["summary"] = ""
                fig["vlm_decision"] = route_vlm_for_figure(fig)["decision"]
                fig.pop("img_bytes", None)
                fig.pop("ext", None)
                results.append(fig)

        logger.info("figure 抽取完成 [%s]: %d 张", paper_id, len(results))
        return results
    finally:
        doc.close()


def process_one_pdf(content: bytes, filename: str, db: Session) -> UploadedPaper:
    """单文件 PDF 解析入库的完整流程，供 upload_paper / upload_batch 复用。

    任何环节失败均返回 success=False 的 UploadedPaper，不抛异常，
    以便批量上传时单文件失败不影响其他文件。

    增强（修复 0 chunks / 0 B 问题）：
    1. pypdf 提取为空时自动尝试 OCR 降级
    2. 无论文本长度，执行 split_into_chunks 确保至少 1 个 chunk
    3. 将 PDF 文件保存到磁盘 uploads/ 目录
    4. 计算并写入 chunk_count 和 index_size
    5. 将 pdf_url 写回数据库，便于后续查看/下载
    """
    if not filename or not filename.lower().endswith(".pdf"):
        logger.warning("[上传跳过] 非 PDF 文件: %s", filename)
        return UploadedPaper(id="", title=filename or "", success=False, error="非 PDF 文件")
    if not content:
        logger.warning("[上传跳过] 文件为空: %s", filename)
        return UploadedPaper(id="", title=filename, success=False, error="文件为空")

    logger.info("[开始解析] PDF: %s, 大小=%d bytes", filename, len(content))

    try:
        meta = _extract_pdf_metadata(content, filename)
    except PDFEncryptedError:
        logger.error("[解析失败] 文件已加密: %s", filename)
        return UploadedPaper(
            id="", title=filename, success=False, error="该 PDF 已加密，请先解密后上传"
        )
    except PDFParseError as e:
        logger.exception("[解析失败] PDF 解析错误: %s, 原因=%s", filename, e)
        return UploadedPaper(id="", title=filename, success=False, error=f"无法解析该 PDF：{e}")
    except Exception as e:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        logger.exception("[解析失败] 未知解析异常: %s, 原因=%s", filename, e)
        return UploadedPaper(id="", title=filename, success=False, error=f"无法解析该 PDF：{e}")

    paper_id = _gen_paper_id(content, meta["title"])

    # 1. B1: 提取 PDF 全文（失败不阻塞入库）
    full_text = extract_full_text(content)

    # 扫描 PDF 不再走已退役 OCR；明确标记为不可提取，避免伪造全文。
    is_scanned = not bool(full_text)
    ocr_status = "failed" if is_scanned else "done"
    if is_scanned:
        logger.warning("[文本提取失败] PDF 无可提取文本，旧 OCR 已退役: %s", filename)

    # 3. 分块切割
    chunks = split_into_chunks(full_text) if full_text else [""]
    chunk_count = len(chunks)
    index_size = sum(len(c.encode("utf-8")) for c in chunks)

    logger.info(
        "[解析完成] %s -> paper_id=%s, chunks=%d, index_size=%d, text_len=%d",
        filename,
        paper_id,
        chunk_count,
        index_size,
        len(full_text),
    )

    # 4. 保存 PDF 到磁盘
    pdf_path = _save_pdf_to_disk(content, paper_id)
    if not pdf_path:
        logger.error("[保存失败] PDF 文件未能写入 uploads/: paper_id=%s", paper_id)
        # 不阻断入库，但 pdf_url 为空
        pdf_url = ""
    else:
        pdf_url = str(Path(pdf_path).as_posix())
        logger.info("[保存成功] PDF 已写入: %s", pdf_path)

    # 5. 入库
    try:
        crud.create_paper_from_upload(
            db,
            paper_id=paper_id,
            title=meta["title"],
            authors=meta["authors"],
            year=meta["year"],
            abstract=meta["abstract"],
            pdf_url=pdf_url,
            full_text=full_text,
            chunk_count=chunk_count,
            index_size=index_size,
            ocr_status=ocr_status,
            is_scanned=is_scanned,
        )
    except Exception as e:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        logger.exception("[入库失败] paper_id=%s, 原因=%s", paper_id, e)
        return UploadedPaper(
            id=paper_id, title=meta["title"], success=False, error=f"入库失败：{e}"
        )

    logger.info("[入库成功] paper_id=%s, title=%s", paper_id, meta["title"])
    return UploadedPaper(
        id=paper_id,
        title=meta["title"],
        authors=meta["authors"],
        year=meta["year"],
        abstract=meta["abstract"],
        source="upload",
        success=True,
        ocr_status=ocr_status,
        is_scanned=is_scanned,
    )


# ==================== DOCX 文档解析 ====================


def _iter_body_blocks(doc):
    """Walk doc.element.body in document order, yielding Paragraph/Table objects.

    python-docx 的 doc.paragraphs / doc.tables 只返回顶层、且不保证原始顺序。
    实验报告等表格密集型文档常把标题 / 摘要 / 内容放在表格里——只查 doc.paragraphs
    会丢失整个表格内容，导致 reflection/file / 上传触发 422「未能从文件中提取文本」。
    这里通过 XML body 的 iterchildren() 按 w:p / w:tbl 顺序产出，保留阅读序列
    （标题段 → 实验设置表 → 结论段 → ...）。
    """
    from docx.oxml.ns import qn
    from docx.table import Table as _DocxTable
    from docx.text.paragraph import Paragraph as _DocxParagraph

    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag
        if tag == qn("w:p"):
            yield _DocxParagraph(child, doc)
        elif tag == qn("w:tbl"):
            yield _DocxTable(child, doc)


def _walk_table_blocks(table):
    """Yield Paragraph objects inside a table (recursively, deduped by _tc identity).

    合并单元格处理：
    - 横向合并（gridSpan）：python-docx 的 row.cells 对同行合并区域返回相同 _tc
    - 垂直合并（vMerge）：跨行合并时，row.cells 也返回顶部 _tc，但 cell.paragraphs
      能拿到本行实际内容

    去重 key 用 (row_index, tc_id) 而非纯 tc_id：横向合并同行去重，
    垂直合并跨行保留。原实现用纯 tc_id 会误判 vMerge 跨行 cell 为重复，
    导致表格模板式实验报告的正文行（4303-13175字）被整行跳过。
    """
    seen_keys = set()
    for ri, row in enumerate(table.rows):
        for cell in row.cells:
            key = (ri, id(cell._tc))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            for p in cell.paragraphs:
                yield p
            if cell.tables:
                for nested in cell.tables:
                    yield from _walk_table_blocks(nested)


def _collect_docx_text_blocks(doc) -> list[str]:
    """按文档顺序收集所有非空段落文本（含表格内段落，跳过页眉页脚）。

    实验报告 / 课程报告常把「实验名称/仪器/结论」等放在布局表格里，只查
    doc.paragraphs 会丢失完整正文 → full_text < 10 字符 → 422 错误。

    页眉 / 页脚不抽取：学校模板会在每页重复学号/姓名，作为 RAG 噪声会降低
    向量检索精度。

    类型分派：用 isinstance 检查 DocxTable，避免依赖 ``hasattr(block, 'rows')``
    这种脆弱判定 —— 未来 python-docx 若给 Paragraph 增加 .rows 属性会出 bug。
    """
    from docx.table import Table as _DocxTable
    from docx.text.paragraph import Paragraph as _DocxParagraph

    blocks: list[str] = []
    for block in _iter_body_blocks(doc):
        if isinstance(block, _DocxParagraph):
            t = (block.text or "").strip()
            if t:
                blocks.append(t)
        elif isinstance(block, _DocxTable):
            # Table: 递归遍历单元格段落（已去重合并单元格）
            for p in _walk_table_blocks(block):
                t = (p.text or "").strip()
                if t:
                    blocks.append(t)
        # 其他 body 元素（sectPr 等）跳过 —— 当前版本没有需要抽取的内容
    return blocks


def _collect_docx_top_level_paragraph_texts(doc) -> list[str]:
    """仅收集顶级 body 段落（不进入表格），用于元数据 title 启发式。

    表格密集型文档（如实验报告）的真正标题几乎总是在文档顶部段落里，
    而表格单元格里常见的「实验名称:」「仪器:」等列头会被错误识别为标题。
    因此 title 解析应只用顶级段落，abstract 抽取才用全量块（含表格）。
    """
    from docx.text.paragraph import Paragraph as _DocxParagraph

    out: list[str] = []
    for block in _iter_body_blocks(doc):
        if isinstance(block, _DocxParagraph):
            t = (block.text or "").strip()
            if t:
                out.append(t)
    return out


def _extract_docx_metadata(content: bytes, filename: str) -> dict[str, Any]:
    """从 DOCX 二进制内容中提取元数据。

    与 _extract_pdf_metadata 一致的结构，其中 title 最大长度限制
    为 1000 字符以避免极端情况（pdf_parser 中无此限制，但 DOCX
    文本通常较短）。

    使用 python-docx 读取段落文本：
    - 标题：取前 3 个段落中最长的非空行或文件名兜底
    - 作者：从文档属性（core.xml）提取
    - 年份：从全文搜索 4 位年份数字（取最频繁出现的）
    - 摘要：取前 5 个段落拼接，截取前 800 字符

    Returns:
        {"title": str, "authors": list[str], "year": int, "abstract": str}
    """
    import re as _re

    try:
        from docx import Document
    except ImportError:
        raise PDFParseError(
            "python-docx 未安装，无法解析 DOCX 文件。请运行: pip install python-docx"
        ) from None

    try:
        doc = Document(BytesIO(content))
    except Exception as e:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        raise PDFParseError(f"无法打开 DOCX 文件：{e}") from e

    # 1. 提取所有段落 + 表格内段落（按文档顺序，含表格 / 嵌套表格，合并单元格已去重）。
    #    旧版只查 doc.paragraphs 会丢失布局表格里的「实验名称/仪器/结论」等内容，
    #    导致元数据（标题/摘要）丢失、且 full_text < 10 字符触发 422 错误。
    paragraphs: list[str] = _collect_docx_text_blocks(doc)

    # 2. 标题：前 3 个非空**顶级段落**中最长的（避免表格里的列头如「实验名称:」赢）
    #    表格里的列头通常是 4-6 字，真实标题是更长的句子（10+ 字），
    #    「最长 wins」仍能正确选到真实标题。使用顶级段落避免被列头误导。
    title = ""
    title_candidates = _collect_docx_top_level_paragraph_texts(doc)[:3]
    if title_candidates:
        title = max(title_candidates, key=len)
    if not title or len(title) < 3:
        title = _re.sub(r"\.(docx?)$", "", filename, flags=_re.IGNORECASE).strip() or filename

    # 3. 作者：从文档属性提取
    authors: list[str] = []
    try:
        core_props = doc.core_properties
        if core_props.author:
            raw = str(core_props.author)
            authors = [a.strip() for a in _re.split(r"[;,&]", raw) if a.strip()]
    except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        pass

    # 4. 年份：从全文搜索
    year = 0
    full_text = "\n".join(paragraphs)
    year_matches = _re.findall(r"\b(19\d{2}|20\d{2})\b", full_text)
    if year_matches:
        from collections import Counter

        year_counter = Counter(year_matches)
        # 取最频繁出现的年份（排除太旧的）
        for y, _ in year_counter.most_common():
            y_int = int(y)
            if 1990 <= y_int <= 2030:
                year = y_int
                break

    # 5. 摘要：前 5 段拼接
    abstract = "\n".join(paragraphs[:5]).strip()
    if len(abstract) > 800:
        abstract = abstract[:800]

    return {"title": title, "authors": authors, "year": year, "abstract": abstract}


def _extract_docx_full_text(content: bytes) -> str:
    """提取 DOCX 全文文本（顶部段落 + 表格内的段落 + 嵌套表格，按文档顺序）。

    旧版只遍历 doc.paragraphs，会丢失整个表格内容——实验报告 / 课程报告
    把「实验名称 / 仪器 / 结论」放在布局表格里时，会触发
    _process_one_reflection_file 的 'full_text < 10 字符' 校验，返回 422。
    改为调用 `_collect_docx_text_blocks`，后者通过 _iter_body_blocks 按
    XML body 顺序产出 paragraphs + tables，并通过 _walk_table_blocks
    递归处理合并单元格去重与嵌套表格。

    页眉 / 页脚不抽取（RAG 噪声）。
    """
    if not content:
        return ""
    try:
        from docx import Document
    except ImportError:
        return ""
    try:
        doc = Document(BytesIO(content))
    except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        return ""
    return "\n".join(_collect_docx_text_blocks(doc)).strip()


def process_one_docx(content: bytes, filename: str, db: Session) -> UploadedPaper:
    """单文件 DOCX 解析入库。"""
    if not filename or not filename.lower().endswith(".docx"):
        return UploadedPaper(id="", title=filename or "", success=False, error="非 DOCX 文件")
    if not content:
        return UploadedPaper(id="", title=filename, success=False, error="文件为空")

    try:
        meta = _extract_docx_metadata(content, filename)
    except PDFParseError as e:
        return UploadedPaper(id="", title=filename, success=False, error=str(e))
    except Exception as e:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        return UploadedPaper(id="", title=filename, success=False, error=f"无法解析该 DOCX：{e}")

    paper_id = _gen_paper_id(content, meta["title"])
    full_text = _extract_docx_full_text(content)
    chunks = split_into_chunks(full_text) if full_text else [""]
    chunk_count = len(chunks)
    index_size = sum(len(c.encode("utf-8")) for c in chunks)
    try:
        crud.create_paper_from_upload(
            db,
            paper_id=paper_id,
            title=meta["title"],
            authors=meta["authors"],
            year=meta["year"],
            abstract=meta["abstract"],
            full_text=full_text,
            chunk_count=chunk_count,
            index_size=index_size,
        )
    except Exception as e:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        return UploadedPaper(
            id=paper_id, title=meta["title"], success=False, error=f"入库失败：{e}"
        )

    return UploadedPaper(
        id=paper_id,
        title=meta["title"],
        authors=meta["authors"],
        year=meta["year"],
        abstract=meta["abstract"],
        source="upload",
        success=True,
    )


def process_zip_upload(
    content: bytes, db: Session, max_size: int = MAX_ZIP_SIZE
) -> ZipUploadResponse:
    """ZIP 压缩包批量解析入库。

    处理流程：
    1. 校验文件大小
    2. 保存到临时目录并解压
    3. 递归遍历所有 .pdf 文件（上限 MAX_ZIP_PDF_COUNT）
    4. 逐个调用 process_one_pdf 解析入库
    5. 收集成功/失败结果，清理临时文件
    6. 返回汇总统计

    Args:
        content: ZIP 文件的二进制内容
        db: SQLAlchemy 数据库会话
        max_size: ZIP 文件最大字节数（默认 50MB）

    Returns:
        ZipUploadResponse（即使无 PDF 也返回，不抛异常）
    """
    # 1. 大小校验
    if len(content) > max_size:
        raise ValueError(
            f"ZIP 压缩包过大（{len(content) / 1024 / 1024:.1f} MB），"
            f"最大支持 {max_size / 1024 / 1024:.0f} MB"
        )
    if len(content) < 4:
        raise ValueError("文件为空，无法作为 ZIP 处理")

    # 2. 校验是否为有效 ZIP
    if not zipfile.is_zipfile(BytesIO(content)):
        raise ValueError("该文件不是有效的 ZIP 压缩包")

    # 3. 创建临时目录并解压（防 Zip-Slip：每个条目 resolve 后必须在 tmp_dir 内）
    tmp_dir = tempfile.mkdtemp(prefix="pf_zip_")
    tmp_root = Path(tmp_dir).resolve()
    try:
        with zipfile.ZipFile(BytesIO(content)) as zf:
            # 🛡️ P0 Zip-Slip 修复：
            # 原版 zf.extractall(tmp_dir) 不校验条目的相对路径，攻击者构造
            # 包含 "../app/main.py" / "..\\..\\Windows\\System32\\..." 的 ZIP 条目，
            # 可覆盖应用代码 / 植入 autostart → RCE。
            # 解压前对每个条目显式 resolve，必须仍位于 tmp_dir 内，且必须是
            # 真实的相对路径（resolve 后长度 > tmp_root 长度）。
            # Python 3.12+ 还有 filter="data" 内置防护；这里用兼容 3.10+ 的手写校验。
            for member in zf.infolist():
                # 🛡️ P0-1 Zip-Slip 修复后续加固：先归一化分隔符，否则不限制。
                # 攻击者可在 POSIX 里提交 '..\\..\\Windows\\evil.exe' 作为单个文件名，
                # Path(.).parts 仅返回 ['..\\..\\Windows\\evil.exe'] 一项，拼下不发生。
                norm_name = member.filename.replace("\\", "/")
                member_path = Path(tmp_dir) / norm_name
                abs_path = member_path.resolve()
                # resolve() 后若 abs_path 不是 tmp_root 的子路径 → 中毒条目
                # (在 Windows 上 abs_path.resolve() 可能换盘符，全部走 str 比对最稳)
                if (
                    str(abs_path) != str(tmp_root)  # 根目录自身（说明 member 是 '.'）
                    and not str(abs_path).startswith(str(tmp_root) + os.sep)
                ):
                    raise ValueError(
                        f"ZIP 含非法路径「{member.filename}」，"
                        "解压后会跳出临时目录（疑似 Zip-Slip 攻击）。"
                    )
                # 拒绝绝对路径 / 父级引用 · ZIP 中以 0xA 开头的 external_attr 常为符号链接
                parts = Path(norm_name).parts
                if norm_name.startswith(("/", "\\")) or any(p == ".." for p in parts):
                    raise ValueError(
                        f"ZIP 含绝对路径或父级引用「{member.filename}」，"
                        "已拒绝解压（疑似 Zip-Slip 攻击）。"
                    )
                # 拒绝符号链接条目（zip 中 S_IFLNK = 0o120000 = 0xA0000000）
                # 位与掩码 0xF0000000 == 0xA0000000 即 symlink
                if (member.external_attr >> 16) & 0xF000 == 0xA000:
                    raise ValueError(f"ZIP 含符号链接条目「{member.filename}」，已拒绝解压")
            # 校验通过，正式解压（Python 3.12+ 可加 filter="data" 双保险，但 3.11-
            # 的 filter 参数缺失，宁可手写校验也不要在版本分支上赌兼容）。
            zf.extractall(tmp_dir)
    except zipfile.BadZipFile:
        raise ValueError("ZIP 文件已损坏，无法解压") from None
    except ValueError:
        # 安全校验失败：清理临时目录后向上抛
        import shutil as _shutil

        _shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    except Exception as e:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        raise ValueError(f"解压失败：{e}") from e

    # 4. 递归收集所有支持的文档文件（.pdf / .docx）
    pdf_paths: list[tuple[str, str]] = []  # [(full_path, extension)]
    for root, _dirs, files in os.walk(tmp_dir):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                full = os.path.join(root, name)
                _rel = os.path.relpath(full, tmp_dir)
                pdf_paths.append((full, ext))
                if len(pdf_paths) >= MAX_ZIP_PDF_COUNT:
                    break
        if len(pdf_paths) >= MAX_ZIP_PDF_COUNT:
            break

    total = len(pdf_paths)
    succeeded: list[UploadedPaper] = []
    failed_files: list[dict] = []

    # 5. 逐个处理文档（自动区分 PDF / DOCX）
    for path, ext in pdf_paths:
        rel_name = os.path.relpath(path, tmp_dir)
        try:
            with open(path, "rb") as f:
                file_content = f.read()
        except Exception as e:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
            failed_files.append({"filename": rel_name, "error": f"无法读取文件：{e}"})
            continue

        # 根据扩展名选择处理器
        if ext == ".pdf":
            result = process_one_pdf(file_content, os.path.basename(path), db)
        elif ext == ".docx":
            result = process_one_docx(file_content, os.path.basename(path), db)
        else:
            failed_files.append({"filename": rel_name, "error": f"不支持的文件格式 ({ext})"})
            continue

        if result.success:
            succeeded.append(result)
        else:
            failed_files.append({"filename": rel_name, "error": result.error or "未知错误"})

    # 6. 清理临时文件
    import shutil

    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001 - OCR/PDF 单页/单图回调 - 单失败不应中断整文档
        pass

    return ZipUploadResponse(
        total=total,
        succeeded=len(succeeded),
        failed=len(failed_files),
        failed_files=failed_files,
        papers=succeeded,
    )
