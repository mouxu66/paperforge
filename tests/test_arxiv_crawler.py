"""测试 mock_api/arxiv_crawler.py 中的 arXiv 爬虫。

覆盖场景：
- search_arxiv：空关键词 / 正常请求 / 网络异常 / 非 200 状态码
- _parse_arxiv_atom：合法 Atom XML / 非法 XML
- _extract_arxiv_id：带版本号 / 不带版本号 / 空字符串
- _extract_year：正常日期 / 空字符串 / 非法字符串
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
import requests

from mock_api.arxiv_crawler import (
    search_arxiv,
    _parse_arxiv_atom,
    _extract_arxiv_id,
    _extract_year,
)


# ---------------------------------------------------------------------------
# 测试夹具：构造 arXiv Atom XML 响应样本（含 2 篇论文）
# ---------------------------------------------------------------------------
SAMPLE_ATOM_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2106.09685v1</id>
    <title>Attention Is All You Need</title>
    <summary>The dominant sequence transduction models are based on complex recurrent or convolutional neural networks.</summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
    <published>2017-06-17T17:40:40Z</published>
    <link title="pdf" href="http://arxiv.org/pdf/2106.09685v1"/>
    <category term="cs.CL"/>
    <category term="cs.AI"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.12345</id>
    <title>  A   Paper  With   Extra   Whitespace  </title>
    <summary>Another abstract.</summary>
    <author><name>Jane Doe</name></author>
    <published>2024-01-15T00:00:00Z</published>
    <link title="pdf" href="http://arxiv.org/pdf/2401.12345"/>
    <category term="cs.LG"/>
  </entry>
</feed>
"""


# ---------------------------------------------------------------------------
# search_arxiv：空关键词
# ---------------------------------------------------------------------------
def test_search_arxiv_empty_keyword_raises():
    """空关键词 → 抛出 RuntimeError，提示「关键词不能为空」。"""
    with pytest.raises(RuntimeError, match="关键词不能为空"):
        search_arxiv("")


def test_search_arxiv_whitespace_keyword_raises():
    """仅空白字符的关键词 → 抛出 RuntimeError，提示「关键词不能为空」。"""
    with pytest.raises(RuntimeError, match="关键词不能为空"):
        search_arxiv("   ")


# ---------------------------------------------------------------------------
# search_arxiv：正常请求
# ---------------------------------------------------------------------------
def test_search_arxiv_success():
    """mock requests.get 返回 200 + Atom XML → 返回解析后的论文列表。"""
    mock_resp = MagicMock(status_code=200, text=SAMPLE_ATOM_XML)
    with patch("mock_api.arxiv_crawler.requests.get", return_value=mock_resp) as mock_get:
        papers = search_arxiv("transformer", max_results=5)

    # 应当发起一次 GET 请求
    assert mock_get.call_count == 1
    # 返回两篇论文
    assert len(papers) == 2
    # 第一篇字段校验
    first = papers[0]
    assert first["id"] == "2106.09685"
    assert first["title"] == "Attention Is All You Need"
    assert first["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert first["year"] == 2017
    assert "sequence transduction" in first["abstract"]
    assert first["pdfUrl"] == "http://arxiv.org/pdf/2106.09685v1"
    assert first["tags"] == ["cs.CL", "cs.AI"]
    assert first["source"] == "arxiv"
    assert first["category"] == "arxiv"


# ---------------------------------------------------------------------------
# search_arxiv：网络异常
# ---------------------------------------------------------------------------
def test_search_arxiv_network_error():
    """requests.get 抛出 RequestException → RuntimeError，提示「请求 arXiv API 失败」。"""
    with patch("mock_api.arxiv_crawler.requests.get", side_effect=requests.RequestException("timeout")):
        with pytest.raises(RuntimeError, match="请求 arXiv API 失败"):
            search_arxiv("neural network")


# ---------------------------------------------------------------------------
# search_arxiv：非 200 状态码
# ---------------------------------------------------------------------------
def test_search_arxiv_non_200():
    """响应状态码 500 → RuntimeError，提示「非 200」。"""
    mock_resp = MagicMock(status_code=500, text="")
    with patch("mock_api.arxiv_crawler.requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="非 200"):
            search_arxiv("neural network")


# ---------------------------------------------------------------------------
# _parse_arxiv_atom：合法 XML
# ---------------------------------------------------------------------------
def test_parse_arxiv_atom_valid():
    """解析含 2 个 entry 的 Atom XML → 返回 2 篇论文且字段正确。"""
    papers = _parse_arxiv_atom(SAMPLE_ATOM_XML)

    assert len(papers) == 2

    # 第一篇：带版本号的 ID 应去掉 v1
    first = papers[0]
    assert first["id"] == "2106.09685"
    assert first["title"] == "Attention Is All You Need"
    assert first["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert first["year"] == 2017
    assert "sequence transduction" in first["abstract"]
    assert first["pdfUrl"] == "http://arxiv.org/pdf/2106.09685v1"
    assert first["tags"] == ["cs.CL", "cs.AI"]

    # 第二篇：不带版本号、标题含多余空白应被规整
    second = papers[1]
    assert second["id"] == "2401.12345"
    assert second["title"] == "A Paper With Extra Whitespace"
    assert second["authors"] == ["Jane Doe"]
    assert second["year"] == 2024
    assert second["pdfUrl"] == "http://arxiv.org/pdf/2401.12345"
    assert second["tags"] == ["cs.LG"]


# ---------------------------------------------------------------------------
# _parse_arxiv_atom：非法 XML
# ---------------------------------------------------------------------------
def test_parse_arxiv_atom_invalid_xml():
    """非法 XML 字符串 → 抛出 RuntimeError，提示「XML 解析失败」。"""
    with pytest.raises(RuntimeError, match="XML 解析失败"):
        _parse_arxiv_atom("<not valid xml")


# ---------------------------------------------------------------------------
# _extract_arxiv_id
# ---------------------------------------------------------------------------
def test_extract_arxiv_id_with_version():
    """带版本号的 URL → 去掉版本号。"""
    assert _extract_arxiv_id("http://arxiv.org/abs/2106.09685v1") == "2106.09685"


def test_extract_arxiv_id_without_version():
    """不带版本号的 URL → 原样返回 ID。"""
    assert _extract_arxiv_id("http://arxiv.org/abs/2401.12345") == "2401.12345"


def test_extract_arxiv_id_empty():
    """空字符串 → 返回空字符串。"""
    assert _extract_arxiv_id("") == ""


# ---------------------------------------------------------------------------
# _extract_year
# ---------------------------------------------------------------------------
def test_extract_year_normal():
    """合法 ISO 日期 → 返回年份整数。"""
    assert _extract_year("2021-06-17T17:40:40Z") == 2021


def test_extract_year_empty():
    """空字符串 → 返回 0。"""
    assert _extract_year("") == 0


def test_extract_year_invalid():
    """无法匹配 4 位数字 → 返回 0。"""
    assert _extract_year("invalid") == 0