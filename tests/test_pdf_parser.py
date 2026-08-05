"""测试 mock_api/pdf_parser.py 中的 process_one_pdf 函数。

覆盖场景：
- 加密 PDF → 返回 success=False, error 包含「加密」
- 损坏 PDF → 返回 success=False, error 包含「无法解析」
- 正常 PDF → 返回 success=True 及元数据（标题/作者/摘要/年份）
"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock, patch

from mock_api.pdf_parser import process_one_pdf


# ---------------------------------------------------------------------------
# PDF 测试夹具生成
# ---------------------------------------------------------------------------
def _make_normal_pdf() -> bytes:
    """生成一个包含元数据的正常 PDF。"""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.add_metadata(
        {
            "/Title": "Attention Is All You Need",
            "/Author": "Vaswani, Shazeer, Parmar",
            "/CreationDate": "D:20170601",
        }
    )
    buf = BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_encrypted_pdf() -> bytes:
    """生成一个加密的 PDF（需要密码）。"""
    from pypdf import PdfWriter

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
# process_one_pdf 的测试 PDF 是空白页（无文本），会触发 OCR 降级并可能挂起。
# 这里 mock 掉全文提取，让测试聚焦在元数据/入库逻辑上。
@patch("mock_api.pdf_parser.extract_full_text", return_value="sample full text")
@patch("mock_api.pdf_parser.crud.create_paper_from_upload")
def test_normal_pdf_returns_metadata(mock_create, _mock_extract):
    """正常 PDF → success=True, 返回元数据。"""
    mock_create.return_value = MagicMock()
    content = _make_normal_pdf()
    db = MagicMock()
    result = process_one_pdf(content, "attention.pdf", db)
    assert result.success is True
    assert result.title == "Attention Is All You Need"
    assert "Vaswani" in result.authors
    # 年份提取依赖 pypdf 的 creation_date 属性；3.0.1 不支持则返回 0，3.x+ 支持则返回 2017
    assert result.year in (0, 2017)
    assert result.id.startswith("upload_")


# 空白测试 PDF 会触发 OCR 降级，mock 全文提取避免挂起。
@patch("mock_api.pdf_parser.extract_full_text", return_value="sample full text")
@patch("mock_api.pdf_parser.crud.create_paper_from_upload")
def test_normal_pdf_id_is_deterministic(mock_create, _mock_extract):
    """相同内容 → 相同 paper_id（基于内容哈希）。"""
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


# 空白测试 PDF 会触发 OCR 降级，mock 全文提取避免挂起。
@patch("mock_api.pdf_parser.extract_full_text", return_value="sample full text")
@patch("mock_api.pdf_parser.crud.create_paper_from_upload")
def test_db_failure_returns_error(mock_create, _mock_extract):
    """入库失败 → success=False, error 包含「入库失败」。"""
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
    with patch("pypdf.PdfReader", return_value=reader):
        result = extract_full_text(b"%PDF-1.4 fake")
    assert "第一页内容" in result
    assert "第二页内容" in result
    assert "\n" in result


def test_extract_full_text_strips_trailing_whitespace():
    """结果去尾空白。"""
    pages = [_make_page("  内容带空格  ")]
    reader = _make_reader(pages)
    with patch("pypdf.PdfReader", return_value=reader):
        result = extract_full_text(b"%PDF fake")
    assert result == "内容带空格"


def test_extract_full_text_encrypted_decryptable():
    """加密 PDF 但空密码可解密 → 正常提取文本。"""
    pages = [_make_page("解密后的内容")]
    reader = _make_reader(pages, encrypted=True, decrypt_ok=True)
    with patch("pypdf.PdfReader", return_value=reader):
        result = extract_full_text(b"%PDF encrypted")
    assert result == "解密后的内容"


def test_extract_full_text_encrypted_no_decrypt_returns_empty():
    """加密 PDF 且空密码无法解密 → 返回空字符串（不阻塞）。"""
    reader = _make_reader([], encrypted=True, decrypt_ok=False)
    with patch("pypdf.PdfReader", return_value=reader):
        result = extract_full_text(b"%PDF encrypted")
    assert result == ""


def test_extract_full_text_corrupted_returns_empty():
    """损坏 PDF（PdfReader 构造抛异常）→ 返回空字符串。"""
    with patch("pypdf.PdfReader", side_effect=Exception("invalid pdf")):
        result = extract_full_text(b"not a pdf")
    assert result == ""


def test_extract_full_text_page_extraction_failure_skipped():
    """单页 extract_text 抛异常：跳过该页，继续其他页。"""
    bad_page = MagicMock()
    bad_page.extract_text.side_effect = Exception("page corrupt")
    good_page = _make_page("正常页内容")
    reader = _make_reader([bad_page, good_page])
    with patch("pypdf.PdfReader", return_value=reader):
        result = extract_full_text(b"%PDF mixed")
    assert result == "正常页内容"


def test_extract_full_text_empty_pages_returns_empty():
    """所有页面文本为空 → 返回空字符串。"""
    pages = [_make_page(""), _make_page("")]
    reader = _make_reader(pages)
    with patch("pypdf.PdfReader", return_value=reader):
        result = extract_full_text(b"%PDF blank")
    assert result == ""


def test_extract_full_text_pypdf2_missing_returns_empty():
    """pypdf 未安装 → 返回空字符串（降级，不抛异常）。"""
    import builtins

    real_import = builtins.__import__

    def _no_pypdf(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_no_pypdf):
        result = extract_full_text(b"%PDF fake")
    assert result == ""


# ===========================================================================
# DOCX 全文 / 元数据提取（B2：实验报告等表格密集型 DOCX）
# ===========================================================================
from mock_api.pdf_parser import _extract_docx_full_text, _extract_docx_metadata  # noqa: E402


def _make_docx_with_only_table() -> bytes:
    """构造一个完整正文都在表格里的 DOCX（实验报告典型样式）。

    例如「激光谐振腔调谐」这种实验报告，常把课程名、实验名称、仪器、结论等
    放在布局表格里。如果只查 doc.paragraphs，会丢失整个正文。
    """
    from docx import Document

    doc = Document()
    doc.add_heading("实验报告", level=1)
    table = doc.add_table(rows=4, cols=2)
    table.cell(0, 0).text = "实验名称"
    table.cell(0, 1).text = "激光谐振腔调谐"
    table.cell(1, 0).text = "实验仪器"
    table.cell(1, 1).text = "氦氖激光器 / 光功率计 / 示波器"
    table.cell(2, 0).text = "实验结论"
    table.cell(2, 1).text = "腔长优化后输出功率提升 25%"
    table.cell(3, 0).text = "误差分析"
    table.cell(3, 1).text = "环境振动干扰为主要误差来源"
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_docx_paragraphs_and_tables_ordered() -> bytes:
    """paragraph → table → paragraph 结构（验证文档顺序保留）。

    如果不按文档顺序产出，会把段落都排在前面 / 表格都排在后面，
    对 RAG chunk 化的语义连续性不利（比如「实验设置:」紧跟仪器表）。
    """
    from docx import Document

    doc = Document()
    doc.add_paragraph("实验目的: 验证不同腔长对输出功率的影响")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "腔长 1"
    table.cell(0, 1).text = "输出 1mW"
    table.cell(1, 0).text = "腔长 2"
    table.cell(1, 1).text = "输出 1.25mW"
    doc.add_paragraph("实验结论: 输出功率与腔长正相关")
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_docx_with_merged_cells() -> bytes:
    """合并单元格：row.cells 在合并区域两半返回相同 _tc 元素，必须去重。"""
    from docx import Document

    doc = Document()
    table = doc.add_table(rows=2, cols=3)
    table.cell(0, 0).text = "项目"
    # 合并 0,1 和 0,2 为同一单元（python-docx 用 merge() 完成）
    table.cell(0, 1).merge(table.cell(0, 2))
    table.cell(0, 1).text = "数值与单位"
    table.cell(1, 0).text = "功率"
    table.cell(1, 1).text = "1.2 mW"
    table.cell(1, 2).text = "(误差 0.05)"
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_docx_with_nested_tables() -> bytes:
    """嵌套表格：单元格内嵌一个 table（递归处理）。"""
    from docx import Document

    doc = Document()
    outer = doc.add_table(rows=1, cols=1)
    outer_cell = outer.cell(0, 0)
    outer_cell.text = "实验数据"
    nested = outer_cell.add_table(rows=2, cols=2)
    nested.cell(0, 0).text = "t=0"
    nested.cell(0, 1).text = "P=1mW"
    nested.cell(1, 0).text = "t=10min"
    nested.cell(1, 1).text = "P=0.95mW"
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_docx_full_text_extracts_table_content():
    """修复重点：所有正文都在表格里时，full_text 仍能抽取表格内段落。

    修复前：full_text 为空（仅 doc.add_heading 不算非空），触发 422。
    修复后：包含表头与单元格内容。
    """
    content = _make_docx_with_only_table()
    full = _extract_docx_full_text(content)
    assert "实验名称" in full
    assert "激光谐振腔调谐" in full
    assert "氦氖激光器" in full
    assert "腔长优化后输出功率提升 25%" in full


def test_docx_full_text_preserves_document_order():
    """paragraph → table → paragraph 结构应按文档顺序抽取（不止是 all-para-then-all-tbl）。"""
    content = _make_docx_paragraphs_and_tables_ordered()
    full = _extract_docx_full_text(content)
    pos_purpose = full.index("实验目的")
    pos_table = full.index("腔长 1")
    pos_conclusion = full.index("实验结论")
    assert pos_purpose < pos_table < pos_conclusion, (
        f"文档顺序不符合预期: purpose={pos_purpose} table={pos_table} conclusion={pos_conclusion}\n"
        f"实际输出: {full!r}"
    )


def test_docx_full_text_dedupes_merged_cells():
    """合并单元格：相同内容只出现一次（python-docx row.cells merge dup）。"""
    content = _make_docx_with_merged_cells()
    full = _extract_docx_full_text(content)
    # 「数值与单位」是合并单元格的文本，应当恰好出现 1 次（不是 2 次）
    assert full.count("数值与单位") == 1, f"合并单元格内容重复：{full!r}"
    assert "项目" in full
    assert "1.2 mW" in full


def test_docx_full_text_handles_nested_tables():
    """嵌套表格：单元格内的表格也应当被抽取。"""
    content = _make_docx_with_nested_tables()
    full = _extract_docx_full_text(content)
    assert "实验数据" in full  # 外层单元格
    assert "t=0" in full  # 内层表格
    assert "P=0.95mW" in full  # 内层表格


def test_docx_metadata_includes_table_text():
    """元数据提取也应从表格里抽取（title 之前可能放在布局表里）。"""
    content = _make_docx_with_only_table()
    meta = _extract_docx_metadata(content, "fallback.docx")
    # 摘要应包含实验名称/仪器/结论里的内容（前 5 块拼接到 800 字符）
    assert "实验名称" in meta["abstract"]
    assert "激光谐振腔调谐" in meta["abstract"]


def test_docx_full_text_empty_content():
    """空 content → 返回空字符串。"""
    assert _extract_docx_full_text(b"") == ""


def test_docx_full_text_returns_min_10_chars_for_tables():
    """回归测试：保证 _extract_docx_full_text 对表格丰富文档返回 >= 10 字符。

    修复前此场景返回空字符串，会导致 _process_one_reflection_file 触发 422：
    'full_text.strip() < 10' 判定为「可能是扫描版 PDF / DOCX 损坏 / 纯图片」。
    """
    content = _make_docx_with_only_table()
    assert len(_extract_docx_full_text(content).strip()) >= 10


def _make_docx_with_header_text(secret: str) -> bytes:
    """构造带页眉 (header) 的 DOCX，验证 full_text 不抽取页眉。"""
    from docx import Document

    doc = Document()
    section = doc.sections[0]
    section.header.paragraphs[0].text = secret
    doc.add_paragraph("实验报告正文第一段")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "腔长"
    table.cell(0, 1).text = "1.0m"
    table.cell(1, 0).text = "功率"
    table.cell(1, 1).text = "1.0mW"
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_docx_full_text_excludes_headers():
    """页眉文本不应出现在 full_text 中（RAG 噪声：学校模板页眉重复学号）。"""
    secret_header = "HEADER-SECRET-TOKEN-12345"
    content = _make_docx_with_header_text(secret_header)
    full = _extract_docx_full_text(content)
    assert "实验报告正文第一段" in full
    assert "腔长" in full
    assert "1.0mW" in full
    # 关键断言：页眉不应被抽取
    assert secret_header not in full, f"页眉文本不应出现在 full_text 中，实际输出: {full[:200]!r}"


def test_docx_metadata_title_prefers_top_level_paragraph():
    """标题解析只用顶级段落（不进入表格），避免表格列头「实验名称:」赢。"""
    from docx import Document

    doc = Document()
    doc.add_paragraph("激光谐振腔调谐实验报告：研究腔长对输出功率的影响")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "实验名称"  # 列头，5 字符
    table.cell(0, 1).text = "X"
    table.cell(1, 0).text = "腔长"  # 列头，2 字符
    table.cell(1, 1).text = "Y"
    buf = BytesIO()
    doc.save(buf)
    meta = _extract_docx_metadata(buf.getvalue(), "fallback.docx")
    # 标题应该是顶级段落（长 ★），而不是表格里的列头
    assert "激光谐振腔调谐实验报告" in meta["title"], f"标题应为顶级段落，实际: {meta['title']!r}"
    assert meta["title"] != "实验名称"
    assert meta["title"] != "腔长"


# 该测试与 test_docx_full_text_preserves_document_order + test_docx_full_text_extracts_table_content 重叠，删除避免维护成本。
