"""reflection_fidelity v2 新增能力测试：照抄检测、句对句取最大、两段式计分，
以及 depth_eval_reflection 的 snippet 真实性校验与头尾截断。

核心技巧：monkeypatch 嵌入层为确定性向量映射，避免真实模型依赖。
"""

from __future__ import annotations

import numpy as np
import pytest

from mock_api.depth_eval_reflection import (
    ReflectionReviewer,
    _snippet_exists,
    _truncate_head_tail,
)
from mock_api.reflection_fidelity import (
    FidelityResult,
    _sentence_is_crosslingual,
    compute_copy_ratio,
    compute_coverage,
    compute_fidelity,
)

# 测试句需满足：
#   ① 向量相似度可控（monkeypatch）
#   ② 6-gram 包含率符合预期（P_NEAR 需与 S_GROUND 共享 ≥15% 的 6-gram）
#   ③ 长度 ≥ MIN_REPORT_CHARS(100)

# S_COPY = P_COPY（逐字相同 → 照抄）
S_COPY = "本文提出了一种基于多传感器融合的远程实验平台架构，该架构通过标准化接口连接不同厂商的硬件设备。"
P_COPY = "本文提出了一种基于多传感器融合的远程实验平台架构，该架构通过标准化接口连接不同厂商的硬件设备。"

# S_GROUND 与 P_NEAR 共享「状态估计方法」+「实验对照」等 6-gram
S_GROUND = "对状态估计方法进行深入分析并且给出清晰实验对照，这是论文的主要贡献。"
P_NEAR = "对状态估计方法进行深入分析是本文的核心方法，实验对照验证了结果。"

# S_STRAY 与任何论文句无关（编造永动机方案）
S_STRAY = "论文证明了作者独创的永动机方案通过在旋转磁场中加入特殊材料能够实现能源的完全循环利用。"
S_TAIL = "结尾的感想段落讨论了教育机器人在中小学的普及前景和社会意义。"

# 无关论文句
P_FAR = "The weather in summer is often hot and humid in many places around the world."

# 确定性向量：R4 单位向量，保证相似度可控
#   cos(S_COPY, P_COPY)=1.0 | cos(S_GROUND, P_NEAR)=0.6 | cos(S_STRAY, ·)≈0.1/0.05
V_S_COPY = np.array([1.0, 0.0, 0.0, 0.0])
V_S_GROUND = np.array([0.0, 1.0, 0.0, 0.0])
V_S_STRAY = np.array([0.0, 0.0, 1.0, 0.0])
V_P_COPY = np.array([1.0, 0.0, 0.0, 0.0])
V_P_NEAR = np.array([0.0, 0.6, 0.1, float(np.sqrt(1 - 0.36 - 0.01))])
V_P_FAR = np.array([0.1, 0.0, 0.05, float(np.sqrt(1 - 0.01 - 0.0025))])


class _FakeEmbedder:
    def embed(self, texts):
        out = []
        for t in texts:
            t = t.strip()
            if t.startswith("本文提出了一种基于多传感器融合"):
                out.append(V_S_COPY)
            elif t.startswith("对状态估计方法进行深入分析并且"):
                out.append(V_S_GROUND)
            elif t.startswith("对状态估计方法进行深入分析是"):
                out.append(V_P_NEAR)
            elif t.startswith("论文证明了作者独创的永动机方案"):
                out.append(V_S_STRAY)
            elif t.startswith("结尾的感想段落"):
                out.append(V_S_STRAY * 0.5)
            else:
                out.append(V_P_FAR)
        return out


def _fake_many(texts, batch=64):
    return [[float(x) for x in v] for v in _FakeEmbedder().embed(texts)]


def _fake_get_embedder():
    return _FakeEmbedder()


@pytest.fixture(autouse=True)
def _patch_embedder(monkeypatch):
    import mock_api.reflection_fidelity as rf

    monkeypatch.setattr(rf, "_embed_many_ml", _fake_many)
    monkeypatch.setattr(rf, "get_ml_embedder", _fake_get_embedder)
    yield


