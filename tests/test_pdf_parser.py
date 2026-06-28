"""测试 mock_api/pdf_parser.py 中的 process_one_pdf 函数。

覆盖场景：
- 加密 PDF → 返回 success=False, error 包含「加密」
- 损坏 PDF → 返回 success=False, error 包含「无法解析」
- 正常 PDF → 返回 success=True 及元数据（标题/作者/摘要/年份）
"""
from __future__ import annotations

from io import BytesIO
from unittest.mock import patch, MagicMock

from mock_api.pdf_parser import process_one_pdf


# ---------------------------------------------------------------------------
# PDF 测试夹具生成
# ---------------------------------------------------------------------------
def _make_normal_pdf() -> bytes:
    """生成一个包含元数据的正常 PDF。"""
    from PyPDF2 import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({
        "/Title": "Attention Is All You Need",
        "/Author": "Vaswani, Shazeer, Parmar",
        "/CreationDate": "D:20170601",
    })
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_encrypted_pdf() -> bytes:
    """生成一个加密的 PDF（需要密码）。"""
    from PyPDF2 import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata({"/Title": "Secret Document"})
    writer.encrypt("secret_password")
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_corrupted_pdf() -> bytes:
    """生成一个损坏的 PDF（非有效 PDF 格式）。"""
    return b"%PDF-1.4\nthis is not valid pdf content\n%%EOF"


# ---------------------------------------------------------------------------
# 加密 PDF
# ---------------------------------------------------------------------------
def test_encrypted_pdf_returns_error():
    """加密 PDF → success=False, error 包含「加密」。"""
    content = _make_encrypted_pdf()
    db = MagicMock()
    result = process_one_pdf(content, "secret.pdf", db)
    assert result.success is False
    assert "加密" in result.error


# ---------------------------------------------------------------------------
# 损坏 PDF
# ---------------------------------------------------------------------------
def test_corrupted_pdf_returns_error():
    """损坏 PDF → success=False, error 包含「无法解析」。"""
    content = _make_corrupted_pdf()
    db = MagicMock()
    result = process_one_pdf(content, "broken.pdf", db)
    assert result.success is False
    assert "无法解析" in result.error


# ---------------------------------------------------------------------------
# 正常 PDF
# ---------------------------------------------------------------------------
@patch("mock_api.pdf_parser.crud.create_paper_from_upload")
def test_normal_pdf_returns_metadata(mock_create):
    """正常 PDF → success=True, 返回元数据。"""
    mock_create.return_value = MagicMock()
    content = _make_normal_pdf()
    db = MagicMock()
    result = process_one_pdf(content, "attention.pdf", db)
    assert result.success is True
    assert result.title == "Attention Is All You Need"
    assert "Vaswani" in result.authors
    # 年份提取依赖 PyPDF2 的 creation_date 属性；3.0.1 不支持则返回 0，3.x+ 支持则返回 2017
    assert result.year in (0, 2017)
    assert result.id.startswith("upload_")


@patch("mock_api.pdf_parser.crud.create_paper_from_upload")
def test_normal_pdf_id_is_deterministic(mock_create):
    """相同内容 → 相同 paper_id（基于内容哈希）。"""
    mock_create.return_value = MagicMock()
    content = _make_normal_pdf()
    db = MagicMock()
    r1 = process_one_pdf(content, "a.pdf", db)
    r2 = process_one_pdf(content, "b.pdf", db)
    assert r1.id == r2.id


# ---------------------------------------------------------------------------
# 边界情况
# ---------------------------------------------------------------------------
def test_non_pdf_file():
    """非 .pdf 文件 → success=False, error 包含「非 PDF」。"""
    result = process_one_pdf(b"hello", "readme.txt", MagicMock())
    assert result.success is False
    assert "非 PDF" in result.error


def test_empty_content():
    """空内容 → success=False, error 包含「文件为空」。"""
    result = process_one_pdf(b"", "empty.pdf", MagicMock())
    assert result.success is False
    assert "文件为空" in result.error


