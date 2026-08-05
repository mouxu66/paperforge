"""本地 NLP 工具包 —— 基于 spaCy / 规则引擎的轻量级文本预处理。

职责：
- 关键词抽取（TF-IDF + 停用词过滤）：为 RAG 检索提供精准 query 扩展
- 分句：按中英文标点切分句子，供 DEPTH 审稿段落对齐
- 章节切分：基于正则匹配识别论文章节标题，将全文切为结构化段落
- 语言检测：自动识别中/英/混合文本

设计原则：
- 优先使用 spaCy（高性能 C 扩展，毫秒级）；spaCy 未安装时降级为正则规则引擎
- 不替代 LLM，只做 CPU 轻量预处理，把 GPU/推理算力留给语义理解
- 所有方法为纯函数，无状态，可安全用于多线程并发
"""

from __future__ import annotations

import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# spaCy 懒加载
# ---------------------------------------------------------------------------
_nlp_en: object | None = None
_nlp_zh: object | None = None
_spacy_available: bool | None = None


def _check_spacy() -> bool:
    """检查 spaCy 是否可用（懒加载，仅首次调用时检查）。"""
    global _spacy_available
    if _spacy_available is None:
        try:
            import spacy  # noqa: F401

            _spacy_available = True
        except ImportError:
            _spacy_available = False
            logger.info("spaCy 未安装，NLP 将使用规则引擎降级。安装: pip install spacy>=3.7")
    return _spacy_available


def _get_nlp_en():
    """获取英文 spaCy 模型（懒加载）。"""
    global _nlp_en
    if _nlp_en is None and _check_spacy():
        try:
            import spacy

            _nlp_en = spacy.load("en_core_web_sm")
        except Exception:  # noqa: BLE001 - NLP helper - 退化路径兜底
            logger.info(
                "spaCy 英文模型 en_core_web_sm 未安装。安装: python -m spacy download en_core_web_sm"
            )
    return _nlp_en


def _get_nlp_zh():
    """获取中文 spaCy 模型（懒加载）。"""
    global _nlp_zh
    if _nlp_zh is None and _check_spacy():
        try:
            import spacy

            _nlp_zh = spacy.load("zh_core_web_sm")
        except Exception:  # noqa: BLE001 - NLP helper - 退化路径兜底
            logger.info(
                "spaCy 中文模型 zh_core_web_sm 未安装。安装: python -m spacy download zh_core_web_sm"
            )
    return _nlp_zh


# ---------------------------------------------------------------------------
# 停用词表（规则引擎降级用）
# ---------------------------------------------------------------------------
_EN_STOP_WORDS: set[str] = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "can",
    "shall",
    "to",
    "of",
    "in",
    "for",
    "on",
    "with",
    "at",
    "by",
    "from",
    "as",
    "into",
    "through",
    "during",
    "before",
    "after",
    "above",
    "below",
    "between",
    "under",
    "again",
    "further",
    "then",
    "once",
    "here",
    "there",
    "when",
    "where",
    "why",
    "how",
    "all",
    "both",
    "each",
    "few",
    "more",
    "most",
    "other",
    "some",
    "such",
    "no",
    "nor",
    "not",
    "only",
    "own",
    "same",
    "so",
    "than",
    "too",
    "very",
    "s",
    "t",
    "just",
    "don",
    "now",
    "d",
    "ll",
    "m",
    "o",
    "re",
    "ve",
    "y",
    "ain",
    "aren",
    "couldn",
    "didn",
    "doesn",
    "hadn",
    "hasn",
    "haven",
    "isn",
    "ma",
    "mightn",
    "mustn",
    "needn",
    "shan",
    "shouldn",
    "wasn",
    "weren",
    "won",
    "wouldn",
    "and",
    "but",
    "or",
    "if",
    "while",
    "about",
    "up",
    "out",
    "it",
    "its",
    "that",
    "this",
    "these",
    "those",
    "we",
    "you",
    "he",
    "she",
    "they",
    "them",
    "their",
    "our",
    "my",
    "your",
    "his",
    "her",
    "itself",
    "themselves",
    "ourselves",
}

_ZH_STOP_WORDS: set[str] = {
    "的",
    "了",
    "在",
    "是",
    "我",
    "有",
    "和",
    "就",
    "不",
    "人",
    "都",
    "一",
    "一个",
    "上",
    "也",
    "很",
    "到",
    "说",
    "要",
    "去",
    "你",
    "会",
    "着",
    "没有",
    "看",
    "好",
    "自己",
    "这",
    "他",
    "她",
    "它",
    "们",
    "那",
    "些",
    "所",
    "为",
    "所以",
    "因为",
    "但是",
    "然而",
    "如果",
    "虽然",
    "可以",
    "这个",
    "那个",
    "什么",
    "怎么",
    "哪",
    "吗",
    "呢",
    "啊",
    "吧",
    "哦",
    "之",
    "将",
    "对",
    "与",
    "及",
    "或",
    "把",
    "被",
    "从",
    "以",
    "让",
    "向",
    "往",
    "朝",
    "等",
    "等等",
    "其",
    "其中",
    "其他",
    "其它",
}


# ---------------------------------------------------------------------------
# 语言检测
# ---------------------------------------------------------------------------
def detect_language(text: str) -> str:
    """检测文本语言：en / zh / mixed。

    简单启发式：计算 CJK 字符占比，>30% 判定为中文，>10% 为混合。
    """
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    ratio = cjk / max(len(text), 1)
    if ratio > 0.3:
        return "zh" if ratio > 0.7 else "mixed"
    return "en"


