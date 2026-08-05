"""AI 生成文本疑似度评分（事后检测，仅供参考 / advisory only）。

⚠️⚠️⚠️ 重要限制 —— 务必传达给使用者 ⚠️⚠️⚠️
    本模块是「事后检测」：在【已提交】文本上猜测是否由 AI 生成。
    对【中文】极不可靠，误判率高，绝不能作为处分依据，仅供老师「参考 / 提醒」。
    可靠的识别只能靠「过程溯源」（学生在工具内写作时记录每段 AI 参与度），
    但本项目当前场景是学生交【独立 .docx】，无写作过程，故只能走本条弱信号路线。

设计原则（与现有评分链路解耦）：
    - 本地、零模型依赖、瞬时可跑，不调 LLM、不联网。
    - 综合多个【弱信号】给出 0~1 的疑似度 ai_likelihood，并输出每个信号的
      子分数与命中样例，便于人工复核，而非黑箱定罪。
    - 本结果【绝不】进入 scores / verdict（学术评分），仅作为并列的 advisory 字段。

信号（均为经验性弱信号，权重见 WEIGHTS，可离线校准）：
    1. connector_density  — 衔接词过载（首先/其次/此外/综上所述…）AI 中文高频
    2. hedge_density      — 套话 / 空泛断言（可以看出/不难发现/日益重要…）
    3. vague_opener_ratio — 空泛开头比例（随着…的发展/在当今社会…）
    4. length_uniformity  — 句长均匀度（burstiness 反向）：AI 句长更整齐划一
    5. personal_scarcity  — 个人体验标记稀缺（我/觉得/体会/这次…）人类反思常有
    6. punct_diversity    — 标点多样性低（AI 多用 ，。 少 ？！（）《》）

输出 dict：
    {
      "ai_likelihood": float 0~1,
      "tier": "human" | "uncertain" | "likely_ai",
      "signals": { name: {"score":0~1, "hits":[...], "weight":float} },
      "confidence": "low",
      "note": "仅供参考，非证据；中文事后检测误判率高",
      "text_chars": int,
    }
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# —— 信号权重（合计 1.0；待离线用已知人机样本校准，此处为经验初值）——
WEIGHTS = {
    "connector_density": 0.22,
    "hedge_density": 0.20,
    "vague_opener_ratio": 0.18,
    "length_uniformity": 0.15,
    "personal_scarcity": 0.15,
    "punct_diversity": 0.10,
}

# tier 阈值（经验初值，需校准）
TIER_LIKELY = 0.60  # >= 此 → likely_ai
TIER_UNCERTAIN = 0.35  # >= 此且 < TIER_LIKELY → uncertain；否则 human

# —— 词表 ——
CONNECTORS = [
    "首先",
    "其次",
    "再者",
    "此外",
    "另外",
    "与此同时",
    "除此之外",
    "总之",
    "综上所述",
    "总而言之",
    "值得注意的是",
    "需要指出的是",
    "不可否认",
    "毋庸置疑",
    "显而易见",
    "诚然",
    "固然",
]
HEDGES = [
    "可以看出",
    "不难发现",
    "由此可见",
    "总的来说",
    "总体而言",
    "在一定程度上",
    "从某种程度上说",
    "具有十分重要的意义",
    "发挥着重要作用",
    "日益重要",
    "不可或缺",
    "值得关注",
    "众所周知",
    "随着时代的发展",
    "随着科技的发展",
    "扮演着重要角色",
    "具有重要意义",
]
VAGUE_OPENERS = [
    "随着",
    "在当今社会",
    "在当今时代",
    "在信息化时代",
    "近年来",
    "在当前背景下",
    "在…的今天",
    "在知识经济时代",
]
PERSONAL = [
    "我",
    "我们",
    "觉得",
    "感受",
    "体会",
    "这次",
    "我注意到",
    "我的",
    "我个人",
    "回想",
    "记得",
    "起初",
    "后来",
    "一开始",
    "令我",
    "让我",
]

# 句长均匀度参考 CV（变异系数）；高于此视为「足够有起伏」（human-like）
CV_REF = 0.6

_PUNCT_CHARS = set("。，！？；：（）《》“”‘’、…—.,!?;:()[]\"'")


def split_sentences(text: str) -> list[str]:
    """中英句号/问号/叹号/换行切分（与 reflection_fidelity 同逻辑）。"""
    parts = re.split(r"[。！？!?\n]+", text or "")
    return [p.strip() for p in parts if p.strip()]


def _count_hits(text: str, phrases: list[str]) -> tuple[int, list[str]]:
    """统计短语命中数，返回 (次数, 去重样例)。中文无需分词，子串匹配即可。"""
    hits: list[str] = []
    total = 0
    for ph in phrases:
        n = text.count(ph)
        if n:
            total += n
            if len(hits) < 5:
                hits.append(ph)
    return total, hits


def _safe_ratio(num: int, denom: int) -> float:
    return (num / denom) if denom else 0.0


def compute_ai_likelihood(text: str) -> dict:
    """计算中文文本的 AI 生成疑似度（事后检测，仅供参考）。

    Args:
        text: 报告全文（任意长度；过短会退化为 low-confidence）。
    Returns:
        见模块 docstring 的输出结构。
    """
    raw = (text or "").strip()
    chars = len(raw)
    empty = {
        "ai_likelihood": 0.0,
        "tier": "human",
        "signals": {},
        "confidence": "low",
        "note": "文本过短或为空，无法评估；仅供参考，非证据",
        "text_chars": chars,
    }
    if chars < 80:
        return empty

    sents = split_sentences(raw)
    signals: dict = {}

    # 1) connector_density：每千字衔接词命中数，归一化
    conn_n, conn_hits = _count_hits(raw, CONNECTORS)
    conn_rate = conn_n / max(chars / 1000.0, 1e-9)
    # 经验：>12 次/千字偏 AI；clamp 到 0~1
    signals["connector_density"] = {
        "score": min(conn_rate / 12.0, 1.0),
        "hits": conn_hits,
        "weight": WEIGHTS["connector_density"],
    }

    # 2) hedge_density：每千字套话命中数
    hedge_n, hedge_hits = _count_hits(raw, HEDGES)
    hedge_rate = hedge_n / max(chars / 1000.0, 1e-9)
    signals["hedge_density"] = {
        "score": min(hedge_rate / 8.0, 1.0),
        "hits": hedge_hits,
        "weight": WEIGHTS["hedge_density"],
    }

    # 3) vague_opener_ratio：段/句级空泛开头比例（按句统计以首 4 字匹配）
    vague_n = 0
    for s in sents:
        head = s[:4]
        if any(head.startswith(v[:2]) and v in s[:20] for v in VAGUE_OPENERS):
            vague_n += 1
    vratio = _safe_ratio(vague_n, len(sents)) if sents else 0.0
    signals["vague_opener_ratio"] = {
        "score": min(vratio / 0.30, 1.0),  # >30% 句以空泛词开头偏 AI
        "hits": [s[:12] for s in sents[:3] if any(v in s[:20] for v in VAGUE_OPENERS)],
        "weight": WEIGHTS["vague_opener_ratio"],
    }

    # 4) length_uniformity：句长变异系数反向（越整齐越 AI-like）
    if len(sents) >= 3:
        lengths = [len(s) for s in sents]
        mean = sum(lengths) / len(lengths)
        if mean > 0:
            var = sum((x - mean) ** 2 for x in lengths) / len(lengths)
            cv = (var**0.5) / mean
        else:
            cv = 0.0
        uniformity = 1.0 - min(cv / CV_REF, 1.0)  # CV 越小 → uniformity 越接近 1
    else:
        uniformity = 0.0
    signals["length_uniformity"] = {
        "score": uniformity,
        "hits": [f"句数={len(sents)}, 均值长度={int(mean) if len(sents) >= 3 else 0}"],
        "weight": WEIGHTS["length_uniformity"],
    }

    # 5) personal_scarcity：个人体验标记稀缺 → 偏 AI
    pers_n, pers_hits = _count_hits(raw, PERSONAL)
    # 经验：整篇 <3 处个人标记偏 AI；clamp
    scarcity = 1.0 - min(pers_n / 3.0, 1.0)
    signals["personal_scarcity"] = {
        "score": scarcity,
        "hits": pers_hits if pers_n else ["（全文未见个人体验标记）"],
        "weight": WEIGHTS["personal_scarcity"],
    }

    # 6) punct_diversity：标点类型多样性低 → 偏 AI
    used = {c for c in raw if c in _PUNCT_CHARS}
    # 常见标点池（中英文逗号句号问叹分号冒号括号引号破折省略）
    common_pool = set("，。！？；：（）《》“”‘’、…—,.!?;:()")
    diversity = _safe_ratio(len(used), len(common_pool))
    # 多样性高 = human-like → 反向：diversity 低 = AI-like
    signals["punct_diversity"] = {
        "score": 1.0 - min(diversity / 0.35, 1.0),  # 用到 <35% 池 → 偏 AI
        "hits": [f"使用标点种类={len(used)}"],
        "weight": WEIGHTS["punct_diversity"],
    }

    # —— 加权融合 ——
    likelihood = sum(s["score"] * s["weight"] for s in signals.values())

    if likelihood >= TIER_LIKELY:
        tier = "likely_ai"
    elif likelihood >= TIER_UNCERTAIN:
        tier = "uncertain"
    else:
        tier = "human"

    return {
        "ai_likelihood": round(likelihood, 3),
        "tier": tier,
        "signals": signals,
        "confidence": "low",
        "note": "仅供参考，非证据；中文事后检测误判率高，请勿据此处分学生",
        "text_chars": chars,
    }