# 报告正文需 ≥ MIN_REPORT_CHARS(100)：1 照抄 + 2 复述 + 1 编造
SECTIONS = {"q": f"{S_COPY}。{S_GROUND}。{S_GROUND}。{S_STRAY}。", "tech": "", "exp": "", "reflection": S_TAIL}
PAPER = f"{P_COPY}。{P_NEAR}。{P_FAR}。"


class TestCopyRatio:
    def test_verbatim_ratio_high(self):
        assert compute_copy_ratio(S_COPY * 3, S_COPY * 3 + "extra") == pytest.approx(1.0, abs=0.02)

    def test_unrelated_ratio_zero(self):
        assert compute_copy_ratio("完全无关的内容表述", P_FAR) == 0.0

    def test_partial_ratio(self):
        text = S_COPY + "。这是完全自创的一段话跟论文没有任何关系啊"
        assert 0.3 < compute_copy_ratio(text, S_COPY) < 0.7


class TestFidelityV2:
    def test_copy_penalty_reduces_fidelity(self):
        """1 照抄 + 2 复述 + 1 编造 → fidelity = 有据占比 − 照抄占比。
        实际长度: S_COPY(47) + 2×S_GROUND(68) + S_STRAY(43) = 158
        fidelity = 68/158 − 47/158 ≈ 0.133
        """
        fid = compute_fidelity(SECTIONS, PAPER, batch_embedder=_fake_many)
        assert fid.status == "ok"
        assert fid.fidelity == pytest.approx(0.13, abs=0.02)
        assert fid.copy_ratio == pytest.approx(0.30, abs=0.02)

    def test_no_copy_all_grounded(self):
        """无照抄、全部有效复述 → fidelity ≈ 1。"""
        sections = {"q": f"{S_GROUND}。{S_GROUND}。{S_GROUND}。{S_GROUND}。", "tech": "", "exp": ""}
        paper = f"{P_NEAR}。{P_NEAR}。"
        fid = compute_fidelity(sections, paper, batch_embedder=_fake_many)
        assert fid.fidelity >= 0.9
        assert fid.copy_ratio == 0.0

    def test_copy_sentences_reported(self):
        fid = compute_fidelity(SECTIONS, PAPER, batch_embedder=_fake_many)
        assert any("基于多传感器融合" in s for s in fid.copy_sentences)

    def test_anchors_have_copy_flag(self):
        fid = compute_fidelity(SECTIONS, PAPER, batch_embedder=_fake_many)
        assert all("copy" in a for a in fid.anchors)

    def test_stray_claim_detected(self):
        fid = compute_fidelity(SECTIONS, PAPER, batch_embedder=_fake_many)
        assert any("永动机" in s for s in fid.stray_claims)

    def test_short_sentence_not_flagged_copy_by_sim(self):
        """短句共享名词不误判照抄（MIN_COPY_CHARS 过滤）。"""
        short = "E-Lab建模与组件分析"
        sections = {"q": f"{short}。{S_GROUND}。{S_GROUND}。{S_GROUND}。{S_GROUND}。", "tech": "", "exp": ""}
        paper = f"{P_COPY}。{P_NEAR}。{P_FAR}。"
        fid = compute_fidelity(sections, paper, batch_embedder=_fake_many)
        # 短句即使与论文某句高相似（这里给它 P_COPY 向量 1.0）也不判照抄
        assert not any("E-Lab建模" in s for s in fid.copy_sentences)


class TestCoverageV2:
    def test_copy_covered_keypoint_excluded(self):
        """论文关键句与报告照抄句匹配 → 不计数（reason=copy）。"""
        # 论文关键句 = P_COPY（被照抄句覆盖，排除）+ P_NEAR（被复述句覆盖，计数）
        paper = f"{P_COPY}。{P_NEAR}。"
        cov = compute_coverage(SECTIONS, paper)
        assert cov.coverage == pytest.approx(0.5, abs=0.01)
        assert any(i.get("reason") == "copy" for i in cov.uncovered)

    def test_coverage_has_copy_ratio(self):
        cov = compute_coverage(SECTIONS, PAPER)
        assert cov.copy_ratio is not None


