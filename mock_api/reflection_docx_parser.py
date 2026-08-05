"""感悟报告 .docx 解析器（reflection report parser）。

把学生提交的「学号-姓名.docx」拆成结构化字段，供后续质量分析（fidelity / 4 维）使用：

    - student_id / name      从文件名提取（批阅场景关键元数据）
    - paper_title / author / source   从报告头部「论文题目：…」提取
    - sections                四段式：q(问题) / tech(技术) / exp(实验) / reflection(感想)
    - raw_text                全文（兜底）

设计约束：
    - 使用 python-docx 做主要解析（与 pdf_parser.py 保持一致），标准库仅做兜底
    - 不动原文件，只读
    - 与 depth_eval_reflection.py 解耦：本模块只负责「解析」，不负责「评分」
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .pdf_parser import _collect_docx_text_blocks

logger = logging.getLogger(__name__)

__all__ = ["ReflectionDoc", "parse_docx", "parse_docx_from_bytes"]


@dataclass
class ReflectionDoc:
    """一份感悟报告解析后的结构化表示。"""

    student_id: str | None = None
    name: str | None = None
    paper_title: str | None = None
    paper_author: str | None = None
    paper_source: str | None = None
    # 四段式内容：q / tech / exp / reflection（键缺失表示报告缺该段）
    sections: dict = field(default_factory=dict)
    raw_text: str = ""


# 主章节标记（无括号的「一、二、三、四、」；带括号的「（一）」是子节，不匹配）
_SECTION_MARKERS = [
    ("q", re.compile(r"^一、")),
    ("tech", re.compile(r"^二、")),
    ("exp", re.compile(r"^三、")),
    ("reflection", re.compile(r"^四、")),
]

# 报告头部字段（显式标签，优先级最高）
_HEADER_PATTERNS = {
    "paper_title": re.compile(r"^\s*(?:论文题目|论文标题)[:：]\s*(.+?)\s*$"),
    "paper_author": re.compile(r"^\s*(?:论文作者|作者)[:：]\s*(.+?)\s*$"),
    "paper_source": re.compile(r"^\s*(?:来源|发表会议|期刊)[:：]\s*(.+?)\s*$"),
}

# —— 语义关键词兜底分节 ——
# 当严格「一、二、三、四、」标记 0 命中时启用：按段首语义关键词把段落归入对应维度。
# 顺序重要：q 优先（问题/研究目的先判定），再 tech，再 exp，最后 reflection。
# 每个正则锚定段首，避免「实验日期」「实验题目」头部信息误判为 exp 节。
_KEYWORD_SECTIONS: list[tuple[str, re.Pattern]] = [
    # q：研究问题/研究目的/难题/论文研究了什么 / 【实验目的】/ a. 论文研究的问题
    (
        "q",
        re.compile(
            r"^(?:"
            r"问题\s*[1-4一二三四]|研究问题|研究目的|研究背景|难题|"
            r"论文研究了|论文主要研究|论文研究\w*什么|"
            r"\d+[.、]\s*(?:论文|本文|这篇)\s*(?:主要)?研究|"
            r"核心问题|研究内容|"
            r"【\s*实验目的\s*】|"
            r"[a-d][.、)]\s*(?:论文|本文).{0,4}(?:研究|问题)"
            r")"
        ),
    ),
    # tech：硬件结构/软件结构/机器人型号/技术架构 / b. 使用的机器人与软硬件结构
    (
        "tech",
        re.compile(
            r"^(?:"
            r"硬件(?:结构|基础|平台|组成)|软件(?:结构|框架|架构|系统|组成)|"
            r"机器人(?:型号|本体|平台|硬件|软件)|技术架构|系统架构|"
            r"软硬件|涉及.{0,4}机器人|核心.{0,2}硬件|核心.{0,2}软件|"
            r"\d+[.、]\s*(?:机器人|硬件|软件|系统|平台|核心)|"
            r"[a-d][.、)]\s*(?:使用|机器人|硬件|软件|系统|平台|核心)"
            r")"
        ),
    ),
    # exp：实验设计/实验结果/实验一/实验1/实验过程/实验内容/【实验内容】/【实验结果】/c. 实验与结果
    # 注意：排除「实验日期」「实验题目」「实验报告」头部，这些用负向断言挡掉
    (
        "exp",
        re.compile(
            r"^(?:"
            r"实验\s*(?:设计|结果|过程|内容|分析|一|二|三|1|2|3|4|数据|意义|现象)|"
            r"实验结果\w*含义|实验步骤|实验场景|实验设置|"
            r"【\s*实验(?:内容|结果|过程|分析)\s*】|"
            r"[a-d][.、)]\s*实验"
            r")"
        ),
    ),
    # reflection：感想/体会/反思/总结/心得/启示/收获 / d. 收获与感想 / 【实验结论】
    (
        "reflection",
        re.compile(
            r"^(?:"
            r"感想|体会|反思|总结|心得|启示|收获|个人感想|"
            r"实验结果\w*意义|意义\w*总结|"
            r"【\s*实验(?:结论|感想|总结)\s*】|"
            r"[a-d][.、)]\s*(?:收获|感想|体会|反思|总结|启示)"
            r")"
        ),
    ),
]

# 兜底：含英文论文标题特征（拉丁字母≥6 且含大写）
_TITLE_SKIP = re.compile(r"^(实验报告|阅读报告|研读报告|目录|目\s*录)$")
_SECTION_START = re.compile(r"^[一二三四（(]\s*[、.。)]")
_ENGLISH_TITLE = re.compile(r"[A-Za-z].*[A-Za-z]")  # 含至少两个拉丁片段
_READ_PREFIX = re.compile(r"^\s*阅读并理解[：:]?\s*(.+?)[，。,]\s*")
_NAMING_FMT = re.compile(r"命名格式[:：]\s*(.+?)\s*-\s*[\w]+\s*-\s*[\u4e00-\u9fa5]+")

# 文件名：学号-姓名.docx（学号可能含字母，姓名中文）
_FILENAME_RE = re.compile(r"([A-Za-z0-9]+)\s*[-_]\s*(.+?)\.docx?$", re.IGNORECASE)


def _extract_paragraphs_xml(xml: str) -> list[str]:
    """从 document.xml 提取非空段落文本（保留顺序）。

    此为 python-docx 不可用时（未安装 / 环境受限）的降级方案。
    使用 xml.etree.ElementTree 解析，比正则更可靠地处理 XML 实体和嵌套标签。
    """
    import xml.etree.ElementTree as ET

    paras: list[str] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return paras

    # WordprocessingML namespace
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    for p in root.findall(".//w:p", ns):
        texts = [t.text or "" for t in p.findall(".//w:t", ns)]
        line = "".join(texts).strip()
        if line:
            paras.append(line)
    return paras


def _extract_paragraphs(file_bytes: bytes) -> list[str]:
    """优先用 python-docx 提取段落（含表格内段落），失败时降级为 XML 解析。"""
    try:
        from docx import Document
    except ImportError:  # pragma: no cover - python-docx 未安装时的降级
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        return _extract_paragraphs_xml(xml)

    try:
        doc = Document(io.BytesIO(file_bytes))
        return _collect_docx_text_blocks(doc)
    except Exception:  # noqa: BLE001 - docx 结构多样 - 单字段解析失败不阻塞整体
        # python-docx 解析失败（如文件损坏），降级为 XML 解析
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        return _extract_paragraphs_xml(xml)


def parse_docx_from_bytes(file_bytes: bytes, filename: str = "") -> ReflectionDoc:
    """从内存字节解析一篇感悟报告 docx，返回结构化 ReflectionDoc。"""
    paras = _extract_paragraphs(file_bytes)
    doc = ReflectionDoc()

    # —— 文件名：学号-姓名 ——
    fname = (filename or "").replace("\\", "/").split("/")[-1]
    fm = _FILENAME_RE.search(fname)
    if fm:
        doc.student_id, doc.name = fm.group(1).strip(), fm.group(2).strip()

    # —— 头部字段 + 四段式分节 ——
    cur: str | None = None
    buf: list[str] = []
    for line in paras:
        stripped = line.strip()
        # 头部
        for key, pat in _HEADER_PATTERNS.items():
            hm = pat.match(stripped)
            if hm:
                setattr(doc, key, hm.group(1).strip())
        # 主章节切换
        switched = False
        for sid, marker in _SECTION_MARKERS:
            if marker.match(stripped):
                if cur:
                    doc.sections[cur] = "\n".join(buf).strip()
                cur = sid
                buf = [line]
                switched = True
                break
        if not switched and cur:
            buf.append(line)

    if cur:
        doc.sections[cur] = "\n".join(buf).strip()

    # —— 语义关键词兜底分节 ——
    # 仅当严格「一、二、三、四、」标记 0 命中时启用，避免破坏已有正确分节。
    # 逐段扫描，段首命中某类关键词 → 切换当前节；命中后该段归入对应节。
    if not doc.sections and len(paras) >= 3:
        cur2: str | None = None
        buf2: list[str] = []
        for line in paras:
            s = line.strip()
            switched2 = False
            for sid, kpat in _KEYWORD_SECTIONS:
                if kpat.match(s):
                    if cur2:
                        doc.sections[cur2] = "\n".join(buf2).strip()
                    cur2 = sid
                    buf2 = [line]
                    switched2 = True
                    break
            if not switched2 and cur2:
                buf2.append(line)
        if cur2:
            doc.sections[cur2] = "\n".join(buf2).strip()

    # —— 兜底标题提取（显式标签未命中时）——
    if not doc.paper_title:
        doc.paper_title = _extract_fallback_title(paras)

    doc.raw_text = "\n".join(paras)
    return doc


def parse_docx(path: str) -> ReflectionDoc:
    """解析一篇感悟报告 docx，返回结构化 ReflectionDoc。

    内部读取文件字节后委托 parse_docx_from_bytes，保持向后兼容。
    为降低路径遍历风险，仅允许读取 uploads/ 目录下的文件。
    """
    target = Path(path).resolve()
    uploads_dir = Path(__file__).resolve().parent.parent / "uploads"
    try:
        target.relative_to(uploads_dir)
    except ValueError as exc:
        raise ValueError(f"只允许读取 uploads/ 目录下的 docx 文件: {path}") from exc

    with open(target, "rb") as f:
        data = f.read()
    return parse_docx_from_bytes(data, filename=path)


def _extract_fallback_title(paras: list[str]) -> str | None:
    """多策略兜底抽取论文题目：

    1) 「阅读并理解《XXX》，…」→ 提取 XXX
    2) 「命名格式：主题 - 学号 - 姓名」→ 提取主题
    3) 首行英文标题特征（≥6 拉丁字母且含大写，非问句/非章节）
    """
    # 1) 阅读并理解 前缀
    for line in paras:
        m = _READ_PREFIX.search(line)
        if m:
            cand = m.group(1).strip().strip("《》")
            if cand:
                return cand

    # 2) 命名格式：主题 - 学号 - 姓名
    for line in paras:
        m = _NAMING_FMT.search(line)
        if m:
            topic = m.group(1).strip()
            if topic:
                return topic

    # 3) 英文标题首行
    for line in paras:
        s = line.strip()
        if not s or len(s) < 8 or len(s) > 200:
            continue
        if s.endswith(("？", "?")):
            continue
        if _SECTION_START.match(s) or _TITLE_SKIP.match(s):
            continue
        latin = re.findall(r"[A-Za-z]", s)
        if len(latin) >= 6 and any(c.isupper() for c in latin):
            return s
    return None


def extract_title_from_text(text: str) -> str | None:
    """从 report 的纯文本 full_text 中重新提取原论文题目（用于补识别任务）。

    优先匹配「论文题目：...」等显式标签；未命中则使用与 parse_docx 相同的
    兜底策略（阅读并理解/命名格式/英文首行）。
    """
    paras = [line.strip() for line in (text or "").splitlines() if line.strip()]

    # 显式标签：只取「论文题目/论文标题」
    title_pat = _HEADER_PATTERNS.get("paper_title")
    if title_pat:
        for line in paras:
            m = title_pat.match(line)
            if m:
                return m.group(1).strip()

    # 兜底策略
    return _extract_fallback_title(paras)


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else ""
    if not target:
        print("用法: python reflection_docx_parser.py <path.docx>")
        raise SystemExit(1)
    d = parse_docx(target)
    print("学号:", d.student_id)
    print("姓名:", d.name)
    print("论文题目:", d.paper_title)
    print("作者:", d.paper_author)
    print("来源:", d.paper_source)
    print("段落数:", len(d.sections), "->", list(d.sections.keys()))
    for k, v in d.sections.items():
        print(f"  [{k}] {len(v)} 字 | 首行: {v.splitlines()[0][:30] if v else ''}")
