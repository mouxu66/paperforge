"""Semantic Scholar API 客户端 —— 拉取论文的引用数、影响力指数、研究领域。

B2：集成 Semantic Scholar 丰富论文元数据。
- GET https://api.semanticscholar.org/v1/paper/{paper_id}
- 返回 citations（引用数）、influentialCitationCount（有影响力引用）、fieldsOfStudy（研究领域）
- 超时 10 秒，失败时静默降级（返回 None），不阻塞主流程

paper_id 支持：
- arXiv ID（如 "1706.03762"）需加前缀 "ARXIV:" → "ARXIV:1706.03762"
- DOI（如 "10.1145/..."）需加前缀 "DOI:"
- Semantic Scholar 内部 ID（40 字符）直接使用
"""
from __future__ import annotations

from typing import Optional

import requests

API_BASE = "https://api.semanticscholar.org/v1/paper"
TIMEOUT = 10  # 秒


def _normalize_paper_id(paper_id: str) -> str:
    """将 PaperForge 内部的 paper_id 规范化为 Semantic Scholar 接受的格式。

    - arXiv ID（如 "1706.03762"）→ "ARXIV:1706.03762"
    - 上传的 PDF ID（"upload_xxx"）→ 无法识别，返回原值（大概率查询失败）
    - 已带前缀（ARXIV:/DOI:）→ 原样返回
    """
    pid = (paper_id or "").strip()
    if not pid:
        return pid
    if pid.startswith(("ARXIV:", "DOI:", "PMID:", "CORPUS:")):
        return pid
    # arXiv ID 格式：纯数字 + 点（如 1706.03762）或带版本号（1706.03762v1）
    if "." in pid and all(c.isdigit() or c in ".v" for c in pid):
        return f"ARXIV:{pid}"
    return pid


def fetch_paper_metadata(paper_id: str) -> Optional[dict]:
    """从 Semantic Scholar 拉取论文元数据。

    Args:
        paper_id: PaperForge 内部 paper_id（arXiv ID 或 DOI）。

    Returns:
        {"citations": int, "influential_citations": int, "fields_of_study": list[str]}
        失败时返回 None（不抛异常，调用方静默降级）。
    """
    s2_id = _normalize_paper_id(paper_id)
    if not s2_id:
        return None

    url = f"{API_BASE}/{s2_id}"
    # 仅请求需要的字段，减少传输量
    params = {"fields": "citationCount,influentialCitationCount,fieldsOfStudy"}

    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException:
        return None

    if resp.status_code != 200:
        return None

    try:
        data = resp.json()
    except ValueError:
        return None

    return {
        "citations": int(data.get("citationCount") or 0),
        "influential_citations": int(data.get("influentialCitationCount") or 0),
        "fields_of_study": list(data.get("fieldsOfStudy") or []),
    }


def is_available() -> bool:
    """检查 Semantic Scholar API 是否可访问（轻量 HEAD 请求）。

    仅在调试/诊断时使用，正常流程直接调用 fetch_paper_metadata 并按 None 降级。
    """
    try:
        resp = requests.get(f"{API_BASE}/ARXIV:1706.03762", timeout=TIMEOUT)
        return resp.status_code == 200
    except requests.RequestException:
        return False