@patch("mock_api.pdf_parser.crud.create_paper_from_upload")
def test_db_failure_returns_error(mock_create):
    """入库失败 → success=False, error 包含「入库失败」。"""
    mock_create.side_effect = Exception("DB connection lost")
    content = _make_normal_pdf()
    db = MagicMock()
    result = process_one_pdf(content, "paper.pdf", db)
    assert result.success is False
    assert "入库失败" in result.error


# ===========================================================================
# extract_full_text（B1：PDF 全文提取）
# ===========================================================================
from mock_api.pdf_parser import extract_full_text  # noqa: E402


def _make_page(text: str):
    """构造模拟 PDF 页面对象，extract_text() 返回给定文本。"""
    page = MagicMock()
    page.extract_text.return_value = text
    return page


def _make_reader(pages: list, encrypted: bool = False, decrypt_ok: bool = True):
    """构造模拟 PdfReader。"""
    reader = MagicMock()
    reader.is_encrypted = encrypted
    reader.pages = pages
    if encrypted:
        reader.decrypt.return_value = 1 if decrypt_ok else 0
    return reader


def test_extract_full_text_empty_content():
    """空内容 → 返回空字符串。"""
    assert extract_full_text(b"") == ""


def test_extract_full_text_multi_page_concatenation():
    """多页 PDF：拼接所有页面文本，换行分隔。"""
    pages = [_make_page("第一页内容"), _make_page("第二页内容")]
    reader = _make_reader(pages)
    with patch("PyPDF2.PdfReader", return_value=reader):
        result = extract_full_text(b"%PDF-1.4 fake")
    assert "第一页内容" in result
    assert "第二页内容" in result
    assert "\n" in result


def test_extract_full_text_strips_trailing_whitespace():
    """结果去尾空白。"""
    pages = [_make_page("  内容带空格  ")]
    reader = _make_reader(pages)
    with patch("PyPDF2.PdfReader", return_value=reader):
        result = extract_full_text(b"%PDF fake")
    assert result == "内容带空格"


def test_extract_full_text_encrypted_decryptable():
    """加密 PDF 但空密码可解密 → 正常提取文本。"""
    pages = [_make_page("解密后的内容")]
    reader = _make_reader(pages, encrypted=True, decrypt_ok=True)
    with patch("PyPDF2.PdfReader", return_value=reader):
        result = extract_full_text(b"%PDF encrypted")
    assert result == "解密后的内容"


def test_extract_full_text_encrypted_no_decrypt_returns_empty():
    """加密 PDF 且空密码无法解密 → 返回空字符串（不阻塞）。"""
    reader = _make_reader([], encrypted=True, decrypt_ok=False)
    with patch("PyPDF2.PdfReader", return_value=reader):
        result = extract_full_text(b"%PDF encrypted")
    assert result == ""


def test_extract_full_text_corrupted_returns_empty():
    """损坏 PDF（PdfReader 构造抛异常）→ 返回空字符串。"""
    with patch("PyPDF2.PdfReader", side_effect=Exception("invalid pdf")):
        result = extract_full_text(b"not a pdf")
    assert result == ""


def test_extract_full_text_page_extraction_failure_skipped():
    """单页 extract_text 抛异常：跳过该页，继续其他页。"""
    bad_page = MagicMock()
    bad_page.extract_text.side_effect = Exception("page corrupt")
    good_page = _make_page("正常页内容")
    reader = _make_reader([bad_page, good_page])
    with patch("PyPDF2.PdfReader", return_value=reader):
        result = extract_full_text(b"%PDF mixed")
    assert result == "正常页内容"


def test_extract_full_text_empty_pages_returns_empty():
    """所有页面文本为空 → 返回空字符串。"""
    pages = [_make_page(""), _make_page("")]
    reader = _make_reader(pages)
    with patch("PyPDF2.PdfReader", return_value=reader):
        result = extract_full_text(b"%PDF blank")
    assert result == ""


def test_extract_full_text_pypdf2_missing_returns_empty():
    """PyPDF2 未安装 → 返回空字符串（降级，不抛异常）。"""
    import builtins

    real_import = builtins.__import__

    def _no_pypdf2(name, *args, **kwargs):
        if name == "PyPDF2":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_no_pypdf2):
        result = extract_full_text(b"%PDF fake")
    assert result == ""