# ---------------------------------------------------------------------------
# 关键词抽取
# ---------------------------------------------------------------------------
def extract_keywords(
    text: str,
    top_k: int = 10,
    min_len: int = 3,
    use_spacy: bool = True,
) -> list[str]:
    """从文本中抽取关键词（TF-IDF 近似 + 停用词过滤）。

    Args:
        text: 输入文本。
        top_k: 返回前 K 个关键词。
        min_len: 最小词长度（字符数）。
        use_spacy: 是否优先使用 spaCy（降级为正则分词）。

    Returns:
        按重要性排序的关键词列表。
    """
    text = (text or "").strip()
    if not text:
        return []

    lang = detect_language(text)
    stop_words = _EN_STOP_WORDS
    if lang == "zh":
        stop_words = _ZH_STOP_WORDS
    elif lang == "mixed":
        stop_words = _EN_STOP_WORDS | _ZH_STOP_WORDS

    nlp = None
    if use_spacy:
        nlp = _get_nlp_en() if lang == "en" else _get_nlp_zh()

    if nlp:
        # spaCy 路径：词性过滤 + 停用词过滤
        doc = nlp(text[:5000])  # 限制长度，避免过慢
        words = [
            token.lemma_.lower()
            for token in doc
            if not token.is_stop
            and not token.is_punct
            and len(token.lemma_) >= min_len
            and token.lemma_.isalpha()
            and token.pos_ in ("NOUN", "PROPN", "ADJ")
        ]
    else:
        # 规则引擎降级：简单分词 + 停用词过滤
        if lang == "zh":
            # 中文：按单字 + 双字组合分词
            text_clean = re.sub(r"[^\u4e00-\u9fff\w]", " ", text)
            # 提取所有 2-4 字的中文词组
            words_cn = re.findall(r"[\u4e00-\u9fff]{2,4}", text_clean)
            words_en = re.findall(r"[a-zA-Z]{2,}", text_clean)
            words = [w.lower() for w in words_cn + words_en if len(w) >= min_len]
        else:
            # 英文：按非字母字符分词
            words = re.findall(r"[a-zA-Z]{3,}", text.lower())

    # 停用词过滤 + 频率统计
    words = [w for w in words if w not in stop_words and len(w) >= min_len]
    if not words:
        return []

    counter = Counter(words)
    # 按频率排序取 top_k
    return [word for word, _ in counter.most_common(top_k)]


# ---------------------------------------------------------------------------
# 分句
# ---------------------------------------------------------------------------
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?。！？\n])\s+(?=[A-Z\u4e00-\u9fff])|"
    r"(?<=[.!?。！？])(?=[A-Z\u4e00-\u9fff])"
)


def split_sentences(text: str, use_spacy: bool = True) -> list[str]:
    """将文本切分为句子列表。

    Args:
        text: 输入文本。
        use_spacy: 是否优先使用 spaCy。

    Returns:
        句子列表（去首尾空白）。
    """
    text = (text or "").strip()
    if not text:
        return []

    lang = detect_language(text)
    nlp = None
    if use_spacy:
        nlp = _get_nlp_en() if lang == "en" else _get_nlp_zh()

    if nlp:
        doc = nlp(text[:10000])
        return [sent.text.strip() for sent in doc.sents if sent.text.strip()]

    # 规则引擎降级
    sentences = _SENTENCE_SPLIT_RE.split(text)
    # 过滤空句和过短句
    return [s.strip() for s in sentences if len(s.strip()) > 1]


# ---------------------------------------------------------------------------
# 章节切分
# ---------------------------------------------------------------------------
_SECTION_HEADING_RE = re.compile(
    r"(?:^|\n)\s*(?:(?:\d+\.?\s*)?(?:"
    r"Abstract|Introduction|Related Work|Background|Method|"
    r"Experiment|Result|Discussion|Conclusion|Reference|Appendix|"
    r"摘要|引言|绪论|前言|相关工作|背景|方法|实验|结果|讨论|结论|参考文献|附录"
    r"))\s*\n",
    re.IGNORECASE,
)


def segment_chapters(text: str) -> list[dict]:
    """将学术论文全文切分为章节段落。

    识别常见论文章节标题（中英文），将文本切分为：
    [{title: "Introduction", content: "..."}, {title: "Methods", content: "..."}, ...]

    Args:
        text: 论文全文。

    Returns:
        章节列表，每项包含 title 和 content。
    """
    text = (text or "").strip()
    if not text:
        return []

    matches = list(_SECTION_HEADING_RE.finditer(text))
    if not matches:
        # 无章节标题：整篇作为一个章节
        return [{"title": "Full Text", "content": text[:5000]}]

    chapters: list[dict] = []
    for i, match in enumerate(matches):
        title = match.group().strip()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if content:
            chapters.append({"title": title, "content": content[:3000]})

    return chapters


# ---------------------------------------------------------------------------
# RAG 查询扩展：关键词扩展 + 去重
# ---------------------------------------------------------------------------
def expand_query(query: str, top_k: int = 5) -> str:
    """为 RAG 检索扩展查询词。

    从查询文本中抽取关键词，附加到原始查询后，提升 FTS5 召回率。

    Args:
        query: 用户原始查询。
        top_k: 扩展关键词数量。

    Returns:
        扩展后的查询字符串（原始 + 关键词）。
    """
    keywords = extract_keywords(query, top_k=top_k)
    if not keywords:
        return query
    # 去重：移除已在原始查询中的关键词
    new_kw = [kw for kw in keywords if kw.lower() not in query.lower()]
    if not new_kw:
        return query
    return f"{query} {' '.join(new_kw)}"
