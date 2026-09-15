"""报告条件化证据检索（reflection_evidence）单测。

不依赖 DB / LLM / 真实嵌入模型：
- 嵌入用可控向量 fake（受控余弦 + crc32 伪随机正交兜底）；
- 流水线集成测试复用 test_reflection_rigor 的 mock 模式。

覆盖：
1. 开关门控（默认关 / =1 开）；
2. build_evidence_pack 边界（无论文 / 无报告 / 无嵌入器）；
3. 选证据策略：低相似断言句优先、照抄句跳过、预算裁剪；
4. 流水线集成：paper_supplement 注入 + evidence_retrieval 诊断透传 + 默认关闭零行为变化。
"""

from __future__ import annotations

import zlib
from unittest.mock import Mock

import pytest
from mock_api.reflection_evidence import (
    PACK_HEADER,
    build_evidence_pack,
    evidence_enabled,
)

DIM = 4096  # 高维正交基：不同桶的句子余弦恒为 0，杜绝伪随机向量喧宾夺主


def _normalize(vec: list[float]) -> list[float]:
    n = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / n for x in vec]


def _unit(k: int) -> list[float]:
    v = [0.0] * DIM
    v[k % DIM] = 1.0
    return v


def _auto_vec(text: str) -> list[float]:
    """未知句子的确定性正交基向量（与其他所有句子余弦恰为 0）。"""
    return _unit(zlib.crc32(text.encode("utf-8")))