class TestSnippetValidation:
    def test_fake_snippet_rejected(self):
        claims = [{"id": "C1", "text": "观点", "evidence_id": "E1"}]
        pool = [{"id": "E1", "snippet": "编造的引文啊", "claim_ref": "C1"}]
        effective, v_claims, v_pool = ReflectionReviewer._cross_validate_evidence(
            claims, pool, full_text="报告里只有真实引文"
        )
        assert effective == 0
        assert v_claims == []
        assert v_pool == []

    def test_real_snippet_accepted(self):
        claims = [{"id": "C1", "text": "观点", "evidence_id": "E1"}]
        pool = [{"id": "E1", "snippet": "真实引文", "claim_ref": "C1"}]
        effective, v_claims, v_pool = ReflectionReviewer._cross_validate_evidence(
            claims, pool, full_text="报告里有真实引文"
        )
        assert effective == 1
        assert len(v_claims) == 1
        assert len(v_pool) == 1

    def test_no_full_text_backward_compat(self):
        """不传 full_text 时保持旧行为（只查 ID 交叉引用）。"""
        claims = [{"id": "C1", "text": "观点", "evidence_id": "E1"}]
        pool = [{"id": "E1", "snippet": "任何", "claim_ref": "C1"}]
        effective, _, _ = ReflectionReviewer._cross_validate_evidence(claims, pool)
        assert effective == 1

    def test_snippet_exists_whitespace_insensitive(self):
        assert _snippet_exists(" 你好 世界 ", "开头你好世界结尾")
        assert not _snippet_exists("不存在", "你好世界")


class TestCrosslingual:
    """中文占比判定：中文句复述英文论文 → 跨语言 → 只用余弦，不启用 6-gram 门阀。"""

    EN_PAPER = "This paper proposes a remote laboratory architecture using standardized interfaces and industrial networks."
    CN_PAPER = "本文提出了一种基于多传感器融合的远程实验平台架构，该架构通过标准化接口连接不同厂商的硬件设备。"

    def test_chinese_sentence_vs_english_paper_is_crosslingual(self):
        """中文复述句 vs 英文论文 → 跨语言。"""
        assert _sentence_is_crosslingual("论文研究了如何构建一个开放可扩展的远程实验室平台架构", self.EN_PAPER)

    def test_mixed_sentence_with_english_terms_is_crosslingual(self):
        """含英文术语的中文句（R-Lab/PLC）本质是中文复述 → 跨语言，不被 6-gram 门阀误杀。"""
        assert _sentence_is_crosslingual("可编程逻辑控制器（PLC）用于工业过程控制", self.EN_PAPER)

    def test_english_sentence_vs_english_paper_not_crosslingual(self):
        """英文句 vs 英文论文 → 同语言 → 6-gram 门阀适用。"""
        assert not _sentence_is_crosslingual("This paper proposes a remote laboratory architecture", self.EN_PAPER)

    def test_chinese_sentence_vs_chinese_paper_not_crosslingual(self):
        """中文句 vs 中文论文 → 同语言 → 6-gram 门阀适用。"""
        assert not _sentence_is_crosslingual("论文研究了如何构建开放平台", self.CN_PAPER)


class TestHeadTailTruncation:
    def test_short_text_not_truncated(self):
        text = "短报告"
        out, truncated = _truncate_head_tail(text)
        assert out == text
        assert truncated is False

    def test_long_text_head_tail_preserved(self):
        text = "A" * 100 + "B" * 40000 + "C" * 100
        out, truncated = _truncate_head_tail(text)
        assert truncated is True
        assert out.startswith("A" * 100)
        assert out.endswith("C" * 100)
        assert "省略" in out
        assert len(out) < len(text)
