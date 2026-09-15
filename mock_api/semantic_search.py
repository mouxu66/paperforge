"""语义搜索模块 —— 基于 fastembed 的向量检索（混合检索 + RRF 融合）。

设计要点：
1. 向量模型：BAAI/bge-small-en-v1.5（384 维，纯 CPU 推理，无 PyTorch 依赖）。
   通过 fastembed 库加载（基于 ONNX Runtime），首次使用时自动下载到
   ~/.cache/fastembed/。桌面版打包不含模型文件，首次联网下载；
   未联网或下载失败时自动降级为纯 FTS5 关键词检索。
2. 懒加载：fastembed 作为可选依赖，未安装时 get_embedder() 返回 None，
   调用方自动降级为 FTS5 检索。首次加载失败后标记不可用，避免重复尝试。
3. 向量存储：PaperEmbedding 表用 JSON 字符串存储向量（避免 numpy 二进制列）。
4. 相似度：余弦相似度，使用 numpy 批量计算（论文数 < 1000 时性能足够）。
5. 混合检索：FTS5（Top 20）+ 向量（Top 20），RRF（k=60）融合，返回 Top 10。
   详见 crud.hybrid_search_papers / crud.rrf_fuse。
"""

from __future__ import annotations

import json
import logging
import math

logger = logging.getLogger(__name__)

# 向量模型：BAAI/bge-small-en-v1.5（384 维，英文语义检索效果好，体积小 ~130MB）
MODEL_NAME = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384

# 全局模型缓存（fastembed 内部线程安全；GIL 保证赋值原子性）
_embedder = None
_embedder_unavailable = False  # 标记是否已尝试加载且失败，避免重复联网尝试


def get_embedder():
    """懒加载 fastembed 模型。

    返回 TextEmbedding 实例；未安装 fastembed / 模型下载失败时返回 None。
    首次失败后标记 `_embedder_unavailable=True`，后续调用直接返回 None，
    避免每次语义搜索都触发网络请求（模型下载）。
    """
    global _embedder, _embedder_unavailable
    if _embedder is not None:
        return _embedder
    if _embedder_unavailable:
        return None
    try:
        # 延迟导入：仅在实际需要语义搜索时才加载 fastembed + onnxruntime
        from fastembed import TextEmbedding

        _embedder = TextEmbedding(model_name=MODEL_NAME)
        logger.info("fastembed 模型已加载：%s", MODEL_NAME)
        return _embedder
    except Exception as e:  # noqa: BLE001
        # ImportError（未安装）/ OSError/ConnectTimeout（模型下载失败）/ 其他
        _embedder_unavailable = True
        logger.warning(
            "fastembed 不可用（%s），语义搜索将降级为 FTS5 关键词检索。"
            "如需启用：pip install fastembed>=0.2.0 并联网首次下载模型。",
            e,
        )
        return None


def is_available() -> bool:
    """检查向量检索是否可用（fastembed 已安装且模型可加载）。"""
    return get_embedder() is not None


def embed_text(text: str) -> list[float] | None:
    """将文本编码为 384 维向量。失败返回 None。"""
    if not text or not text.strip():
        return None
    model = get_embedder()
    if model is None:
        return None
    try:
        # fastembed.embed 返回生成器，取首条；向量已 L2 归一化
        vec = next(iter(model.embed([text])))
        return [float(x) for x in vec.tolist()]
    except Exception as e:  # noqa: BLE001
        logger.warning("向量编码失败：%s", e)
        return None


def embed_batch(texts: list[str]) -> list[list[float]] | None:
    """批量编码文本为向量（比逐条调用快得多）。失败返回 None。

    fastembed 的 embed 接受可迭代字符串，返回生成器，批量推理效率更高。
    """
    if not texts:
        return []
    model = get_embedder()
    if model is None:
        return None
    try:
        # 批量编码；bge-small 输出已归一化（余弦相似度=点积）
        return [[float(x) for x in vec.tolist()] for vec in model.embed(texts)]
    except Exception as e:  # noqa: BLE001
        logger.warning("批量向量编码失败：%s", e)
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度。

    若向量已归一化（embed_text/embed_batch 默认归一化），等价于点积；
    这里仍按通用余弦公式计算，兼容未归一化的输入。
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    # 优先用 numpy（更快），失败时降级为纯 Python
    try:
        import numpy as np

        va = np.asarray(a, dtype=np.float32)
        vb = np.asarray(b, dtype=np.float32)
        na = float(np.linalg.norm(va))
        nb = float(np.linalg.norm(vb))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))
    except ImportError:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


def _cosine(a: list[float], b: list[float]) -> float:
    """内部便捷别名：等价于 ``cosine_similarity``。

    从 ``mock_api.crud._shared`` 迁移至此，供需要余弦相似度但不想引入
    ``crud`` 包的模块使用。
    """
    return cosine_similarity(a, b)


def serialize_vector(vec: list[float]) -> str:
    """向量 → JSON 字符串（用于存储到 PaperEmbedding.embedding 列）。"""
    return json.dumps(vec, ensure_ascii=False)


def deserialize_vector(s: str) -> list[float]:
    """JSON 字符串 → 向量（从 DB 读取时调用）。"""
    try:
        return [float(x) for x in json.loads(s)]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
