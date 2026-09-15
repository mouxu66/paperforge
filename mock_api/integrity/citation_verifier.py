"""引用真值校验（ADR-014 · P4）。

解决 W8：DEPTH 与 reflection 流水线此前对「引用」只做**表面计数**
（数 [1][2][3] 有几个），完全不核验参考文献是否真实存在、是否真支持论断。
一篇论文可塞入大量编造参考文献而系统照样给高分。

本模块提供三层校验，全部 **fail-open**（网络/密钥/限流异常一律降级为
``unknown``，绝不因校验失败误杀论文或阻断评测管线）：

1. **DOI / arXiv 抽取**：从正文与参考文献块抽取标识符。
2. **Crossref 真值核验**：对每个 DOI 查询 Crossref；格式合法但 404 = 疑似编造。
3. **引用一致性**：文中引用集合 vs 参考文献列表集合的差异（漏引 / 虚列）。

默认在线核验（Crossref）；设 ``PAPERFORGE_CITATION_VERIFY=0``/``offline``
或网络不可达时降级为仅本地抽取 + 一致性 + 占位符/假 arXiv 启发式，
不发起外部请求，结果标记 ``unchecked``（除非本地启发式命中，则标 ``suspect``）。

环境变量：
- ``PAPERFORGE_CITATION_VERIFY``      : 默认开启 Crossref 在线核验；设 "0"/"offline"
                                        关闭（仅本地抽取 + 一致性 + 占位符/假 arXiv 启发式）
- ``PAPERFORGE_CROSSREF_MAILTO``      : 传入 Crossref polite-pool 邮箱（可选，提升配额）
- ``PAPERFORGE_CITATION_VERIFY_CAP``  : 单篇最多核验的 DOI 数（默认 30，防止 bulk 卡死）
- ``PAPERFORGE_CITATION_VERIFY_TIMEOUT``: 单请求超时秒（默认 4.0）

另含零网络本地启发式（无论在线/离线都会执行）：
- 占位符 DOI（尾段含 ≥6 位单调递增数字串，如 10.5555/1234567.8901234）
- 假 arXiv 号（月份非法，或 5 位序列号单调递增/递减，如 2501.98765）
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── 环境开关（OS env 优先，其次 pydantic Settings 读 .env）─────────────────
# 历史问题：此前只读 os.environ，导致 .env 里的 PAPERFORGE_CITATION_VERIFY 等
# 配置不生效。这里统一收口：OS env 命中即用，否则回退 Settings（含 .env）。
_SETTING_ATTR = {
    "PAPERFORGE_CITATION_VERIFY": "citation_verify",
    "PAPERFORGE_CROSSREF_MAILTO": "crossref_mailto",
    "PAPERFORGE_CITATION_VERIFY_CAP": "citation_verify_cap",
    "PAPERFORGE_CITATION_VERIFY_TIMEOUT": "citation_verify_timeout",
}


def _setting_str(name: str) -> str | None:
    """从 pydantic Settings 读配置（覆盖 .env）；无字段/空值/异常时返回 None。"""
    attr = _SETTING_ATTR.get(name)
    if not attr:
        return None
    try:
        from ..settings import get_settings

        val = getattr(get_settings(), attr, None)
        if val in (None, ""):
            return None
        return str(val)
    except Exception:  # noqa: BLE001 - 配置读取失败回退 os.environ/默认值
        return None


def _env_str(name: str) -> str | None:
    """OS env 优先，其次 .env（经 Settings）。均未设置返回 None。"""
    raw = os.environ.get(name)
    if raw is None:
        raw = _setting_str(name)
    return raw


def _env_flag(name: str, default: bool = False) -> bool:
    raw = _env_str(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_positive(name: str, default: float) -> float:
    raw = _env_str(name)
    try:
        v = float(raw if raw not in (None, "") else default)
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


ONLINE_ENABLED = lambda: _env_flag("PAPERFORGE_CITATION_VERIFY", True)
_CROSSREF_MAILTO = _env_str("PAPERFORGE_CROSSREF_MAILTO") or ""
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
# 注意：真实条目编号后一定跟作者/标题等文字（如 "31. Tang LQ, ..."），
# 而跨行落在行首的孤立页码（如 "118." / "1128."）后面没有文字，
# 若不排除会被误当条目编号 → listed_not_cited 假阳性。
# 括号形式 (N) 限 1-3 位：行首 "(2007)" 是出版年份而非条目编号。
_REF_ENTRY_RE = re.compile(r"^[ \t]*(?:\[(\d+)\]|\((\d{1,3})\)|(\d+)[.])[ \t]+\S", re.MULTILINE)
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


# ── 本地启发式（零网络，占位符 DOI / 假 arXiv 号）───────────────────────
def _monotonic_digit_run(s: str, min_len: int) -> bool:
    """检测字符串中是否存在 ≥min_len 位的连续单调数字串。

    同时覆盖递增与递减，且允许 9→0 回绕（如 89012 视为递增、90123 视为递增）。
    非数字字符会中断连续性。真实 DOI/arXiv 序列号近乎随机，几乎不会出现
    这种「全序数字串」，是典型的占位符/编造指纹。
    """
    asc = desc = 1
    for i in range(1, len(s)):
        a, b = s[i - 1], s[i]
        if not (a.isdigit() and b.isdigit()):
            asc = desc = 1
            continue
        asc = asc + 1 if (ord(b) - ord(a)) % 10 == 1 else 1
        desc = desc + 1 if (ord(a) - ord(b)) % 10 == 1 else 1
        if asc >= min_len or desc >= min_len:
            return True
    return False


def detect_placeholder_dois(dois: list[str]) -> list[str]:
    """本地启发式：占位符 DOI（无需联网）。

    命中特征：DOI 尾段（注册码之后）含 ≥6 位单调递增数字串
    （如 ``10.5555/1234567.8901234`` 的 ``1234567`` / ``8901234``）。
    """
    out: list[str] = []
    for doi in dois:
        suffix = doi.split("/", 1)[1] if "/" in doi else doi
        if _monotonic_digit_run(suffix, 6):
            out.append(doi)
    return out


def detect_fake_arxiv_ids(arxiv_ids: list[str]) -> list[str]:
    """本地启发式：假 arXiv 号（无需联网）。

    命中特征：
    1. 新式 ``YYMM.NNNNN`` 的月份非法（MM<1 或 >12，如 ``2513.xxxxx``）；
    2. 5 位序列号呈单调递增/递减（如 ``12345`` / ``98765``）——真实序列号
       近乎随机，不会出现全序数字串。
    """
    out: list[str] = []
    for aid in arxiv_ids:
        m = re.match(r"^(\d{2})(\d{2})\.(\d{4,5})(?:v\d+)?$", aid)
        if not m:
            continue
        mm = int(m.group(2))
        serial = m.group(3)
        if mm < 1 or mm > 12:
            out.append(aid)
            continue
        if _monotonic_digit_run(serial, 5):
            out.append(aid)
    return out


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
    placeholder_dois: list[str] = field(default_factory=list)  # 本地启发式：占位符 DOI
    suspect_arxiv_ids: list[str] = field(default_factory=list)  # 本地启发式：假 arXiv 号
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
            "placeholder_dois": self.placeholder_dois,
            "suspect_arxiv_ids": self.suspect_arxiv_ids,
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

        # 本地启发式（零网络，无论在线/离线都执行）
        placeholder = detect_placeholder_dois(all_dois)
        fake_arxiv = detect_fake_arxiv_ids(arxiv)
        has_local_suspect = bool(placeholder or fake_arxiv)

        report = CitationReport(
            total_dois=len(all_dois),
            arxiv_ids=arxiv,
            placeholder_dois=placeholder,
            suspect_arxiv_ids=fake_arxiv,
            consistency=consistency,
        )

        if not online:
            # 离线模式：只做本地抽取/一致性/启发式，DOI 一律标 unchecked；
            # 占位符 DOI / 假 arXiv 号仍会被本地启发式标记为 suspect。
            report.status = "suspect" if has_local_suspect else "unchecked"
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
        if report.not_found > 0 or has_local_suspect:
            report.status = "suspect"  # 存在疑似编造（Crossref 查无 / 本地启发式）
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
      - "fabricated_suspected": 存在 Crossref 查无的 DOI，或占位符 DOI /
        假 arXiv 号本地启发式命中（疑似编造）
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
