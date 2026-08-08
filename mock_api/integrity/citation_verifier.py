"""引用真值校验（ADR-014 · P4）。

解决 W8：DEPTH 与 reflection 流水线此前对「引用」只做**表面计数**
（数 [1][2][3] 有几个），完全不核验参考文献是否真实存在、是否真支持论断。
一篇论文可塞入大量编造参考文献而系统照样给高分。

本模块提供三层校验，全部 **fail-open**（网络/密钥/限流异常一律降级为
``unknown``，绝不因校验失败误杀论文或阻断评测管线）：

1. **DOI / arXiv 抽取**：从正文与参考文献块抽取标识符。
2. **Crossref 真值核验**：对每个 DOI 查询 Crossref；格式合法但 404 = 疑似编造。
3. **引用一致性**：文中引用集合 vs 参考文献列表集合的差异（漏引 / 虚列）。

默认离线（``PAPERFORGE_CITATION_VERIFY=0`` 或网络不可达）时仅做本地抽取与
一致性检查，不发起外部请求，结果标记 ``unchecked``，不影响现有评分。

环境变量：
- ``PAPERFORGE_CITATION_VERIFY``      : "1" 开启 Crossref 在线核验（默认 0/关闭）
- ``PAPERFORGE_CROSSREF_MAILTO``      : 传入 Crossref polite-pool 邮箱（可选，提升配额）
- ``PAPERFORGE_CITATION_VERIFY_CAP``  : 单篇最多核验的 DOI 数（默认 30，防止 bulk 卡死）
- ``PAPERFORGE_CITATION_VERIFY_TIMEOUT``: 单请求超时秒（默认 4.0）
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── 环境开关 ────────────────────────────────────────────────────────────────
def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_positive(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, str(default)))
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


ONLINE_ENABLED = lambda: _env_flag("PAPERFORGE_CITATION_VERIFY", False)
_CROSSREF_MAILTO = os.environ.get("PAPERFORGE_CROSSREF_MAILTO") or ""
_VERIFY_CAP = int(_env_positive("PAPERFORGE_CITATION_VERIFY_CAP", 30))
_VERIFY_TIMEOUT = _env_positive("PAPERFORGE_CITATION_VERIFY_TIMEOUT", 4.0)

_CROSSREF_BASE = "https://api.crossref.org/works/"

# ── 正则 ─────────────────────────────────────────────────────────────────
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\|\]\}\,<>\"'‘’“”)]{3,})\b", re.IGNORECASE)
_ARXIV_RE = re.compile(r"\barXiv:(\d{4}\.\d{4,5}(?:v\d+)?)\b", re.IGNORECASE)
# 参考文献块起始标记
_REF_HEAD_RE = re.compile(
    r"^\s*(references|bibliography| bibliography|参考文献|文献)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# 参考文献条目分隔：行首 [1] / 1. / [1]  Author
_REF_ENTRY_RE = re.compile(r"^\s*(?:\[(\d+)\]|\((\d+)\)|(\d+)[.\s])", re.MULTILINE)
# 仅用于切分的无捕获组版本（re.split 会插入捕获组内容，故切分必须用非捕获组）
_REF_SPLIT_RE = re.compile(r"^\s*(?:\[\d+\]|\(?\d+\)|\d+[.\s])", re.MULTILINE)
# 文中引用：[1], [1,2], (Author, 2004), \cite{x}
_INTEXT_BRACKET_RE = re.compile(r"\[(\d+(?:\s*[,;–\-]\s*\d+)*)\]")
_INTEXT_PAREN_RE = re.compile(r"\(([A-Za-z\-]+(?:\s+and\s+[A-Za-z\-]+)?,\s*\d{4})\)")


# ── 抽取 ───────────────────────────────────────────────────────────────────
def normalize_doi(doi: str) -> str:
    """清洗并小写化 DOI（Crossref 大小写不敏感，但前缀统一更稳）。"""
    doi = doi.strip().rstrip(".,;:")
    doi = re.sub(r"^doi:", "", doi, flags=re.IGNORECASE)
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.lower()


def extract_dois(text: str) -> list[str]:
    """从任意文本抽取去重后的 DOI 列表。"""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _DOI_RE.finditer(text):
        d = normalize_doi(m.group(1))
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def extract_arxiv_ids(text: str) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _ARXIV_RE.finditer(text):
        a = m.group(1)
        if a not in seen:
            seen.add(a)
            out.append(a)
    return out


def extract_reference_block(text: str) -> str:
    """抽取 References/Bibliography 之后的文本块（启发式）。

    找不到标题时返回空串（调用方据此跳过条目级分析）。
    """
    if not text:
        return ""
    m = _REF_HEAD_RE.search(text)
    if not m:
        return ""
    return text[m.end() :].strip()


def split_reference_entries(block: str) -> list[str]:
    """把参考文献块按条目切分（按行首编号）。

    使用无捕获组的 _REF_SPLIT_RE 切分，避免 re.split 把编号作为 None 元素插入。
    """
    if not block:
        return []
    parts = _REF_SPLIT_RE.split(block)
    entries: list[str] = []
    for p in parts:
        if p is None:
            continue
        p = p.strip()
        if len(p) >= 10:  # 过滤掉孤立的编号残片
            entries.append(p)
    if not entries:
        # 退化策略：按空行切
        entries = [e.strip() for e in block.split("\n\n") if len(e.strip()) >= 10]
    return entries


def extract_intext_citations(text: str) -> set[int]:
    """抽取文中方括号引用指向的编号集合，如 [1], [3, 5] -> {1,3,5}。"""
    nums: set[int] = set()
    if not text:
        return nums
    for m in _INTEXT_BRACKET_RE.finditer(text):
        for part in re.split(r"[\s,;–\-]+", m.group(1)):
            part = part.strip()
            if part.isdigit():
                nums.add(int(part))
    return nums


# ── Crossref 核验（fail-open） ──────────────────────────────────────────────
def _verify_single_doi(doi: str, session: Any | None = None) -> tuple[str, dict]:
    """核验单个 DOI。返回 (status, meta)。

    status ∈ {"verified", "not_found", "unknown"}
      - verified  : Crossref 200，文献存在
      - not_found : 格式合法但 Crossref 404（疑似编造）
      - unknown   : 网络/超时/限流/解析异常（fail-open，不判编造）
    """
    url = _CROSSREF_BASE + doi
    params = {"mailto": _CROSSREF_MAILTO} if _CROSSREF_MAILTO else None
    try:
        import requests

        resp = requests.get(url, params=params, timeout=_VERIFY_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json().get("message", {})
            return "verified", {
                "title": (data.get("title") or [""])[0],
                "year": (data.get("published", {}).get("date-parts", [[""]])[0][0])
                or data.get("issued", {}).get("date-parts", [[""]])[0][0],
                "doi": data.get("DOI"),
            }
        if resp.status_code == 404:
            return "not_found", {}
        # 其他状态码（429/5xx）降级为 unknown，不误判
        return "unknown", {"http_status": resp.status_code}
    except Exception as e:  # noqa: BLE001 - 任何异常都 fail-open
        logger.debug("Crossref 核验失败(doi=%s): %s", doi, e)
        return "unknown", {"error": type(e).__name__}


# ── 报告结构 ───────────────────────────────────────────────────────────────
@dataclass
class CitationReport:
    """单篇文献的引用真值校验报告（fail-open 安全结构）。"""

    status: str = "unknown"  # "verified" | "unchecked" | "unknown"
    total_dois: int = 0
    verified: int = 0
    not_found: int = 0  # 格式合法但 Crossref 查无 → 疑似编造
    unknown: int = 0  # 未核验 / 核验失败
    suspect_dois: list[str] = field(default_factory=list)
    arxiv_ids: list[str] = field(default_factory=list)
    consistency: dict = field(default_factory=dict)
    checked_dois: int = 0  # 实际发起核验的 DOI 数（受 CAP 限制）
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "total_dois": self.total_dois,
            "verified": self.verified,
            "not_found": self.not_found,
            "unknown": self.unknown,
            "suspect_dois": self.suspect_dois,
            "arxiv_ids": self.arxiv_ids,
            "consistency": self.consistency,
            "checked_dois": self.checked_dois,
            "error": self.error,
        }


def _consistency_check(full_text: str, reference_block: str) -> dict:
    """文中引用编号 vs 参考文献条目编号一致性（仅数字编号体系有效）。

    注意：split_reference_entries 会把行首编号切掉，因此参考文献编号直接
    用 _REF_ENTRY_RE.finditer 从原始块提取，避免编号丢失。
    """
    intext = extract_intext_citations(full_text)
    ref_nums: set[int] = set()
    for m in _REF_ENTRY_RE.finditer(reference_block):
        for g in m.groups():
            if g and g.isdigit():
                ref_nums.add(int(g))
                break
    if not intext and not ref_nums:
        return {"applicable": False, "cited_not_listed": [], "listed_not_cited": []}
    cited_not_listed = sorted(intext - ref_nums)
    listed_not_cited = sorted(ref_nums - intext)
    return {
        "applicable": True,
        "cited_count": len(intext),
        "listed_count": len(ref_nums),
        "cited_not_listed": cited_not_listed[:50],
        "listed_not_cited": listed_not_cited[:50],
    }


def verify_citations(
    full_text: str,
    *,
    verify_online: bool | None = None,
    cap: int | None = None,
) -> CitationReport:
    """对单篇文本执行引用真值校验（fail-open）。

    Args:
        full_text: 论文/感悟全文。
        verify_online: 强制覆盖在线核验开关；None 时读 ``PAPERFORGE_CITATION_VERIFY``。
        cap: 单篇最多核验 DOI 数；None 时读 ``PAPERFORGE_CITATION_VERIFY_CAP``。

    Returns:
        CitationReport（任何异常都被吞掉，返回 status="unknown" 的安全结果）。
    """
    try:
        if not full_text:
            return CitationReport(status="unknown", error="empty_text")
        online = ONLINE_ENABLED() if verify_online is None else verify_online
        cap = cap if cap is not None else _VERIFY_CAP

        ref_block = extract_reference_block(full_text)
        all_dois = extract_dois(full_text)
        arxiv = extract_arxiv_ids(full_text)
        consistency = _consistency_check(full_text, ref_block)

        report = CitationReport(
            total_dois=len(all_dois),
            arxiv_ids=arxiv,
            consistency=consistency,
        )

        if not online:
            # 离线模式：只做本地抽取与一致性，DOI 一律标 unchecked
            report.status = "unchecked"
            report.unknown = len(all_dois)
            return report

        # 在线核验（受 cap 限制，避免 bulk 卡死）
        checked = all_dois[:cap]
        report.checked_dois = len(checked)
        for doi in checked:
            status, _meta = _verify_single_doi(doi)
            if status == "verified":
                report.verified += 1
            elif status == "not_found":
                report.not_found += 1
                report.suspect_dois.append(doi)
            else:
                report.unknown += 1
        report.unknown += max(0, len(all_dois) - len(checked))  # 超出 cap 的部分按 unknown
        if report.not_found > 0:
            report.status = "suspect"  # 存在疑似编造
        elif report.verified > 0:
            report.status = "verified"
        else:
            report.status = "unknown"
        return report
    except Exception as e:  # noqa: BLE001 - 顶层 fail-open 兜底
        logger.warning("verify_citations 异常降级: %s", e)
        return CitationReport(status="unknown", error=type(e).__name__)


def assess_citation_integrity(full_text: str, **kwargs: Any) -> dict:
    """DEPTH / reflection 集成的便捷封装：返回可序列化 dict。

    额外给一个启发式 ``integrity_flag``：
      - "fabricated_suspected": 存在 Crossref 查无的 DOI（疑似编造）
      - "inconsistent": 引用编号与参考文献列表严重不一致
      - "ok": 未发现明显问题
      - "unknown": 未核验（离线/失败）
    """
    report = verify_citations(full_text, **kwargs)
    d = report.to_dict()
    flag = "unknown"
    if report.status == "suspect":
        flag = "fabricated_suspected"
    elif report.consistency.get("applicable") and (
        report.consistency.get("cited_not_listed") or report.consistency.get("listed_not_cited")
    ):
        flag = "inconsistent"
    elif report.status in ("verified", "unchecked") and report.total_dois > 0:
        flag = "ok"
    d["integrity_flag"] = flag
    return d
