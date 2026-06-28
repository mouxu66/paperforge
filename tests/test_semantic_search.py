"""测试 mock_api/semantic_search.py（向量检索原语）+ mock_api/crud.rrf_fuse（RRF 融合）。

覆盖场景：
- cosine_similarity：相同向量=1、正交向量=0、不同长度=0、空向量=0、numpy 与纯 Python 一致
- serialize_vector / deserialize_vector：往返转换保持数值
- embed_text：fastembed 不可用时返回 None、空文本返回 None
- embed_batch：空列表返回 []、不可用时返回 None
- rrf_fuse：两路融合、单路贡献、去重、top_n 截断、k 参数影响

外部依赖（fastembed）通过 patch get_embedder 模拟，避免下载模型。
"""
from __future__ import annotations

import math
from unittest.mock import patch

import pytest

from mock_api import semantic_search as ss
from mock_api.crud import rrf_fuse


# ===========================================================================
# cosine_similarity
# ===========================================================================
def test_cosine_similarity_identical_vectors_is_one():
    """相同向量 → 余弦相似度 = 1.0。"""
    v = [1.0, 2.0, 3.0]
    assert ss.cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero():
    """正交向量 → 余弦相似度 = 0.0。"""
    assert ss.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors_is_negative_one():
    """反向向量 → 余弦相似度 = -1.0。"""
    assert ss.cosine_similarity([1.0, 2.0], [-1.0, -2.0]) == pytest.approx(-1.0)


def test_cosine_similarity_mismatched_length_is_zero():
    """长度不一致 → 返回 0.0（防御性）。"""
    assert ss.cosine_similarity([1.0, 2.0], [1.0]) == 0.0


def test_cosine_similarity_empty_vectors_is_zero():
    """空向量 → 返回 0.0。"""
    assert ss.cosine_similarity([], []) == 0.0


def test_cosine_similarity_zero_vector_is_zero():
    """零向量（范数为 0）→ 返回 0.0，避免除零。"""
    assert ss.cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_cosine_similarity_pure_python_fallback_matches_numpy():
    """numpy 不可用时降级纯 Python，结果应与公式一致。"""
    # 强制走 ImportError 分支（patch import numpy 抛错）
    import builtins

    real_import = builtins.__import__

    def _no_numpy(name, *args, **kwargs):
        if name == "numpy":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    a, b = [1.0, 2.0, 3.0], [4.0, 5.0, 6.0]
    expected = (
        sum(x * y for x, y in zip(a, b))
        / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(x * x for x in b)))
    )
    with patch("builtins.__import__", side_effect=_no_numpy):
        result = ss.cosine_similarity(a, b)
    assert result == pytest.approx(expected)


# ===========================================================================
# serialize_vector / deserialize_vector
# ===========================================================================
def test_serialize_deserialize_roundtrip():
    """序列化 → 反序列化往返保持数值。"""
    vec = [0.1, -0.2, 0.3, 0.0, 1.5]
    s = ss.serialize_vector(vec)
    assert isinstance(s, str)
    restored = ss.deserialize_vector(s)
    assert restored == pytest.approx(vec)


def test_deserialize_vector_invalid_returns_empty():
    """非法 JSON 字符串 → 返回空列表（不抛异常）。"""
    assert ss.deserialize_vector("not json") == []
    assert ss.deserialize_vector("") == []
    assert ss.deserialize_vector("null") == []


def test_serialize_vector_ensure_ascii_false_for_floats():
    """序列化结果为合法 JSON（浮点数不被转义）。"""
    s = ss.serialize_vector([1.0, 2.0])
    assert "1.0" in s and "2.0" in s


# ===========================================================================
# embed_text / embed_batch（fastembed 不可用时降级）
# ===========================================================================
def test_embed_text_returns_none_when_unavailable():
    """fastembed 不可用 → embed_text 返回 None。"""
    with patch.object(ss, "get_embedder", return_value=None):
        assert ss.embed_text("hello") is None


def test_embed_text_empty_string_returns_none():
    """空文本 → 返回 None（不触发模型加载）。"""
    with patch.object(ss, "get_embedder", return_value=None):
        assert ss.embed_text("") is None
        assert ss.embed_text("   ") is None


