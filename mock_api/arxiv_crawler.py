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
    except Exception as e:  # noqa: BLE001 - arxiv HTTP - 单次拉取失败，下次跳过该关键词
        raise RuntimeError(f"解析 arXiv 响应失败：{e}") from e


def get_arxiv_by_id(arxiv_id: str) -> dict | None:
    """按 arXiv ID 查询单篇论文元数据。

    用 arXiv API 的 id_list 参数精确查询（比 search_query 更准确）。
    复用 _parse_arxiv_atom 解析逻辑。

    Args:
        arxiv_id: arXiv ID，如 "2307.07633"（可带版本号，会自动去）

    Returns:
        论文 dict（与 search_arxiv 返回的单项结构相同），未找到或查询失败返回 None。
    """
    arxiv_id = (arxiv_id or "").strip()
    if not arxiv_id:
        return None
    # 去掉可能的版本号
    arxiv_id = re.sub(r"v\d+$", "", arxiv_id)

    url = f"{ARXIV_API_URL}?id_list={quote(arxiv_id)}"
    try:
        resp = requests.get(url, timeout=15)
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    try:
        papers = _parse_arxiv_atom(resp.text)
    except Exception:  # noqa: BLE001 - 解析失败视为未找到
        return None

    if not papers:
        return None
    return papers[0]


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
    raw_id = _text(entry, "id") or ""
    arxiv_id = _extract_arxiv_id(raw_id)

    # 标题：去除多余空白与换行
    title = " ".join((_text(entry, "title") or "").split())

    # 摘要
    abstract = (_text(entry, "summary") or "").strip()

    # 作者列表：<author><name>...</name></author>
    authors = [(_text(author, "name") or "").strip() for author in entry.findall(f"{_NS}author")]
    authors = [a for a in authors if a]

    # 年份：从 <published>2021-06-17T17:40:40Z</published> 提取前 4 位
    published = _text(entry, "published") or ""
    year = _extract_year(published)

    # PDF URL：<link title="pdf" href="...">
    pdf_url = _find_pdf_link(entry)

    # 学科分类：<category term="cs.LG" />
    tags = _extract_categories(entry)

    # 将 arXiv 细分类别映射到大学科门类，避免按来源/source 分类
    category = _map_arxiv_categories_to_discipline(tags)

    return {
        "id": arxiv_id,
        "title": title,
        "authors": authors,
        "year": year,
        "abstract": abstract,
        "pdfUrl": pdf_url,
        "source": "arxiv",
        "category": category,
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


def _map_arxiv_categories_to_discipline(categories: list[str]) -> str:
    """将 arXiv 分类代码映射到大学科门类。

    映射规则参考 arXiv 官方分类与中国教育部学科门类对照。
    多个分类时，按优先级返回第一个能识别的门类；无法识别时归为交叉学科。
    """
    if not categories:
        return "interdisciplinary"

    # 类别优先级：越具体的排前面
    priority_order = [
        "cs",
        "eess",
        "math",
        "physics",
        "astro-ph",
        "cond-mat",
        "gr-qc",
        "hep-ex",
        "hep-lat",
        "hep-ph",
        "hep-th",
        "math-ph",
        "nlin",
        "nucl-ex",
        "nucl-th",
        "quant-ph",
        "q-bio",
        "q-fin",
        "stat",
        "econ",
    ]

    normalized = [c.lower().strip() for c in categories]

    # 按优先级排序分类
    def _sort_key(cat: str) -> int:
        prefix = cat.split(".")[0]
        try:
            return priority_order.index(prefix)
        except ValueError:
            return len(priority_order)

    sorted_cats = sorted(normalized, key=_sort_key)

    discipline_map = {
        # 工学
        "cs": "engineering",
        "eess": "engineering",
        "nlin": "engineering",
        # 理学
        "math": "science",
        "physics": "science",
        "astro-ph": "science",
        "cond-mat": "science",
        "gr-qc": "science",
        "hep-ex": "science",
        "hep-lat": "science",
        "hep-ph": "science",
        "hep-th": "science",
        "math-ph": "science",
        "nucl-ex": "science",
        "nucl-th": "science",
        "quant-ph": "science",
        "stat": "science",
        # 医学 / 生命科学
        "q-bio": "medicine",
        # 经济学
        "q-fin": "economics",
        "econ": "economics",
    }

    for cat in sorted_cats:
        # 优先精确匹配
        if cat in discipline_map:
            return discipline_map[cat]
        # 再按前缀匹配
        prefix = cat.split(".")[0]
        if prefix in discipline_map:
            return discipline_map[prefix]

    return "interdisciplinary"
