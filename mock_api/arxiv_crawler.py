"""arXiv API 轻量爬虫。

查询 arXiv API 并解析 Atom XML 响应，返回论文元数据列表。
仅依赖 requests + 标准库 xml.etree.ElementTree。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

# arXiv API 端点
ARXIV_API_URL = "http://export.arxiv.org/api/query"

# Atom 命名空间
_ATOM_NS = "http://www.w3.org/2005/Atom"
# ElementTree 访问带命名空间元素时需加 {ns}tag 前缀
_NS = f"{{{_ATOM_NS}}}"


def search_arxiv(keyword: str, max_results: int = 10) -> list[dict]:
    """Search arXiv API and return paper dicts. Raises RuntimeError on network/parse errors."""
    keyword = (keyword or "").strip()
    if not keyword:
        raise RuntimeError("关键词不能为空")

    url = (
        f"{ARXIV_API_URL}?search_query=all:{quote(keyword)}"
        f"&start=0&max_results={max_results}"
        f"&sortBy=submittedDate&sortOrder=descending"
    )

    try:
        resp = requests.get(url, timeout=30)
    except requests.RequestException as e:
        raise RuntimeError(f"请求 arXiv API 失败：{e}") from e

    if resp.status_code != 200:
        raise RuntimeError(f"arXiv API 返回非 200 状态码：{resp.status_code}")

    try:
        return _parse_arxiv_atom(resp.text)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"解析 arXiv 响应失败：{e}") from e


def _parse_arxiv_atom(xml_text: str) -> list[dict]:
    """Parse arXiv Atom XML feed into paper dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise RuntimeError(f"XML 解析失败：{e}") from e

    papers: list[dict] = []
    # 每个 <entry> 即一篇论文
    for entry in root.findall(f"{_NS}entry"):
        papers.append(_parse_entry(entry))
    return papers


def _parse_entry(entry: ET.Element) -> dict:
    """解析单个 <entry> 元素为论文字典。"""
    # arXiv ID：从 <id>http://arxiv.org/abs/2106.09685v1</id> 提取并去版本号
    raw_id = _text(entry, "id")
    arxiv_id = _extract_arxiv_id(raw_id)

    # 标题：去除多余空白与换行
    title = " ".join((_text(entry, "title") or "").split())

    # 摘要
    abstract = (_text(entry, "summary") or "").strip()

    # 作者列表：<author><name>...</name></author>
    authors = [
        (_text(author, "name") or "").strip()
        for author in entry.findall(f"{_NS}author")
    ]
    authors = [a for a in authors if a]

    # 年份：从 <published>2021-06-17T17:40:40Z</published> 提取前 4 位
    published = _text(entry, "published") or ""
    year = _extract_year(published)

    # PDF URL：<link title="pdf" href="...">
    pdf_url = _find_pdf_link(entry)

    # 标签：<category term="cs.LG" />
    tags = _extract_categories(entry)

    return {
        "id": arxiv_id,
        "title": title,
        "authors": authors,
        "year": year,
        "abstract": abstract,
        "pdfUrl": pdf_url,
        "source": "arxiv",
        "category": "arxiv",
        "tags": tags,
    }


def _text(parent: ET.Element, tag: str) -> str | None:
    """读取带命名空间的子元素文本。"""
    el = parent.find(f"{_NS}{tag}")
    if el is None or el.text is None:
        return None
    return el.text


def _extract_arxiv_id(raw_id: str) -> str:
    """从 http://arxiv.org/abs/2106.09685v1 提取 2106.09685（去版本号）。"""
    if not raw_id:
        return ""
    # 取 /abs/ 之后的部分
    m = re.search(r"/abs/([^/]+)", raw_id)
    if not m:
        return raw_id.strip()
    aid = m.group(1)
    # 去掉末尾 vN 版本号
    aid = re.sub(r"v\d+$", "", aid)
    return aid


def _extract_year(published: str) -> int:
    """从 2021-06-17T17:40:40Z 提取年份整数。"""
    if not published:
        return 0
    m = re.match(r"(\d{4})", published)
    if m:
        return int(m.group(1))
    return 0


def _find_pdf_link(entry: ET.Element) -> str:
    """查找 title="pdf" 的 <link>，返回其 href。"""
    for link in entry.findall(f"{_NS}link"):
        title = link.get("title", "")
        if title == "pdf":
            return link.get("href", "")
    return ""


def _extract_categories(entry: ET.Element) -> list[str]:
    """收集所有 <category term="..."/> 的 term 值。"""
    tags: list[str] = []
    for cat in entry.findall(f"{_NS}category"):
        term = cat.get("term", "")
        if term:
            tags.append(term)
    return tags
