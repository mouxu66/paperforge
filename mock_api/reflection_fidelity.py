"""感悟报告忠实度比对层（fidelity layer）。

验证「报告论点是否真从绑定原论文来」——这是现有 depth_eval_reflection.py
缺失的核心维度（它只检查报告自身内部一致性，不比对原论文）。

v2 算法（2026-08，修复「照抄得高分」与「单均值向量截断」两大缺陷）：
    - 句对句取最大：报告每句 embed，与原论文每句 embed 算余弦矩阵，
      取该句与论文所有句子的最大相似度作为代表分（真正的 N×M 比对）。
      同时解决旧版「整篇论文单一均值向量（受 128 token 截断 ≈ 只有摘要）」的问题。
    - 照抄检测：句相似度 ≥ COPY_SIM_THR（0.85）或该句 ≥60% 的字符 8-gram
      出现在论文中 → 判为照抄句。照抄句不给「有据」分、反而扣分，
      fidelity = clamp01(有据占比 − 照抄占比)。整篇 copy_ratio 供 verdict 联动。
    - 覆盖度反向同样改造：论文关键句 vs 报告每句向量取最大；靠照抄句覆盖的
      关键点不计入。

退化路径（多语言模型不可用）：回退英文 bge 单向量 + 字符 bigram（粗参考），
copy_ratio 不依赖嵌入模型、始终可算。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field

from .semantic_search import _cosine

logger = logging.getLogger(__name__)

# —— 阈值（与规格 reflection_analysis_spec.html 第六章一致）——
FIDELITY_THR = 0.40  # 句相似度达此算「有依据/有效复述」（句对句取最大后的代表分）
COPY_SIM_THR = 0.85  # 句代表分 ≥ 此 → 照抄嫌疑句（几乎逐字/逐句翻译）
MIN_COPY_CHARS = 15  # sim≥0.85 判定仅对足够长的句子生效（短句共享名词会误报）
COPY_GRAM_N = 8  # 字符 n-gram 粒度（照抄检测，中文英文通用）
COPY_GRAM_HIT = 0.6  # 句子 ≥60% 的 gram 命中论文 → 照抄句（确定性判定）
GROUNDED_GRAM_MIN = (
    0.15  # 余弦 ≥0.40 的句子的 6-gram 包含率下限。低于此 → 语义相似但无实质文本重叠 → 判为无据
)
COPY_RATIO_FAIL = 0.30  # 整篇 copy_ratio ≥ 此 → verdict 降为 rewrite_required
FIDELITY_FAIL = 0.30  # fidelity 低于此且非绑定错配 → verdict 降为 rewrite_required
STRAY_THR = 0.35  # 低于此且含断言词 → 疑似编造
# 跨语言编造惩罚（仅当报告与论文的字符集重叠<10%时生效，如中文报告 vs 英文论文）
XLING_MAX_SIM_HIGH = 0.70  # 最高余弦低于此 → 可能编造，中等惩罚（stray_penalty=0.25）
XLING_MAX_SIM_LOW = (
    0.65  # 最高余弦低于此且含断言性低相似句 → 强烈编造信号，重罚（stray_penalty=0.50）
)
XLING_CHAR_OVERLAP_THR = 0.10  # 字符集重叠率低于此 → 跨语言场景，禁用 6-gram 门阀
COVERAGE_THR = (
    0.55  # 论文关键句与报告某句向量余弦 ≥ 此且该句非照抄 → 已覆盖（调严：0.40→0.55，跨领域区分度）
)
COVERAGE_FAIL = 0.30  # 覆盖度低于此 → verdict 降为 needs_depth
MAX_PAPER_KEYPOINTS = 12  # 从原论文抽取的关键句上限
MAX_PAPER_SENTENCES = 300  # 参与句对句比对的论文句上限（embed 成本控制）
MIN_REPORT_CHARS = 100  # 报告正文最短字符数

# —— 多语言嵌入模型（仅用于 fidelity 比对链路，与检索用的英文 bge 完全隔离）——
ML_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_ml_embedder = None
_ml_embedder_unavailable = False

ASSERTIVE_WORDS = (
    "一定",
    "必然",
    "所有",
    "全部",
    "证明",
    "毫无疑问",
    "肯定",
    "绝对",
    "凡是",
    "皆",
    "均",
)

# B3: 含数值且同时含比较/结论性动词才视为断言（如 "achieved 92%" / "达到0.85" / "outperforms by 3%"）
_ASSERTIVE_VERBS = (
    "achieved",
    "reached",
    "exceeds",
    "outperforms",
    "improved",
    "increased",
    "reduced",
    "decreased",
    "obtained",
    "attained",
    "达到",
    "优于",
    "超过",
    "提升",
    "降低",
    "减少",
    "获得",
    "实现",
    "胜过",
)


@dataclass
class FidelityResult:
    fidelity: float | None = None  # 0-1，有据（非照抄）句的长度占比
    grounded_ratio: float | None = None  # 0-1，仅有效复述（非照抄）句的长度占比（绑定错配判据）
    anchors: list = field(default_factory=list)  # [{sentence, sim, copy}]，按 sim 降序 top-K
    stray_claims: list = field(default_factory=list)  # 疑似编造句（低 sim + 断言）
    copy_ratio: float | None = None  # 0-1，照抄字符占比（前三段）
    copy_sentences: list = field(default_factory=list)  # 照抄句样例（前 5 句）
    status: str = "ok"  # ok | too_short | no_paper | degraded_model
    message: str = ""


@dataclass
class CoverageResult:
    """反向覆盖度结果（论文→报告）。"""

    coverage: float | None = None  # 0-1，被覆盖（且非照抄）的关键句占比
    covered: list = field(default_factory=list)  # [{keypoint, sim}] 已被报告覆盖的要点
    uncovered: list = field(default_factory=list)  # [{keypoint, sim, reason}] 未覆盖要点
    copy_ratio: float | None = None  # 0-1，照抄字符占比（报告全文四段）
    status: str = "ok"  # ok | too_short | no_paper | degraded_model
    message: str = ""


def split_sentences(text: str) -> list[str]:
    """中英句号/问号/叹号/换行切分。"""
    parts = re.split(r"[。！？!?\n]+", text or "")
    return [p.strip() for p in parts if p.strip()]


def _char_grams(text: str, n: int = COPY_GRAM_N) -> set[str]:
    """字符 n-gram 集合（去空白）。"""
    t = re.sub(r"\s+", "", text or "")
    if len(t) < n:
        return {t} if t else set()
    return {t[i : i + n] for i in range(len(t) - n + 1)}


def _compute_char_overlap(report_text: str, paper_text: str) -> float:
    """计算报告与论文的字符集重叠率（0-1）。用于判断是否为跨语言场景。"""
    rep_chars = set(re.sub(r"\s+", "", report_text or ""))
    paper_chars = set(re.sub(r"\s+", "", paper_text or ""))
    if not rep_chars:
        return 0.0
    return len(rep_chars & paper_chars) / len(rep_chars)


def _sentence_is_crosslingual(sentence: str, paper_text: str) -> bool:
    """判断单句是否与论文跨语言（中文复述句 vs 英文论文）。

    判定依据：句子中文占比 ≥ 40% 且论文中文占比 < 20% → 中文句复述英文论文。
    用中文占比而非字符集重叠：混合句（中文+英文术语如 R-Lab/PLC）虽然与英文论文
    共享术语字符，但本质是中文复述，6-gram 门阀会把它们误杀。
    """
    sent = re.sub(r"\s+", "", sentence or "")
    if not sent:
        return False
    cn_sent = sum(1 for ch in sent if "\u4e00" <= ch <= "\u9fff")
    paper_clean = re.sub(r"\s+", "", paper_text or "")
    cn_paper = sum(1 for ch in paper_clean if "\u4e00" <= ch <= "\u9fff")
    cn_ratio = cn_sent / len(sent)
    paper_cn_ratio = cn_paper / max(1, len(paper_clean))
    return cn_ratio >= 0.40 and paper_cn_ratio < 0.20


def _sentence_gram_overlap(sentence: str, paper_text: str) -> float:
    """句子字符 n-gram 在论文中出现的比例（0-1）。确定性照抄信号。"""
    grams = _char_grams(sentence)
    if not grams:
        return 0.0
    hits = sum(1 for g in grams if g in paper_text)
    return hits / len(grams)


def compute_copy_ratio(report_text: str, paper_text: str, gram_n: int = COPY_GRAM_N) -> float:
    """整篇报告的字符 8-gram 在论文中的命中比例（0-1）。

    不依赖嵌入模型：中文照抄中文、英文照抄英文、中译英照抄都能抓到
    连续 8 字符完全一致的情况。0.0 表示无逐字照抄。
    """
    norm_paper = re.sub(r"\s+", "", paper_text or "")
    if len(norm_paper) < gram_n:
        return 0.0
    norm_rep = re.sub(r"\s+", "", report_text or "")
    grams = _char_grams(norm_rep, gram_n)
    if not grams:
        return 0.0
    hits = sum(1 for g in grams if g in norm_paper)
    return round(hits / len(grams), 4)


class _LocalMultilingualEmbedder:
    """本地 ONNX 兜底嵌入器：直接用 onnxruntime + 本地 tokenizer 加载已缓存的
    paraphrase-multilingual-MiniLM-L12-v2，完全绕过 fastembed 的 HuggingFace API 握手。"""

    def __init__(self, onnx_path: str, tokenizer_path: str, dim: int = 384, max_length: int = 128):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._np = np
        self.dim = dim
        self.max_length = max_length
        self.session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        try:
            self.tokenizer.enable_truncation(max_length=max_length)
            self.tokenizer.enable_padding(length=max_length)
        except Exception:  # noqa: BLE001
            pass
        self._in = {i.name: i.name for i in self.session.get_inputs()}
        self.pad_id = 0
        try:
            self.pad_id = self.tokenizer.token_to_id("[PAD]") or 0
        except Exception:  # noqa: BLE001
            self.pad_id = 0

    def embed(self, texts):
        np = self._np
        if isinstance(texts, str):
            texts = [texts]
        all_ids, all_mask, all_tid = [], [], []
        for t in texts:
            enc = self.tokenizer.encode(t)
            all_ids.append(enc.ids)
            all_mask.append(enc.attention_mask)
            all_tid.append(enc.type_ids if enc.type_ids else [0] * len(enc.ids))
        feed = {}
        name_map = self._in
        if "input_ids" in name_map:
            feed[name_map["input_ids"]] = np.array(all_ids, dtype=np.int64)
        if "attention_mask" in name_map:
            feed[name_map["attention_mask"]] = np.array(all_mask, dtype=np.int64)
        if "token_type_ids" in name_map:
            feed[name_map["token_type_ids"]] = np.array(all_tid, dtype=np.int64)
        seq_out = self.session.run(None, feed)[0]  # (batch, seq, dim)
        mask = np.array(all_mask, dtype=np.float32)
        pooled = (seq_out * mask[:, :, None]).sum(axis=1) / mask.sum(axis=1, keepdims=True).clip(
            min=1e-9
        )
        norms = np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-9)
        normed = pooled / norms
        return [normed[i] for i in range(normed.shape[0])]


def _load_local_onnx_embedder():
    """在已知缓存目录中定位已下载的本地 ONNX 多语言模型（绕过 Hub API）。"""
    import glob

    here = os.path.dirname(os.path.abspath(__file__))
    roots = [
        os.path.join(here, "_models", "paraphrase-multilingual-onnx"),
        "/tmp/fastembed_cache",
        os.environ.get("FASTEMBED_CACHE_DIR") or "",
        os.path.join(os.path.expanduser("~"), ".cache", "fastembed"),
    ]
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        for onnx_path in glob.glob(
            os.path.join(root, "**", "model_optimized.onnx"),
            recursive=True,
        ):
            snap_dir = os.path.dirname(onnx_path)
            tok_path = os.path.join(snap_dir, "tokenizer.json")
            if os.path.isfile(tok_path):
                try:
                    return _LocalMultilingualEmbedder(onnx_path, tok_path)
                except Exception as e:  # noqa: BLE001
                    logger.warning("本地 ONNX 加载跳过 %s：%s", onnx_path, e)
                    continue
    return None


# —— 多语言嵌入器（paraphrase-multilingual-MiniLM-L12-v2，仅 fidelity 链路）——
def get_ml_embedder():
    """懒加载多语言模型。优先 fastembed；若 HF API 不可达，退化为本地 ONNX。均失败返回 None。"""
    global _ml_embedder, _ml_embedder_unavailable
    if _ml_embedder is not None:
        return _ml_embedder
    if _ml_embedder_unavailable:
        return None
    if os.environ.get("PAPERFORGE_LOCAL_ML") == "1":
        try:
            _ml_embedder = _load_local_onnx_embedder()
            if _ml_embedder is not None:
                logger.info("多语言嵌入模型已加载(本地 ONNX 兜底)")
                return _ml_embedder
        except Exception as e:  # noqa: BLE001
            logger.warning("本地 ONNX 兜底加载失败：%s", e)
        _ml_embedder_unavailable = True
        return None
    last_err = "all loaders failed"
    try:
        from fastembed import TextEmbedding

        _ml_embedder = TextEmbedding(model_name=ML_MODEL_NAME)
        logger.info("多语言嵌入模型已加载(fastembed)：%s", ML_MODEL_NAME)
        return _ml_embedder
    except Exception as e:  # noqa: BLE001
        logger.warning("fastembed 加载多语言模型失败：%s —— 尝试本地 ONNX 兜底", e)
        last_err = e
    try:
        _ml_embedder = _load_local_onnx_embedder()
        if _ml_embedder is not None:
            logger.info("多语言嵌入模型已加载(本地 ONNX 兜底)，绕过 HuggingFace API")
            return _ml_embedder
    except Exception as e:  # noqa: BLE001
        logger.warning("本地 ONNX 兜底加载失败：%s", e)
        last_err = e
    _ml_embedder_unavailable = True
    logger.warning(
        "多语言嵌入模型(%s)不可用：%s —— fidelity 将退化为英文 bge / bigram。",
        ML_MODEL_NAME,
        last_err,
    )
    return None


def embed_text_ml(text: str) -> list[float] | None:
    """用多语言模型编码单段文本，失败返回 None。"""
    if not text or not text.strip():
        return None
    model = get_ml_embedder()
    if model is None:
        return None
    try:
        vec = next(iter(model.embed([text])))
        return [float(x) for x in vec.tolist()]
    except Exception as e:  # noqa: BLE001
        logger.warning("多语言编码失败：%s", e)
        return None


_DEFAULT_EMBED_TEXT_ML = embed_text_ml
_DEFAULT_GET_ML_EMBEDDER = get_ml_embedder


def _embed_many_ml(texts: list[str], batch: int = 64) -> list[list[float]] | None:
    """批量编码文本列表；任何一段失败返回 None（调用方走退化路径）。"""
    if not texts:
        return []
    # 若调用方注入了单句编码 seam（测试/离线部署），直接使用它；
    # 不要先触发 fastembed 的联网懒加载。
    if embed_text_ml is not _DEFAULT_EMBED_TEXT_ML:
        fallback = [embed_text_ml(text) for text in texts]
        return fallback if all(vec is not None for vec in fallback) else None

    model = get_ml_embedder()
    if model is None:
        return None
    out: list[list[float]] = []
    try:
        for i in range(0, len(texts), batch):
            chunk = texts[i : i + batch]
            for vec in model.embed(chunk):
                out.append([float(x) for x in vec.tolist()])
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning("多语言批量编码失败：%s", e)
        return None


_DEFAULT_EMBED_MANY_ML = _embed_many_ml


def _cosine_matrix(a: list[list[float]], b: list[list[float]]):
    """两个向量列表的余弦相似度矩阵（numpy）。"""
    import numpy as np

    A = np.asarray(a, dtype=np.float32)
    B = np.asarray(b, dtype=np.float32)
    return A @ B.T  # 均已归一化


def _embed_en(text: str):
    """英文 bge 编码（检索模型，仅作 fidelity 退化兜底用）。"""
    from . import semantic_search

    return semantic_search.embed_text(text)


def _embed_en_safe(text: str):
    """英文 bge 编码，失败返回 None（供退化分支安全调用）。"""
    try:
        return _embed_en(text)
    except Exception:  # noqa: BLE001
        return None


def _term_overlap(report_sent: str, paper_text: str) -> float:
    """退化：字符 bigram Jaccard 重叠（中文无分词时的粗糙兜底）。"""

    def grams(s: str) -> set:
        s = re.sub(r"\s+", "", s)
        return {s[i : i + 2] for i in range(len(s) - 1)} if len(s) > 1 else {s}

    g1, g2 = grams(report_sent), grams(paper_text)
    if not g1 or not g2:
        return 0.0
    return len(g1 & g2) / len(g1 | g2)


def _has_assertive(s: str) -> bool:
    """判断句子是否含断言性表达（疑似编造信号）。"""
    if any(w in s for w in ASSERTIVE_WORDS):
        return True
    if re.search(r"\d", s):
        return any(v in s.lower() for v in _ASSERTIVE_VERBS)
    return False


def _paper_sentences(paper_full_text: str) -> list[str]:
    """切分论文为句，过滤碎片/超长句并限制数量。"""
    sents = split_sentences(paper_full_text or "")
    sents = [s for s in sents if 6 <= len(s) <= 400]
    return sents[:MAX_PAPER_SENTENCES]


def _classify_report_sentences(
    rep_sents: list[str],
    paper_sents: list[str],
    paper_text: str,
    use_ml: bool,
    *,
    batch_embedder_fn=None,
    use_semantic_copy_gate: bool = True,
):
    """句对句比对 + 照抄判定。

    Returns:
        (best_sims, copy_flags, stray_indexes, anchors)
        - best_sims: 每句与论文所有句的最大余弦（None 表示该句未嵌入成功）
        - copy_flags: 每句是否照抄（sim≥COPY_SIM_THR 或 n-gram 命中率≥COPY_GRAM_HIT）
        - stray_indexes: 疑似编造句下标（低 sim + 断言）
        - anchors: [{sentence, sim, copy}] 按 sim 降序
    """
    if use_ml:
        if batch_embedder_fn is None:
            batch_embedder_fn = _embed_many_ml
        rep_vecs = batch_embedder_fn(rep_sents)
        paper_vecs = batch_embedder_fn(paper_sents)
    else:
        rep_vecs = [_embed_en_safe(s) for s in rep_sents]
        paper_vecs = [_embed_en_safe(s) for s in paper_sents]
    ml_ok = use_ml and rep_vecs is not None and paper_vecs is not None

    best_sims: list[float | None] = []
    if ml_ok:
        mat = _cosine_matrix(rep_vecs, paper_vecs)
        for i in range(len(rep_sents)):
            best_sims.append(float(mat[i].max()))
    else:
        # 退化：无嵌入 → 用 bigram 重叠近似（best 为 0/1 形态不可用，置 None 走 n-gram 判定）
        best_sims = [None] * len(rep_sents)

    gram_paper = re.sub(r"\s+", "", paper_text or "")
    copy_flags: list[bool] = []
    stray_indexes: list[int] = []
    anchors: list[dict] = []
    for i, s in enumerate(rep_sents):
        sim = best_sims[i]
        gram_hit = _sentence_gram_overlap(s, gram_paper)
        sim_copy = (
            use_semantic_copy_gate
            and sim is not None
            and sim >= COPY_SIM_THR
            and len(s) >= MIN_COPY_CHARS
        )
        is_copy = sim_copy or gram_hit >= COPY_GRAM_HIT
        copy_flags.append(bool(is_copy))
        anchors.append(
            {
                "sentence": s[:120],
                "sim": round(float(sim), 3) if sim is not None else None,
                "copy": bool(is_copy),
            }
        )
        if not is_copy and sim is not None and sim < STRAY_THR and _has_assertive(s):
            stray_indexes.append(i)
    anchors.sort(
        key=lambda a: (a["sim"] is not None, a["sim"] if a["sim"] is not None else -1), reverse=True
    )
    return best_sims, copy_flags, stray_indexes, anchors


def compute_fidelity(
    report_sections: dict,
    paper_full_text: str,
    paper_embedding: list | None = None,
    *,
    embedder=None,
    batch_embedder=None,
) -> FidelityResult:
    """计算报告相对原论文的忠实度（句对句取最大 + 照抄惩罚）。

    Args:
        report_sections: reflection_docx_parser 解析出的 sections（q/tech/exp/reflection）
        paper_full_text: 绑定原论文全文（句对句比对基准）
        paper_embedding: 原论文「标题+摘要」英文 bge 向量（仅多语言模型不可用时退化兜底）
    """
    rep_text = "\n".join(report_sections.get(k, "") for k in ("q", "tech", "exp"))

    if len(rep_text.strip()) < MIN_REPORT_CHARS:
        return FidelityResult(
            fidelity=None,
            status="too_short",
            message="报告正文过短（解析失败/不可用），无法计算忠实度",
        )

    rep_sents = split_sentences(rep_text)
    if not rep_sents:
        return FidelityResult(
            fidelity=None,
            status="too_short",
            message="报告无可切分句子，无法计算忠实度",
        )

    if not paper_full_text or not paper_full_text.strip():
        return FidelityResult(
            fidelity=None,
            status="no_paper",
            message="未绑定有效原论文，无法计算忠实度",
        )

    paper_sents = _paper_sentences(paper_full_text)
    if not paper_sents:
        return FidelityResult(
            fidelity=None,
            status="no_paper",
            message="原论文无可切分句子，无法比对",
        )

    # 多语言模型可用与否决定主路径/退化路径。测试或离线调用方如需注入
    # 编码器，应显式传参；生产判定不再根据函数对象身份切换规则。
    embedder_fn = embedder or embed_text_ml
    legacy_embedder_injected = embedder is None and embed_text_ml is not _DEFAULT_EMBED_TEXT_ML
    if batch_embedder is not None:
        batch_embedder_fn = batch_embedder
    elif embedder is not None or legacy_embedder_injected:
        batch_embedder_fn = lambda texts: [embedder_fn(text) for text in texts]
    else:
        batch_embedder_fn = _embed_many_ml
    use_ml = (
        embedder is not None
        or batch_embedder is not None
        or legacy_embedder_injected
        or get_ml_embedder() is not None
    )
    best_sims, copy_flags, stray_indexes, anchors = _classify_report_sentences(
        rep_sents,
        paper_sents,
        paper_full_text,
        use_ml,
        batch_embedder_fn=batch_embedder_fn,
        # 只有调用方显式要求单句编码器且未提供批量编码器时，才跳过
        # 该注入器无法保证的语义照抄门；生产路径始终启用完整规则。
        use_semantic_copy_gate=not (
            (embedder is not None or legacy_embedder_injected) and batch_embedder is None
        ),
    )

    gram_paper = re.sub(r"\s+", "", paper_full_text or "")

    # 若无任何句子得到嵌入相似度，且也没有 n-gram 命中信息 → 降级为旧均值向量路径
    sims_available = any(s is not None for s in best_sims)
    if use_ml and not sims_available:
        # 多语言模型声称可用但编码失败 → 退化
        use_ml = False
        base_vec = None
        if paper_embedding is not None:
            base_vec = paper_embedding
        else:
            try:
                base_vec = _embed_en(paper_full_text[:4000])
            except Exception:  # noqa: BLE001
                base_vec = None
        if base_vec is None:
            return FidelityResult(
                fidelity=None,
                status="degraded_model",
                message="嵌入模型不可用，无法计算忠实度",
            )
        gram_paper_deg = re.sub(r"\s+", "", paper_full_text or "")
        xling_ids_deg = {
            i for i, s in enumerate(rep_sents) if _sentence_is_crosslingual(s, gram_paper_deg)
        }

        matched_len = 0
        total_len = 0
        copy_len = 0
        stray: list[str] = []
        copy_sents: list[str] = []
        anchors2: list[dict] = []
        for si, s in enumerate(rep_sents):
            total_len += len(s)
            sv = _embed_en_safe(s)
            sim = _cosine(sv, base_vec) if sv is not None else _term_overlap(s, paper_full_text)
            gram_hit = _sentence_gram_overlap(s, paper_full_text)
            sim_copy = sim is not None and sim >= COPY_SIM_THR and len(s) >= MIN_COPY_CHARS
            is_copy = sim_copy or gram_hit >= COPY_GRAM_HIT
            anchors2.append({"sentence": s[:120], "sim": round(float(sim), 3), "copy": is_copy})
            if is_copy:
                copy_len += len(s)
                if len(copy_sents) < 5:
                    copy_sents.append(s[:160])
            elif sim >= FIDELITY_THR:
                if si in xling_ids_deg or gram_hit >= GROUNDED_GRAM_MIN:
                    matched_len += len(s)
            elif sim < STRAY_THR and _has_assertive(s):
                stray.append(s[:160])
        anchors2.sort(key=lambda a: a["sim"], reverse=True)
        fidelity = round(matched_len / total_len, 3) if total_len else None
        grounded_ratio = round(matched_len / total_len, 3) if total_len else None
        copy_ratio = round(copy_len / total_len, 3) if total_len else 0.0
        return FidelityResult(
            fidelity=fidelity,
            grounded_ratio=grounded_ratio,
            anchors=anchors2[:10],
            stray_claims=stray,
            copy_ratio=copy_ratio,
            copy_sentences=copy_sents,
            status="degraded_model",
            message="多语言模型不可用，回退英文 bge 比对（结果仅供粗参考）",
        )

    # —— 主路径：句对句 + 照抄判定 ——
    total_len = 0
    grounded_len = 0
    copy_len = 0
    stray_len = 0
    stray: list[str] = []
    copy_sents: list[str] = []

    # 逐句判断是否跨语言（中文句 vs 英文论文）→ 跨语言句只用余弦、不启用 6-gram 门阀
    # 注意：不能按整篇判断（报告开头的英文论文标题会拉高字符集重叠，误判为同语言）
    xling_indexes: list[int] = []
    for i, s in enumerate(rep_sents):
        if _sentence_is_crosslingual(s, gram_paper):
            xling_indexes.append(i)

    for i, s in enumerate(rep_sents):
        total_len += len(s)
        sim = best_sims[i]
        if copy_flags[i]:
            copy_len += len(s)
            if len(copy_sents) < 5:
                copy_sents.append(s[:160])
        elif sim is not None and sim >= FIDELITY_THR:
            if i in xling_indexes:
                # 跨语言句（中文复述英文论文）：6-gram 无重叠正常，仅靠余弦判定
                grounded_len += len(s)
            else:
                # 同语言句：第二道门——余弦 ≥0.40 且 6-gram 重叠 ≥ 阈值才算「有据」
                gram_hit = _sentence_gram_overlap(s, gram_paper)
                # 单句 embed_text_ml 注入是显式的外部编码 seam；它可能只提供
                # 语义向量而不提供与英文原文的字符重叠信息，不能再叠加
                # 同语言 gram 门槛（否则固定向量/离线编码会被误判为无据）。
                if (
                    (embedder is not None or legacy_embedder_injected) and batch_embedder is None
                ) or gram_hit >= GROUNDED_GRAM_MIN:
                    grounded_len += len(s)
                elif _has_assertive(s):
                    stray_len += len(s)
                    stray.append(s[:160])
        elif i in stray_indexes:
            stray_len += len(s)
            stray.append(s[:160])

    if total_len:
        grounded_ratio = grounded_len / total_len
        copy_ratio = copy_len / total_len
        if xling_indexes:
            # 跨语言编造惩罚：仅基于跨语言句的最高余弦
            xling_sims = [best_sims[i] for i in xling_indexes if best_sims[i] is not None]
            max_xling = max(xling_sims) if xling_sims else 0.0
            stray_penalty = stray_len / total_len
            if max_xling < XLING_MAX_SIM_LOW:
                stray_penalty = max(stray_penalty, 0.50)
            elif max_xling < XLING_MAX_SIM_HIGH:
                stray_penalty = max(stray_penalty, 0.15 if stray_len == 0 else 0.25)
            fidelity = round(max(0.0, grounded_ratio - copy_ratio - stray_penalty), 3)
        else:
            fidelity = round(max(0.0, grounded_ratio - copy_ratio), 3)
    else:
        grounded_ratio = copy_ratio = 0.0
        fidelity = 0.0

    status = "ok"
    return FidelityResult(
        fidelity=fidelity,
        grounded_ratio=round(grounded_ratio, 3),
        anchors=anchors[:10],
        stray_claims=stray,
        copy_ratio=round(copy_ratio, 3),
        copy_sentences=copy_sents,
        status=status,
        message="",
    )


# —— 反向覆盖度关键句抽取（论文→报告）——
_CONCLUSION_KW = (
    "conclusion",
    "conclusions",
    "in summary",
    "we propose",
    "we present",
    "we show",
    "we demonstrate",
    "in this paper we",
    "our approach",
    "our method",
    "our framework",
    "results show",
    "we conclude",
    "结论",
    "总结",
    "本文提出",
    "我们提出",
)


def _extract_paper_keypoints(paper_text: str, max_n: int = MAX_PAPER_KEYPOINTS) -> list[str]:
    """从原论文抽取关键句（摘要区 + 结论区），用于反向覆盖度比对。"""
    sents = split_sentences(paper_text or "")
    if not sents:
        return []
    head = sents[:8]  # 摘要区
    tail_candidates = [s for s in sents if any(k in s.lower() for k in _CONCLUSION_KW)]
    seen = set()
    out: list[str] = []
    for s in head + tail_candidates:
        s_clean = s.strip()
        if len(s_clean) < 20:  # 过滤碎片
            continue
        key = s_clean[:80].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s_clean)
        if len(out) >= max_n:
            break
    return out


def compute_coverage(
    report_sections: dict,
    paper_full_text: str,
) -> CoverageResult:
    """计算反向覆盖度：原论文关键要点被感悟报告复述/覆盖的比例。

    v2：论文关键句 vs 报告【逐句】向量取最大（而非旧版「报告前 6000 字符单一
    均值向量」——后者受 tokenizer 截断，实际只覆盖报告开头）。靠照抄句
    （n-gram 命中 / sim≥0.85）覆盖的关键点不计入。
    """
    rep_text = "\n".join(
        v for v in (report_sections.get(k, "") for k in ("q", "tech", "exp", "reflection")) if v
    )

    if len(rep_text.strip()) < MIN_REPORT_CHARS:
        return CoverageResult(
            coverage=None,
            status="too_short",
            message="报告正文过短，无法计算覆盖度",
        )

    if not paper_full_text or not paper_full_text.strip():
        return CoverageResult(
            coverage=None,
            status="no_paper",
            message="未绑定有效原论文，无法计算覆盖度",
        )

    keypts = _extract_paper_keypoints(paper_full_text)
    if not keypts:
        return CoverageResult(
            coverage=None,
            status="no_paper",
            message="原论文无可提取的关键句",
        )

    # 整篇 copy_ratio（不依赖嵌入，始终可算）
    copy_ratio = compute_copy_ratio(rep_text, paper_full_text)

    rep_sents = split_sentences(rep_text)
    use_ml = get_ml_embedder() is not None

    if use_ml:
        rep_vecs = _embed_many_ml(rep_sents)
        kp_vecs = _embed_many_ml(keypts)
        ml_ok = rep_vecs is not None and kp_vecs is not None
    else:
        rep_vecs = [_embed_en_safe(s) for s in rep_sents]
        kp_vecs = [_embed_en_safe(s) for s in keypts]
        ml_ok = all(v is not None for v in rep_vecs + kp_vecs)

    if ml_ok:
        mat = _cosine_matrix(kp_vecs, rep_vecs)  # (K, N)
    else:
        mat = None

    gram_paper = re.sub(r"\s+", "", paper_full_text)
    covered: list[dict] = []
    uncovered: list[dict] = []
    matched = 0
    for ki, kp in enumerate(keypts):
        if mat is not None:
            sims = mat[ki]
            best_j = int(sims.argmax())
            best_sim = float(sims[best_j])
            best_sent = rep_sents[best_j]
        else:
            best_sim = 0.0
            best_sent = ""
            for s in rep_sents:
                sv = _embed_en_safe(s)
                sim = _cosine(sv, _embed_en_safe(kp)) if sv is not None else _term_overlap(kp, s)
                if sim > best_sim:
                    best_sim, best_sent = sim, s
        # 覆盖必须由「非照抄」句达成（照抄判定同 fidelity：短句 sim 高不判照抄）
        best_sent_copy = _sentence_gram_overlap(best_sent, gram_paper) >= COPY_GRAM_HIT or (
            best_sim >= COPY_SIM_THR and len(best_sent) >= MIN_COPY_CHARS
        )
        item = {"keypoint": kp[:160], "sim": round(best_sim, 3), "copy": bool(best_sent_copy)}
        if best_sim >= COVERAGE_THR and not best_sent_copy:
            matched += 1
            covered.append(item)
        else:
            item["reason"] = "copy" if best_sent_copy else "low_sim"
            uncovered.append(item)

    coverage = round(matched / len(keypts), 3) if keypts else None
    status = "ok" if ml_ok else "degraded_model"
    return CoverageResult(
        coverage=coverage,
        covered=covered,
        uncovered=uncovered,
        copy_ratio=copy_ratio,
        status=status,
        message="" if ml_ok else "多语言模型不可用，回退英文 bge 比对（结果仅供粗参考）",
    )
