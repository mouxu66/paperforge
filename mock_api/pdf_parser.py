"""PDF 解析与上传入库工具。

从原 main.py 抽取，负责：
- PDF 元数据提取（标题 / 作者 / 年份 / 摘要）
- 加密 / 损坏文件检测
- 基于内容哈希生成 paper_id
- 单文件解析入库完整流程（供 upload_paper / upload_batch 复用）
"""
from __future__ import annotations

import hashlib
import re
from io import BytesIO
from typing import Any

from sqlalchemy.orm import Session

from . import crud
from .schemas import UploadedPaper

# 单次批量上传上限（防止超时）
MAX_BATCH_SIZE = 20


class PDFEncryptedError(Exception):
    """PDF 已加密，需要密码才能读取。"""


class PDFParseError(Exception):
    """PDF 解析失败（文件损坏、格式错误等）。"""


def _extract_pdf_metadata(content: bytes, filename: str) -> dict[str, Any]:
    """从 PDF 二进制内容中提取元数据。

    Returns:
        {"title": str, "authors": list[str], "year": int, "abstract": str}
    """
    try:
        from PyPDF2 import PdfReader
    except ImportError as e:
        raise PDFParseError("未安装 PyPDF2") from e

    try:
        reader = PdfReader(BytesIO(content))
    except Exception as e:
        # PyPDF2 对加密文件抛出的异常类型不统一，按信息判断
        msg = str(e).lower()
        if "encrypt" in msg or "password" in msg:
            raise PDFEncryptedError(filename)
        raise PDFParseError(str(e)) from e

    # 再次检查加密标记
    if reader.is_encrypted:
        try:
            # decrypt("") 不抛异常不代表解密成功，需检查返回值（0=未解密）
            result = reader.decrypt("")
        except Exception:
            raise PDFEncryptedError(filename)
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
    except Exception:
        pass

    # 标题为空时用文件名兜底
    if not title:
        title = re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE).strip() or filename

    # 2. 摘要：取前几页文本拼接，截取前若干字符
    abstract = ""
    try:
        pages_text: list[str] = []
        for i, page in enumerate(reader.pages):
            if i >= 3:
                break
            try:
                pages_text.append(page.extract_text() or "")
            except Exception:
                continue
        full_text = "\n".join(pages_text).strip()
        if full_text:
            abstract = full_text[:800]
    except Exception:
        pass

    return {"title": title, "authors": authors, "year": year, "abstract": abstract}


def _gen_paper_id(content: bytes, title: str) -> str:
    """基于文件内容哈希 + 标题生成 paper_id（去重）。"""
    h = hashlib.sha1(content).hexdigest()[:12]
    safe_title = re.sub(r"[^\w\u4e00-\u9fa5-]", "_", title)[:40]
    return f"upload_{h}_{safe_title}"


def extract_full_text(content: bytes) -> str:
    """提取 PDF 全文文本（覆盖所有页面）。

    B1：将 PDF 全文内容纳入 FTS5 索引，提升搜索/问答检索颗粒度。
    - 使用 PyPDF2 遍历所有页面并拼接文本。
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
        from PyPDF2 import PdfReader
    except ImportError:
        return ""

    try:
        reader = PdfReader(BytesIO(content))
    except Exception:
        return ""

    # 加密文件：尝试空密码解密；失败则返回空
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                return ""
        except Exception:
            return ""

    pages_text: list[str] = []
    for page in reader.pages:
        try:
            txt = page.extract_text() or ""
        except Exception:
            continue
        if txt:
            pages_text.append(txt)
    return "\n".join(pages_text).strip()


def process_one_pdf(content: bytes, filename: str, db: Session) -> UploadedPaper:
    """单文件 PDF 解析入库的完整流程，供 upload_paper / upload_batch 复用。

    任何环节失败均返回 success=False 的 UploadedPaper，不抛异常，
    以便批量上传时单文件失败不影响其他文件。
    """
    if not filename or not filename.lower().endswith(".pdf"):
        return UploadedPaper(id="", title=filename or "", success=False, error="非 PDF 文件")
    if not content:
        return UploadedPaper(id="", title=filename, success=False, error="文件为空")

    try:
        meta = _extract_pdf_metadata(content, filename)
    except PDFEncryptedError:
        return UploadedPaper(id="", title=filename, success=False, error="该 PDF 已加密，请先解密后上传")
    except PDFParseError as e:
        return UploadedPaper(id="", title=filename, success=False, error=f"无法解析该 PDF：{e}")
    except Exception as e:
        return UploadedPaper(id="", title=filename, success=False, error=f"无法解析该 PDF：{e}")

    paper_id = _gen_paper_id(content, meta["title"])
    # B1: 提取 PDF 全文（失败不阻塞入库）
    full_text = extract_full_text(content)
    try:
        crud.create_paper_from_upload(
            db,
            paper_id=paper_id,
            title=meta["title"],
            authors=meta["authors"],
            year=meta["year"],
            abstract=meta["abstract"],
            full_text=full_text,
        )
    except Exception as e:
        return UploadedPaper(id=paper_id, title=meta["title"], success=False, error=f"入库失败：{e}")

    return UploadedPaper(
        id=paper_id,
        title=meta["title"],
        authors=meta["authors"],
        year=meta["year"],
        abstract=meta["abstract"],
        source="upload",
        success=True,
    )
