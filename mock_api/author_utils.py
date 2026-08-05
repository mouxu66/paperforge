"""作者字段统一解析工具。

背景（2026-08-03 全库作者乱码修复）：papers.authors 在 SQLite 里历史上存在三种存储格式：

1. 标准 JSON 数组文本：``["Tim Dettmers", "Artidoro Pagnoni"]``（654 篇）
2. 双重编码：``"[\"Cosmin Pohoata\"]"`` —— 字符串包数组（207 篇），
   由 ``json.dumps(...)`` 写入 SQLAlchemy JSON 列导致再次序列化
3. 引号包裹的逗号分隔串：``"Edward J. Hu, Yelong Shen, ..."``（少量）

由于 ORM 声明为 JSON 列，读取时 SQLAlchemy 只做一层 ``json.loads``：
- 格式 1 → 正确得到 list
- 格式 2 → 得到字符串 ``["Cosmin Pohoata"]``
- 格式 3 → 得到字符串 ``Edward J. Hu, ...``

而旧代码用 ``list(p.authors or [])`` 读取，会把「字符串」逐字符拆成
``['[', '"', 'C', 'o', 's', ...]``，导致全站作者显示乱码。

本工具统一解析为 ``list[str]``。**所有读取 authors 的代码必须使用
``parse_authors``，禁止再用 ``list(...)`` 直接转换。**

额外防御（2026-08-03 code review）：
- 元素带包裹引号的逗号串（``"Alice","Bob"``）→ 解包引号
- 历史写入 ``list(d["authors"])`` 可能产生的逐字符数组 → 先 join 再解析
"""

from __future__ import annotations

import json


def _clean(parts: object) -> list[str]:
    """清洗元素列表：去空、解包裹引号、递归展开嵌套的 JSON 数组字符串。"""
    out: list[str] = []
    for p in parts if isinstance(parts, (list, tuple)) else []:
        if p is None:
            continue
        s = str(p).strip()
        if not s:
            continue
        # 元素本身是 JSON 数组字符串（历史脏数据 / 双层 list）
        if s.startswith("[") and s.endswith("]"):
            try:
                inner = json.loads(s)
                if isinstance(inner, list):
                    out.extend(_clean(inner))
                    continue
            except (json.JSONDecodeError, ValueError):
                pass
        # 元素本身是带引号的 JSON 字符串（如 '"Alice"'）→ 解包引号
        if s.startswith('"') and s.endswith('"') and len(s) >= 2:
            try:
                inner = json.loads(s)
                if isinstance(inner, str):
                    s = inner.strip()
            except (json.JSONDecodeError, ValueError):
                s = s.strip('"').strip()
        if s:
            out.append(s)
    return out


def _looks_like_char_array(parts: list) -> bool:
    """判断列表是否是由逐字符拆分产生的脏数据（元素均为超短字符串）。"""
    return bool(parts) and all(isinstance(p, str) and len(p) <= 2 for p in parts)


def _parse_string(s: str) -> list[str]:
    """解析字符串形态的 authors（JSON 数组 / 双重编码 / 逗号串）。"""
    s = s.strip()
    if not s:
        return []
    # 1. 尝试 JSON 解析（覆盖格式 1 / 2 / 3）
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return _clean(parsed)
        if isinstance(parsed, str):
            # 格式 2 / 3：JSON 字符串内仍是 JSON 数组或逗号分隔串
            inner = parsed.strip()
            if inner.startswith("["):
                try:
                    inner_parsed = json.loads(inner)
                    if isinstance(inner_parsed, list):
                        return _clean(inner_parsed)
                except (json.JSONDecodeError, ValueError):
                    pass
            return _clean([a for a in inner.split(",") if a.strip()])
    except (json.JSONDecodeError, ValueError):
        pass
    # 2. 非 JSON：按逗号分隔（可能是裸逗号串）
    return _clean([a for a in s.split(",") if a.strip()])


def parse_authors(raw: object) -> list[str]:
    """将任意格式的 authors 值统一解析为 ``list[str]``。

    支持的输入格式：
    - ``None`` → ``[]``
    - 真正的 list（ORM 正常读取 JSON 列的结果）
    - JSON 数组字符串（raw SQL 查询结果）
    - 双重编码字符串（JSON 字符串内嵌套数组或逗号串）
    - 逗号分隔字符串（含引号包裹的情况）
    - 逐字符数组（历史 ``list(str)`` 写入的脏数据，先 join 再解析）

    输出保证：非空、去首尾空白的人名列表；解析失败返回 ``[]``。
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        # 防御：逐字符拆分的历史脏数据（如 list('["A"]') 的副作用）
        if _looks_like_char_array(raw):
            joined = "".join(str(p) for p in raw).strip()
            if joined:
                reparsed = _parse_string(joined)
                if reparsed:
                    return reparsed
        return _clean(raw)
    if isinstance(raw, str):
        return _parse_string(raw)
    return []


def authors_to_fts(authors: list[str]) -> str:
    """将解析后的作者列表转为 FTS5 索引用的空格连接字符串。"""
    return " ".join(authors)
