"""DEPTH 全文覆盖层（mock_api/depth_fulltext.py）单元测试 —— ADR-014 P9。

覆盖：
- split_chunks：分块边界、短文本、重叠。
- pick_verbatim_chunks：位置分散度、top_k 上限。
- summarize_chunks：并行、单块失败 fail-open（summary=''）。
- merge_summaries：确定性合并、单块直通。
- format_supplement：空/有上下文。
- is_enabled：默认关 + env 开启。
- build_fulltext_context：门控、文本过短跳过、LLM 全挂仍返回采样块、DB 缓存命中。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mock_api.depth_fulltext import (
    _text_hash,
    build_fulltext_context,
    format_supplement,
    is_enabled,
    merge_summaries,
    pick_verbatim_chunks,
    split_chunks,
    summarize_chunks,
)

# ── 确定性 LLM 桩 ─────────────────────────────────────────────────────


def _stub_llm(summary_map: dict[str, str] | None = None):
    """返回 (llm_func, call_log)。llm 对含 '片段' 的 prompt 返回映射值。"""

    def _llm(prompt: str):
        calls.append(prompt)
        if "片段" in prompt:
            for k, v in (summary_map or {}).items():
                if k in prompt:
                    return v
            return "（块摘要）"
        if "各分段摘要" in prompt:
            return "（全局摘要：方法→实验→结果）"
        return ""

    calls: list[str] = []
    return _llm, calls


# ── 分块 ───────────────────────────────────────────────────────────────


def test_split_chunks_basic() -> None:
    text = "。".join([f"这是第{i}个句子的内容描述。" for i in range(200)])
    chunks = split_chunks(text, chunk_size=300, overlap=0)
    assert len(chunks) > 1
    assert all(len(c) <= 300 for c in chunks)
    # 拼接后应还原全文（句子级切分无损）
    assert "".join(chunks) == text


def test_split_chunks_short_text_single() -> None:
    chunks = split_chunks("很短的一段文本。", chunk_size=1500)
    assert chunks == ["很短的一段文本。"]


def test_split_chunks_empty() -> None:
    assert split_chunks("") == []
    assert split_chunks(None) == []  # type: ignore[arg-type]


def test_split_chunks_long_sentence_hard_cut() -> None:
    long_sentence = "长" * 5000
    chunks = split_chunks(long_sentence, chunk_size=1000, overlap=100)
    assert len(chunks) >= 5
    assert all(len(c) <= 1000 for c in chunks)


# ── 采样增强 ───────────────────────────────────────────────────────────


def test_pick_verbatim_chunks_respects_top_k() -> None:
    # 注意：块文本不含数字，避免触发证据信号正则干扰“纯位置分散”断言
    chunks = [f"区块{chr(0x4e00 + i)}：这里是一般性描述内容。" for i in range(20)]
    picked = pick_verbatim_chunks(chunks, top_k=5)
    assert len(picked) == 5
    assert all(0 <= p["index"] < 20 for p in picked)
    # 位置分散：覆盖首尾与中段
    idxs = sorted(p["index"] for p in picked)
    assert idxs[0] <= 3 and idxs[-1] >= 16, f"采样应覆盖首尾: {idxs}"
    assert max(idxs) - min(idxs) >= 12, f"采样跨度不足: {idxs}"


def test_pick_verbatim_chunks_prefers_evidence_signal() -> None:
    chunks = ["这是一段没有数字的描述。" for _ in range(10)]
    chunks[5] = "实验结果表明 accuracy 达到 0.95，比基线提升 8%，见表 3 与图 2。"
    picked = pick_verbatim_chunks(chunks, top_k=3)
    # 证据信号强的块必须被选中
    assert 5 in [p["index"] for p in picked]


def test_pick_verbatim_chunks_short() -> None:
    chunks = ["a", "b", "c"]
    picked = pick_verbatim_chunks(chunks, top_k=10)
    assert len(picked) == 3


# ── 摘要（map/reduce）─────────────────────────────────────────────────


def test_summarize_chunks_parallel_fail_open() -> None:
    chunks = ["块A" * 50, "块B" * 50, "块C" * 50]
    llm, calls = _stub_llm({"块B": "（块B摘要）"})

    def _flaky(prompt: str):
        calls.append(prompt)
        if "块A" in prompt:
            raise RuntimeError("boom")
        return llm(prompt)

    out = summarize_chunks(chunks, _flaky, max_workers=3)
    assert len(out) == 3
    by = {o["index"]: o["summary"] for o in out}
    assert by[0] == ""  # 块A 失败 → fail-open 空摘要
    assert "块B摘要" in by[1]
    assert by[2]  # 其余块正常


def test_merge_summaries_single_passthrough() -> None:
    merged = merge_summaries([{"summary": "唯一摘要内容"}], lambda p: "不应被调用")
    assert merged == "唯一摘要内容"


def test_merge_summaries_multi() -> None:
    llm, _ = _stub_llm()
    summaries = [{"summary": f"块{i}摘要" + "字" * 100} for i in range(4)]
    merged = merge_summaries(summaries, llm, target_chars=300)
    assert merged  # 非空即视为成功（内容由桩返回）


def test_merge_summaries_all_empty() -> None:
    assert merge_summaries([{"summary": ""}, {"summary": ""}], lambda p: "") == ""


# ── 组装 ──────────────────────────────────────────────────────────────


def test_format_supplement_empty() -> None:
    assert format_supplement(None) == ""
    assert format_supplement({}) == ""


def test_format_supplement_with_ctx() -> None:
    ctx = {
        "global_summary": "本文提出一种新方法。",
        "verbatim_chunks": [{"index": 0, "text": "实验段落原文……", "signal": 3}],
    }
    block = format_supplement(ctx)
    assert "全局摘要" in block
    assert "实验段落原文" in block


# ── 开关与顶层入口 ─────────────────────────────────────────────────────


def test_is_enabled_default_false(monkeypatch: pytest.MonkeyPatch) -> None:
    # ⚠️ 不能只 delenv：pydantic-settings 还会从项目 .env 文件读取（本地
    # .env 可能开着 PAPERFORGE_DEPTH_FULLTEXT_ENABLED=1），必须显式 setenv=0
    # 压过 .env（环境变量优先级最高），保证任何环境都确定性通过。
    monkeypatch.setenv("PAPERFORGE_DEPTH_FULLTEXT_ENABLED", "0")
    monkeypatch.delenv("PAPERFORGE_DEPTH_FULLTEXT", raising=False)
    from mock_api.settings import reset_settings

    reset_settings()
    assert is_enabled() is False


def test_is_enabled_env_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERFORGE_DEPTH_FULLTEXT_ENABLED", "1")
    from mock_api.settings import reset_settings

    reset_settings()
    assert is_enabled() is True


def test_build_gated_off_no_llm_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    # 同 test_is_enabled_default_false：显式 setenv=0 压过 .env 文件，避免本地
    # .env 开着 FULLTEXT 时本测试误触发 LLM。
    monkeypatch.setenv("PAPERFORGE_DEPTH_FULLTEXT_ENABLED", "0")
    monkeypatch.delenv("PAPERFORGE_DEPTH_FULLTEXT", raising=False)
    from mock_api.settings import reset_settings

    reset_settings()

    def _boom(prompt: str):
        raise AssertionError("门控关闭时不应调用 LLM")

    assert build_fulltext_context("p1", "长文" * 20000, _boom) is None


def test_build_short_text_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPERFORGE_DEPTH_FULLTEXT_ENABLED", "1")
    from mock_api.settings import reset_settings

    reset_settings()
    llm, _ = _stub_llm()
    # 文本明显短于 max_chars_full*1.2 → 跳过（不调 LLM）
    assert build_fulltext_context("p1", "短文" * 100, llm) is None


def test_build_llm_all_fail_still_returns_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    """摘要全挂时仍返回采样块（检索增强 fail-open，不给空手而归）。"""
    monkeypatch.setenv("PAPERFORGE_DEPTH_FULLTEXT_ENABLED", "1")
    from mock_api.settings import reset_settings

    reset_settings()

    def _boom(prompt: str):
        raise RuntimeError("llm down")

    # 足够长（> 1.2×max_chars_full，测试环境 low_memory 预设 8000 字）
    text = "这是方法段，实验 accuracy 达到 0.95，见表 3。" * 2000  # ~48000 字
    ctx = build_fulltext_context("p1", text, _boom)
    assert ctx is not None
    assert ctx["global_summary"] == ""
    assert len(ctx["verbatim_chunks"]) >= 1


def test_build_caches_second_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """命中 DB 缓存时不再调 LLM。"""
    monkeypatch.setenv("PAPERFORGE_DEPTH_FULLTEXT_ENABLED", "1")
    from mock_api.settings import reset_settings

    reset_settings()

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    from mock_api.models import Base

    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = S()

    calls: list[str] = []
    llm, _ = _stub_llm()

    def _counting(prompt: str):
        calls.append(prompt)
        return llm(prompt)

    text = "实验表明 accuracy 0.9，结果优于基线。" * 400  # ~3600 字，超过 1.2×max_full(32000)? 不够
    # 用更长的文本确保触发构建
    long_text = "实验表明 accuracy 0.9，结果优于基线。" * 2000  # ~18000 字
    try:
        ctx1 = build_fulltext_context("p1", long_text, _counting, db=db)
        assert ctx1 is not None
        n_calls_first = len(calls)
        assert n_calls_first >= 1
        ctx2 = build_fulltext_context("p1", long_text, _counting, db=db)
        assert ctx2 is not None
        assert ctx2.get("cached") is True
        # 第二次不新增 LLM 调用
        assert len(calls) == n_calls_first
    finally:
        db.close()


def test_text_hash_stable() -> None:
    assert _text_hash("abc") == _text_hash("abc")
    assert _text_hash("abc") != _text_hash("abd")
