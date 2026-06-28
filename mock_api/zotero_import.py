"""Zotero API 客户端 —— 从 Zotero 文献库导入论文到 PaperForge。

B3：支持从 Zotero 用户库导入已有文献。
- GET https://api.zotero.org/users/{userID}/items
- 支持公开库（仅 userID）和私有库（userID + apiKey）
- 解析返回的 data 字段，提取标题、作者、年份、DOI、摘要
- 单次最多导入 100 篇（避免超时）
- 失败条目记录日志，不阻塞整体导入

Zotero API 文档：https://www.zotero.com/support/dev/web_api/v3/start
"""
from __future__ import annotations

from typing import Optional

import requests

API_BASE = "https://api.zotero.org"
TIMEOUT = 15  # 秒
MAX_ITEMS = 100  # 单次导入上限

# Zotero item type 映射到 PaperForge source
ITEM_TYPE_MAP = {
    "journalArticle": "zotero",
    "conferencePaper": "zotero",
    "preprint": "zotero",
    "book": "zotero",
    "bookSection": "zotero",
    "thesis": "zotero",
    "report": "zotero",
}


def fetch_zotero_items(user_id: str, api_key: str = "") -> list[dict]:
    """从 Zotero 用户库拉取文献条目。

    Args:
        user_id: Zotero 用户 ID（数字，在 Zotero 设置 > API 中查看）
        api_key: API Key（私有库必填，公开库可留空）

    Returns:
        解析后的文献列表，每项含：
        {"title", "authors", "year", "doi", "abstract", "item_type", "url"}
        失败时返回空列表（不抛异常，调用方静默降级）。
    """
    user_id = (user_id or "").strip()
    if not user_id:
        return []

    url = f"{API_BASE}/users/{user_id}/items"
    headers = {"Zotero-API-Version": "3"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    params = {
        "limit": MAX_ITEMS,
        "itemType": "journalArticle || conferencePaper || preprint || book || bookSection || thesis || report",
        "format": "json",
    }

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
    except requests.RequestException:
        return []

    if resp.status_code == 403:
        raise PermissionError("Zotero API Key 无效或无权限访问该用户库")
    if resp.status_code == 404:
        raise ValueError(f"Zotero 用户 {user_id} 不存在")
    if resp.status_code != 200:
        return []

    try:
        items = resp.json()
    except ValueError:
        return []

    parsed: list[dict] = []
    for item in items:
        parsed_item = _parse_zotero_item(item)
        if parsed_item and parsed_item["title"]:
            parsed.append(parsed_item)

    return parsed


def _parse_zotero_item(item: dict) -> Optional[dict]:
    """解析单个 Zotero item，提取 PaperForge 需要的字段。

    Zotero item 结构：
        {
            "key": "ABCD1234",
            "data": {
                "itemType": "journalArticle",
                "title": "...",
                "creators": [{"firstName": "...", "lastName": "...", "creatorType": "author"}, ...],
                "date": "2023-01-15",
                "DOI": "10.1145/...",
                "abstractNote": "...",
                "url": "..."
            }
        }
    """
    data = item.get("data") or {}
    title = (data.get("title") or "").strip()
    if not title:
        return None

    # 作者：creators 列表中 creatorType=author 的项
    authors: list[str] = []
    for creator in data.get("creators") or []:
        if creator.get("creatorType") != "author":
            continue
        first = (creator.get("firstName") or "").strip()
        last = (creator.get("lastName") or "").strip()
        # 机构作者（name 字段）或个人作者（firstName + lastName）
        name = creator.get("name") or " ".join(filter(None, [first, last])).strip()
        if name:
            authors.append(name)

    # 年份：从 date 字段提取（格式可能是 "2023-01-15" / "2023" / "2023-01"）
    year = 0
    raw_date = data.get("date") or ""
    if raw_date:
        # 提取第一个 4 位数字作为年份
        import re

        m = re.search(r"(\d{4})", str(raw_date))
        if m:
            year = int(m.group(1))

    doi = (data.get("DOI") or "").strip()
    abstract = (data.get("abstractNote") or "").strip()
    item_type = data.get("itemType") or "journalArticle"
    url = (data.get("url") or "").strip()

    return {
        "title": title,
        "authors": authors,
        "year": year,
        "doi": doi,
        "abstract": abstract,
        "item_type": item_type,
        "url": url,
    }
