"""Ornith 思考模式改造 P0 单测：四形态解析管线 + §0 超时链自检。

覆盖：
- S1 分离成功 / S2 分离失败（内联 <think>）/ S3 误路由（无闭合 CoT）/ S4 grammar 冲突
- 实测泄漏样本（Ornith 真实故障 B）
- hash 变体 </think:abcd1234> 兼容
- §0 自检：默认配置通过 / reasoning on + 旧超时链 raise / reasoning on + 三件套抬升通过
- ChatResult.reasoning 审计字段
"""

import os
import types

import pytest

import mock_api.llm.openai_provider as op
from mock_api.llm.base import ChatResult
from mock_api.llm.openai_provider import (
    _extract_final_answer,
    _find_last_json_object,
    _split_think,
    _validate_llm_runtime,
)


# ───────────────────────── S1：分离成功 ─────────────────────────
def test_s1_separated():
    c, r = _extract_final_answer('{"has_substance": true}', "<think>let me think</think>")
    assert '"has_substance"' in c
    assert "let me think" in r


# ───────────────────────── S2：分离失败，内联 <think> ─────────────────────────
def test_s2_leak_inline():
    content = (
        "<think>The user wants me to find 2-3 key reasons why this abstract is weak."
        "</think>"
        '{"evidence_id":"E5","critique_point":"x","severity":"major"}'
    )
    c, r = _extract_final_answer(content, "")
    assert "evidence_id" in c
    assert "user wants me to find" in r


# ───────────────────────── S3：误路由，纯 CoT 无闭合 ─────────────────────────
def test_s3_pure_cot_no_close():
    content = "<think>First I should examine the methodology. Then check the evidence."
    c, r = _extract_final_answer(content, '{"has_substance": false}')
    assert '"has_substance"' in c
    assert "methodology" in r


# ───────────────────────── S4：grammar 冲突，content 空 ─────────────────────────
def test_s4_grammar_conflict():
    c, r = _extract_final_answer("", '{"reasoning":"...","expectation":0.3}')
    assert "expectation" in c


# ───────────────────────── 实测泄漏样本（Ornith 真实故障 B） ─────────────────────────
def test_real_leak_sample():
    content = (
        "<think>"
        "The user wants me to find 2-3 key reasons why this paper's contribution is unclear."
        "</think>"
        '{"critique_point":"unclearest contribution","evidence_id":"E3","severity":"minor"}'
    )
    c, r = _extract_final_answer(content, "")
    assert "critique_point" in c and "evidence_id" in c
    assert "user wants me to find" in r


# ───────────────────────── hash 变体兼容 ─────────────────────────
def test_hash_variant_split():
    think, ans = _split_think("<think:abcd1234> some thoughts</think:6124c78e>:abcd1234 final answer")
    assert think == "some thoughts"
    assert ans == "final answer"


def test_hash_variant_in_answer():
    content = "<think:abcd1234>CoT</think:abcd1234>{\"ok\":1}"
    c, r = _extract_final_answer(content, "")
    assert '"ok"' in c


def test_hash_variant_real_leak():
    content = (
        "<think:6124c78e>"
        "The user wants me to find 2-3 key reasons why this method is invalid."
        "</think:6124c78e>"
        '{"critique_point":"invalid baseline","evidence_id":"E2","severity":"major"}'
    )
    c, r = _extract_final_answer(content, "")
    assert "critique_point" in c and "evidence_id" in c
    assert "user wants me to find" in r


# ───────────────────────── _find_last_json_object 兜底 ─────────────────────────
def test_find_last_json_object():
    src = "blah {\"a\":1} middle {\"b\":2,\"nested\":{\"c\":3}} tail"
    obj = _find_last_json_object(src)
    assert obj is not None
    assert '"b"' in obj and '"nested"' in obj


def test_find_last_json_object_none():
    assert _find_last_json_object("no json here") is None


# ───────────────────────── §0 自检：默认配置通过 ─────────────────────────
def test_selfcheck_default_passes(monkeypatch):
    monkeypatch.setenv("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "120")
    monkeypatch.setenv("PAPERFORGE_LLM_REQUEST_TIMEOUT", "120")
    monkeypatch.setenv("PAPERFORGE_LLAMA_SERVER_REASONING", "off")
    monkeypatch.setattr(op, "_LOCAL_TIMEOUT_CAP", 100.0)
    _validate_llm_runtime()  # 不应抛异常


# ───────────────────────── §0 自检：reasoning on + 旧超时链 raise ─────────────────────────
def test_selfcheck_reasoning_on_old_chain_raises(monkeypatch):
    # requested=120, cap=100, watchdog=120 → effective=min(120,100,110)=100 < 200
    monkeypatch.setenv("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "120")
    monkeypatch.setenv("PAPERFORGE_LLM_REQUEST_TIMEOUT", "120")
    monkeypatch.setenv("PAPERFORGE_LLAMA_SERVER_REASONING", "on")
    monkeypatch.setattr(op, "_LOCAL_TIMEOUT_CAP", 100.0)
    with pytest.raises(RuntimeError):
        _validate_llm_runtime()


# ───────────────────────── §0 自检：reasoning on + 三件套抬升通过 ─────────────────────────
def test_selfcheck_reasoning_on_three_levers_pass(monkeypatch):
    # requested=280, cap=280, watchdog=300 → effective=min(280,280,290)=280 >= 200
    monkeypatch.setenv("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "300")
    monkeypatch.setenv("PAPERFORGE_LLM_REQUEST_TIMEOUT", "280")
    monkeypatch.setenv("PAPERFORGE_LLAMA_SERVER_REASONING", "on")
    monkeypatch.setattr(op, "_LOCAL_TIMEOUT_CAP", 280.0)
    _validate_llm_runtime()  # 不应抛异常


# ───────────────────────── §0 自检：硬不变式破坏（cap+margin>=watchdog）raise ─────────────────────────
def test_selfcheck_hard_invariant_broken_raises(monkeypatch):
    monkeypatch.setenv("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "100")
    monkeypatch.setenv("PAPERFORGE_LLM_REQUEST_TIMEOUT", "120")
    monkeypatch.setenv("PAPERFORGE_LLAMA_SERVER_REASONING", "off")
    monkeypatch.setattr(op, "_LOCAL_TIMEOUT_CAP", 100.0)  # 100+10=110 >= 100
    with pytest.raises(RuntimeError):
        _validate_llm_runtime()


# ───────────────────────── ChatResult.reasoning 字段 ─────────────────────────
def test_chatresult_reasoning_field():
    cr = ChatResult(content="ans", model="m", provider="p", reasoning="think")
    assert cr.reasoning == "think"
    cr2 = ChatResult(content="ans", model="m", provider="p")
    assert cr2.reasoning == ""


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