def make_embedder(vec_map: dict[str, list[float]]):
    """按关键词匹配受控向量；未命中走伪随机正交兜底。

    键与文本都去掉尾部标点再匹配：split_sentences 切出的句子不带结尾句号，
    带句号的键会永远匹配不上（这是本文件第一版踩过的坑）。
    """
    def _norm(s: str) -> str:
        return s.rstrip("。！？!?.\n ")

    def _embed(texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            vec = next((v for key, v in vec_map.items() if _norm(key) in t), None)
            out.append(_normalize(vec if vec is not None else _auto_vec(t)))
        return out

    return _embed


# 受控向量：anchor 论文句 = e1；anchor 报告句 = e1 + 0.9*e2 → 余弦 ≈ 0.74
# （介于 COVERAGE_THR 与 COPY_SIM_THR 之间，非照抄锚点）。
_V_PAPER_ANCHOR = _unit(1)
_V_REPORT_ANCHOR = _normalize([1.0, 0.9] + [0.0] * (DIM - 2))
_V_STRAY = _unit(3)  # 与论文所有受控向量正交 → 模拟「论文里找不到」的编造断言

_ANCHOR_PAPER_SENT = "该方法采用图神经网络进行路径规划并在标准数据集上取得最优成绩。"
_ANCHOR_REPORT_SENT = "论文的核心方法是利用图神经网络完成路径规划，效果优于对比方法。"
_STRAY_CLAIM = "实验中该模型准确率达到99%，远超所有基线方法和人类专家水平。"

_PAPER_TEXT = (
    "本文研究多机器人路径规划问题。"
    + _ANCHOR_PAPER_SENT
    + "实验在三个公开数据集上验证了方法的有效性。"
)


# ── 开关门控 ─────────────────────────────────────────────────────────────────


class TestGate:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("PAPERFORGE_REFLECTION_EVIDENCE", raising=False)
        assert evidence_enabled() is False

    def test_enabled_with_env(self, monkeypatch):
        monkeypatch.setenv("PAPERFORGE_REFLECTION_EVIDENCE", "1")
        assert evidence_enabled() is True

    @pytest.mark.parametrize("val", ["0", "true", "on", "yes", ""])
    def test_other_values_do_not_enable(self, monkeypatch, val):
        """严格 =1 才开启，避免 dev-gate 式宽口径误开（改变评分行为）。"""
        monkeypatch.setenv("PAPERFORGE_REFLECTION_EVIDENCE", val)
        assert evidence_enabled() is False


# ── build_evidence_pack 边界 ────────────────────────────────────────────────


class TestBuildEvidencePackBoundaries:
    def test_no_paper(self):
        res = build_evidence_pack({"q": "这是一个足够长的报告句子内容。"}, "")
        assert res.status == "no_paper"
        assert res.pack_text == ""

    def test_no_report(self):
        res = build_evidence_pack({"q": "", "tech": ""}, _PAPER_TEXT)
        assert res.status == "no_report"
        assert res.pack_text == ""

    def test_no_embedder(self):
        res = build_evidence_pack(
            {"q": "这是一个足够长的报告句子内容。"},
            _PAPER_TEXT,
            batch_embedder=lambda texts: None,
        )
        assert res.status == "no_embedder"
        assert res.pack_text == ""

    def test_paper_without_sentences(self):
        res = build_evidence_pack({"q": "这是一个足够长的报告句子内容。"}, "。。。！！！")
        assert res.status == "no_paper"


# ── 选证据策略 ───────────────────────────────────────────────────────────────


class TestSelection:
    def test_stray_assertive_and_anchor_both_selected(self):
        """低相似断言句（疑似编造）与高相似锚点句都应进入证据包。"""
        embedder = make_embedder(
            {
                _ANCHOR_PAPER_SENT: _V_PAPER_ANCHOR,
                _ANCHOR_REPORT_SENT: _V_REPORT_ANCHOR,
                _STRAY_CLAIM: _V_STRAY,
            }
        )
        res = build_evidence_pack(
            {
                "q": _ANCHOR_REPORT_SENT,
                "exp": _STRAY_CLAIM,
                "tech": "",
                "reflection": "",
            },
            _PAPER_TEXT,
            batch_embedder=embedder,
        )
        assert res.status == "ok"
        assert res.pack_text.startswith(PACK_HEADER)
        queries = " ".join(b["query"] for b in res.blocks)
        assert "99%" in queries, "断言句应驱动取证"
        assert "图神经网络" in res.pack_text, "锚点句对应的论文原文应被选中"

    def test_copy_sentence_skipped(self):
        """照抄句（sim≥0.85）不驱动取证——报告里已有逐字原文，不占预算。"""
        embedder = make_embedder(
            {
                _ANCHOR_PAPER_SENT: _V_PAPER_ANCHOR,
                _STRAY_CLAIM: _V_STRAY,
            }
        )
        res = build_evidence_pack(
            {"exp": _ANCHOR_PAPER_SENT + _STRAY_CLAIM},
            _PAPER_TEXT,
            batch_embedder=embedder,
        )
        assert res.status == "ok"
        assert "图神经网络" not in res.pack_text, "照抄句命中的原文不应被注入"
        assert len(res.blocks) == 1, "只有编造断言句驱动的那一块被选中"

    def test_budget_respected(self):
        """预算耗尽即停：单块被裁剪到剩余预算内，且不会超发。"""
        paper = "".join(f"论文要点句子编号{i}内容各不相同以便独立成块。" for i in range(6))
        report = "".join(f"报告讨论要点编号{i}并给出相关评价与延伸思考。" for i in range(6))
        vec_map = {f"要点句子编号{i}": _unit(i) for i in range(6)}
        # 每个报告句各自指向自己的论文句（余弦 ≈0.74，非照抄），目标互不相同
        for i in range(6):
            v = [0.0] * DIM
            v[i] = 1.0
            v[(i + 6) % DIM] = 0.9
            vec_map[f"要点编号{i}"] = _normalize(v)
        res = build_evidence_pack(
            {"q": report},
            paper,
            budget_chars=60,
            batch_embedder=make_embedder(vec_map),
        )
        assert res.status == "ok"
        assert len(res.blocks) == 1, "60 字符预算只够一块（首块被裁剪）"
        body = res.pack_text.split("\n", 1)[1]
        assert len(body) <= 60 + 10

    def test_dedup_same_target(self):
        """多个报告句命中同一段论文原文时只注入一次。"""
        paper = "图神经网络方法在路径规划任务中表现优异且鲁棒性强。"
        vec_map = {
            "报告第一句提到": _V_PAPER_ANCHOR,
            "报告第二句再次讨论": _V_PAPER_ANCHOR,
        }
        report = "报告第一句提到该方法的优异表现。" + "报告第二句再次讨论其鲁棒性优势。"
        res = build_evidence_pack(
            {"q": report},
            paper,
            batch_embedder=make_embedder(vec_map),
        )
        assert res.status == "ok"
        assert len(res.blocks) == 1


# ── 流水线集成 ───────────────────────────────────────────────────────────────


class _FakeReviewResult:
    scores = {
        "understanding_accuracy": 0.82,
        "analysis_depth": 0.76,
        "innovative_insights": 0.80,
        "evidence_support": 0.84,
        "average": 0.78,
    }
    verdict = "well_done"
    effective_evidence_count = 3
    hardcoded_overrides: list = []
    parse_failed = False
    llm_calls = 1
    llm_empty = 0
    truncated = False
    evidence_rejections: dict = {}
    score_uncertainty: dict = {}


def _patch_pipeline(monkeypatch, captured: dict):
    from mock_api.reflection_docx_parser import ReflectionDoc

    parse = ReflectionDoc(
        student_id="20240001",
        name="张三",
        paper_title="Deep Learning Advances",
        paper_author="Alice",
        paper_source="NeurIPS",
        sections={
            "q": _ANCHOR_REPORT_SENT,
            "tech": "",
            "exp": _STRAY_CLAIM,
            "reflection": "",
        },
        raw_text=_ANCHOR_REPORT_SENT + _STRAY_CLAIM,
    )
    monkeypatch.setattr(
        "mock_api.reflection_pipeline.parse_docx_from_bytes", lambda data, filename="": parse
    )
    monkeypatch.setattr(
        "mock_api.reflection_pipeline.match_paper_by_title", lambda title, db: "paper001"
    )
    monkeypatch.setattr(
        "mock_api.reflection_pipeline.compute_fidelity",
        lambda sections, full, emb: Mock(
            fidelity=0.7, status="ok", anchors=[], stray_claims=[], copy_ratio=0.0,
            copy_sentences=[], grounded_ratio=0.7,
        ),
    )
    monkeypatch.setattr(
        "mock_api.reflection_pipeline.compute_coverage",
        lambda sections, full: Mock(
            coverage=0.7, status="ok", covered=[], uncovered=[], copy_ratio=0.0,
        ),
    )
    monkeypatch.setattr(
        "mock_api.reflection_pipeline.compute_ai_likelihood",
        lambda text: {"ai_likelihood": 0.1, "tier": "human", "signals": {}, "note": ""},
    )
    monkeypatch.setattr(
        "mock_api.reflection_pipeline._get_paper_text_emb", lambda db, pid: (_PAPER_TEXT, None)
    )

    def fake_review(*args, **kwargs):
        captured.update(kwargs)
        return _FakeReviewResult()

    monkeypatch.setattr(
        "mock_api.depth_eval_reflection.ReflectionReviewer",
        lambda: Mock(review=fake_review),
    )
    monkeypatch.setattr(
        "mock_api.reflection_evidence._default_embedder",
        make_embedder(
            {
                _ANCHOR_PAPER_SENT: _V_PAPER_ANCHOR,
                _ANCHOR_REPORT_SENT: _V_REPORT_ANCHOR,
                _STRAY_CLAIM: _V_STRAY,
            }
        ),
    )


class TestPipelineIntegration:
    def test_supplement_injected_when_enabled(self, tmp_path, monkeypatch):
        """开关开启 → 证据包经 paper_supplement 注入 reviewer，诊断字段透传。"""
        captured: dict = {}
        monkeypatch.setenv("PAPERFORGE_REFLECTION_EVIDENCE", "1")
        monkeypatch.delenv("PAPERFORGE_BENCH_NO_LLM", raising=False)
        _patch_pipeline(monkeypatch, captured)

        from mock_api.reflection_pipeline import analyze_reflection_file

        f = tmp_path / "r.docx"
        f.write_bytes(b"x")
        result = analyze_reflection_file(str(f), Mock())

        supp = captured.get("paper_supplement", "")
        assert supp.startswith(PACK_HEADER), "证据包应经 paper_supplement 通道注入"
        assert "图神经网络" in supp
        ev = result["evidence_retrieval"]
        assert ev["status"] == "ok"
        assert ev["chars"] == len(supp)
        assert ev["block_count"] >= 1
        assert ev["budget_chars"] > 0

    def test_disabled_by_default_no_behavior_change(self, tmp_path, monkeypatch):
        """默认关闭 → paper_supplement 为空串（与旧版完全一致），诊断标 disabled。"""
        captured: dict = {}
        monkeypatch.delenv("PAPERFORGE_REFLECTION_EVIDENCE", raising=False)
        monkeypatch.delenv("PAPERFORGE_BENCH_NO_LLM", raising=False)
        _patch_pipeline(monkeypatch, captured)

        from mock_api.reflection_pipeline import analyze_reflection_file

        f = tmp_path / "r.docx"
        f.write_bytes(b"x")
        result = analyze_reflection_file(str(f), Mock())

        assert captured.get("paper_supplement", "") == ""
        assert result["evidence_retrieval"]["status"] == "disabled"
        assert result["average"] is not None, "关闭时评分主链路不受影响"

    def test_no_bound_paper_stays_disabled_status(self, tmp_path, monkeypatch):
        """未绑定原论文（full 为空）→ 不触发检索，状态保持 disabled。"""
        captured: dict = {}
        monkeypatch.setenv("PAPERFORGE_REFLECTION_EVIDENCE", "1")
        monkeypatch.delenv("PAPERFORGE_BENCH_NO_LLM", raising=False)
        _patch_pipeline(monkeypatch, captured)
        monkeypatch.setattr(
            "mock_api.reflection_pipeline._get_paper_text_emb", lambda db, pid: ("", None)
        )

        from mock_api.reflection_pipeline import analyze_reflection_file

        f = tmp_path / "r.docx"
        f.write_bytes(b"x")
        result = analyze_reflection_file(str(f), Mock())

        assert captured.get("paper_supplement", "") == ""
        assert result["evidence_retrieval"]["status"] == "disabled"