def test_embed_text_success():
    """fastembed 可用 → 返回浮点列表。"""
    fake_model = MagicMock_embedder([[0.1, 0.2, 0.3]])
    with patch.object(ss, "get_embedder", return_value=fake_model):
        vec = ss.embed_text("machine learning")
    assert vec == [0.1, 0.2, 0.3]


def test_embed_batch_empty_list_returns_empty():
    """空列表 → 返回 []（不调用模型）。"""
    assert ss.embed_batch([]) == []


def test_embed_batch_unavailable_returns_none():
    """fastembed 不可用 → embed_batch 返回 None。"""
    with patch.object(ss, "get_embedder", return_value=None):
        assert ss.embed_batch(["a", "b"]) is None


def test_embed_batch_success():
    """批量编码 → 返回向量列表。"""
    fake_model = MagicMock_embedder([[0.1, 0.2], [0.3, 0.4]])
    with patch.object(ss, "get_embedder", return_value=fake_model):
        result = ss.embed_batch(["text1", "text2"])
    assert result == [[0.1, 0.2], [0.3, 0.4]]


class MagicMock_embedder:
    """模拟 fastembed TextEmbedding：embed() 返回可迭代的类列表对象。"""

    def __init__(self, vectors: list[list[float]]):
        self._vectors = vectors

    def embed(self, texts):
        # fastembed.embed 返回生成器，元素为带 tolist() 的类 numpy 对象
        for vec in self._vectors:

            class _V:
                def tolist(self_inner):
                    return vec

            yield _V()


# ===========================================================================
# rrf_fuse（倒数排名融合）
# ===========================================================================
def test_rrf_fuse_empty_inputs_returns_empty():
    """两路均为空 → 返回空列表。"""
    assert rrf_fuse([], []) == []


def test_rrf_fuse_single_source_only_fts():
    """仅 FTS5 有结果：单路贡献，按 rank 升序。"""
    fts = ["p1", "p2", "p3"]
    result = rrf_fuse(fts, [])
    ids = [pid for pid, _ in result]
    assert ids == ["p1", "p2", "p3"]
    # 分数 = 1/(60+rank)
    assert result[0][1] == pytest.approx(1.0 / 61)


def test_rrf_fuse_single_source_only_vec():
    """仅向量有结果：单路贡献。"""
    result = rrf_fuse([], ["v1", "v2"])
    assert [pid for pid, _ in result] == ["v1", "v2"]


def test_rrf_fuse_both_sources_overlap_top_ranked():
    """两路结果重叠：重叠文档分数累加，排名更高。"""
    # p1 在 FTS 排第1、向量排第1 → 累加分数最高
    fts = ["p1", "p2"]
    vec = ["p1", "p3"]
    result = rrf_fuse(fts, vec)
    assert result[0][0] == "p1"
    # p1 分数 = 1/61 + 1/61 = 2/61
    assert result[0][1] == pytest.approx(2.0 / 61.0)


def test_rrf_fuse_deduplication():
    """同一文档在两路出现：仅出现一次（去重）。"""
    fts = ["p1", "p2"]
    vec = ["p1", "p2"]
    result = rrf_fuse(fts, vec)
    ids = [pid for pid, _ in result]
    assert len(ids) == len(set(ids))  # 无重复
    assert set(ids) == {"p1", "p2"}


def test_rrf_fuse_top_n_limit():
    """top_n 截断：返回不超过 top_n 条。"""
    fts = [f"p{i}" for i in range(20)]
    vec = [f"p{i}" for i in range(20)]
    result = rrf_fuse(fts, vec, top_n=5)
    assert len(result) == 5


def test_rrf_fuse_k_parameter_affects_score():
    """k 参数影响分数绝对值（k 越大分数越小）。"""
    fts = ["p1"]
    small_k = rrf_fuse(fts, [], k=1)
    large_k = rrf_fuse(fts, [], k=100)
    assert small_k[0][1] > large_k[0][1]


def test_rrf_fuse_scores_descending():
    """返回结果按 RRF 分数降序排列。"""
    fts = ["a", "b", "c", "d"]
    vec = ["c", "b", "a", "e"]
    result = rrf_fuse(fts, vec)
    scores = [score for _, score in result]
    assert scores == sorted(scores, reverse=True)


def test_rrf_fuse_default_k_is_60():
    """默认 k=60（项目约定的平滑常数）。"""
    result = rrf_fuse(["p1"], [])
    assert result[0][1] == pytest.approx(1.0 / 61.0)
