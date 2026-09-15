"""P0-12 全文相似度检测单测（text_similarity.py）。

覆盖：
- 归一化 / shingling / Jaccard 纯函数正确性
- 无全文 / 无对照论文 → 空结果
- 近重复全文（重复发表）→ TEXT_DUPLICATION_CANDIDATE + needs_human_review
- 不相似论文不误报
- 排除自身、阈值参数生效
- run_paper_audit 接线：checks_run 含 P0-12_text_duplication
"""

from __future__ import annotations

from mock_api.experiment_audit.service import AuditService
from mock_api.experiment_audit.text_similarity import (
    _jaccard,
    _normalize,
    _shingles,
    detect_semantic_duplication,
    detect_text_duplication,
    scan_corpus_duplication,
)
from mock_api.models import Paper

# 足够长（>50 词）的论文全文，保证走完整比对路径
_TEXT_A = (
    "We propose a novel framework for image classification. Our model uses a "
    "hierarchical feature extractor combined with a lightweight attention module. "
    "We train on the ImageNet dataset with batch size 32 for 50 epochs using the "
    "Adam optimizer with an initial learning rate of 3e-4 and cosine decay. "
    "Extensive experiments show that our method achieves 95.2 percent accuracy on "
    "the benchmark, outperforming the strongest baseline by 3.1 percentage points. "
    "Ablation studies demonstrate that each proposed module contributes positively "
    "to the final performance. We also report the standard deviation across five "
    "random seeds to confirm the stability of the results."
)

_TEXT_B = (
    "This paper studies federated learning systems under heterogeneous data. We "
    "analyze the convergence rate of decentralized optimization and provide a tight "
    "bound on the communication complexity. Our theoretical results cover both "
    "convex and non-convex objectives. Experiments on synthetic and real-world "
    "datasets confirm the theoretical analysis and reveal practical trade-offs "
    "between local computation and global synchronization. We conclude with a "
    "discussion of privacy implications and directions for future work."
)

# 部分重叠：共享一段核心 + 各自独立段落（用于验证阈值参数生效）
_SHARED = (
    "We propose a novel framework for image classification using a hierarchical "
    "feature extractor and a lightweight attention module trained on ImageNet. "
)
_UNIQ_C = (
    "Our contribution focuses on the design of the feature pyramid and its "
    "integration with region proposal networks. We validate the design choices "
    "through a controlled study across three backbone architectures and report "
    "detailed ablations for each component along with training wall-clock time."
)
_UNIQ_D = (
    "The key insight is that attention maps provide strong localization cues. "
    "We derive a closed-form bound on the attention sparsity and show that the "
    "proposed module reduces computation while preserving accuracy on small "
    "devices and low-precision hardware."
)


def _seed_paper(db, paper_id: str, title: str, full_text: str) -> Paper:
    p = Paper(id=paper_id, title=title, full_text=full_text)
    db.add(p)
    db.commit()
    return p


class TestPureFunctions:
    def test_normalize_lowercases_and_strips_punct(self):
        assert _normalize("Hello, World!  F1=84.6%") == "hello world f1 84 6"

    def test_shingles_produce_word_ngrams(self):
        sh = _shingles("a b c d e f", size=3)
        assert sh == {"a b c", "b c d", "c d e", "d e f"}

    def test_shingles_empty_when_too_short(self):
        assert _shingles("only four words here", size=5) == set()

    def test_jaccard(self):
        assert _jaccard({"a", "b"}, {"b", "c"}) == 1 / 3
        assert _jaccard(set(), {"a"}) == 0.0
        assert _jaccard({"a"}, {"a"}) == 1.0


class TestDetectTextDuplication:
    def test_no_paper_returns_empty(self, db_session):
        assert detect_text_duplication(db_session, "missing") == []

    def test_no_full_text_returns_empty(self, db_session):
        _seed_paper(db_session, "p-notxt", "No text paper", "")
        assert detect_text_duplication(db_session, "p-notxt") == []

    def test_no_other_papers_returns_empty(self, db_session):
        _seed_paper(db_session, "p-alone", "Alone", _TEXT_A)
        assert detect_text_duplication(db_session, "p-alone") == []

    def test_near_duplicate_flagged(self, db_session):
        _seed_paper(db_session, "p-dup-a", "Duplicate A", _TEXT_A)
        _seed_paper(db_session, "p-dup-b", "Duplicate B", _TEXT_A)

        findings = detect_text_duplication(db_session, "p-dup-a")
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "TEXT_DUPLICATION_CANDIDATE"
        assert f["severity"] == "high"
        assert f["needs_human_review"] is True
        assert f["normal_explanation"]
        assert "p-dup-b" in f["computed"]

    def test_self_is_excluded(self, db_session):
        _seed_paper(db_session, "p-self", "Self", _TEXT_A)
        # 只有自己一篇，不应与自身比对
        assert detect_text_duplication(db_session, "p-self") == []

    def test_dissimilar_not_flagged(self, db_session):
        _seed_paper(db_session, "p-diss-a", "Dissimilar A", _TEXT_A)
        _seed_paper(db_session, "p-diss-b", "Dissimilar B", _TEXT_B)
        assert detect_text_duplication(db_session, "p-diss-a") == []

    def test_threshold_param_respected(self, db_session):
        _seed_paper(db_session, "p-thr-a", "Threshold A", _SHARED + _UNIQ_C)
        _seed_paper(db_session, "p-thr-b", "Threshold B", _SHARED + _UNIQ_D)
        # 默认阈值 0.55 下，部分重叠（约 0.2）不应命中
        assert detect_text_duplication(db_session, "p-thr-a") == []
        # 阈值降到重叠度以下则命中
        findings = detect_text_duplication(
            db_session, "p-thr-a", jaccard_threshold=0.15
        )
        assert any(f["type"] == "TEXT_DUPLICATION_CANDIDATE" for f in findings)


class TestServiceWiring:
    def test_run_paper_audit_records_p012_check(self, db_session):
        _seed_paper(db_session, "p-wired", "Wired Paper", _TEXT_A)
        audit = AuditService().run_paper_audit(db_session, "p-wired")

        by_check = {c["check"]: c for c in audit.checks_run}
        assert "P0-12_text_duplication" in by_check
        assert by_check["P0-12_text_duplication"]["status"] == "ok"

    def test_run_paper_audit_flags_corpus_duplicate(self, db_session):
        _seed_paper(db_session, "p-wired-a", "Wired A", _TEXT_A)
        _seed_paper(db_session, "p-wired-b", "Wired B", _TEXT_A)
        audit = AuditService().run_paper_audit(db_session, "p-wired-a")

        types = {f["type"] for f in audit.findings}
        assert "TEXT_DUPLICATION_CANDIDATE" in types


# ---------------------------------------------------------------------------
# T4 全库两两相似度扫描（scan_corpus_duplication，倒排索引加速）
# ---------------------------------------------------------------------------
class TestScanCorpusDuplication:
    def test_near_duplicate_pair_detected(self, db_session):
        _seed_paper(db_session, "p-corp-a", "Corpus A", _TEXT_A)
        _seed_paper(db_session, "p-corp-b", "Corpus B", _TEXT_A)
        pairs = scan_corpus_duplication(db_session)
        assert len(pairs) == 1
        p = pairs[0]
        assert {p["paper_a"], p["paper_b"]} == {"p-corp-a", "p-corp-b"}
        assert p["jaccard"] >= 0.9
        assert p["title_a"] and p["title_b"]

    def test_dissimilar_clean(self, db_session):
        _seed_paper(db_session, "p-corp-a", "Corpus A", _TEXT_A)
        _seed_paper(db_session, "p-corp-b", "Corpus B", _TEXT_B)
        assert scan_corpus_duplication(db_session) == []

    def test_threshold_respected(self, db_session):
        _seed_paper(db_session, "p-corp-a", "Corpus A", _SHARED + _UNIQ_C)
        _seed_paper(db_session, "p-corp-b", "Corpus B", _SHARED + _UNIQ_D)
        # 默认全库阈值 0.30：部分重叠（约 0.2）不命中
        assert scan_corpus_duplication(db_session) == []
        # 阈值降到重叠度以下则命中
        pairs = scan_corpus_duplication(db_session, jaccard_threshold=0.15)
        assert len(pairs) == 1

    def test_max_pairs_respected(self, db_session):
        for i in range(3):
            _seed_paper(db_session, f"p-corp-{i}", f"Corpus {i}", _TEXT_A)
        pairs = scan_corpus_duplication(db_session, max_pairs=2)
        assert len(pairs) == 2  # 3 篇相同 → 3 对，只取前 2

    def test_fewer_than_two_papers_empty(self, db_session):
        _seed_paper(db_session, "p-corp-alone", "Alone", _TEXT_A)
        assert scan_corpus_duplication(db_session) == []


# ---------------------------------------------------------------------------
# T3 语义级重复（换词重写）：embedding 余弦 + 表层 Jaccard 双信号
# ---------------------------------------------------------------------------
# 与 _TEXT_A 共享开篇短语（保证有 ≥1 个共享 5-gram 进入候选集），但整体措辞
# 不同（表层 Jaccard 低）→ 是「换词重写」的模拟文本。
_TEXT_REWRITE = (
    "We propose a novel framework for image classification. Our method integrates "
    "a deep convolutional backbone with multi-scale feature fusion and an efficient "
    "self-attention mechanism. Training is performed on ImageNet with a batch size "
    "of 32 over 50 epochs, optimizing with Adam and a cosine schedule at learning "
    "rate 3e-4. Comprehensive evaluation indicates our system attains 95.2 percent "
    "accuracy on the benchmark, beating the strongest competitor by 3.1 percent. "
    "Ablation experiments validate that every ingredient contributes to the final "
    "outcome, and the findings are reproducible across five random seeds."
)


def _fake_embed_batch(texts):
    """伪嵌入：含「novel framework」的文本 → [1,0,0]，否则 [0,1,0]。

    用于在无向量模型环境下确定性验证双信号逻辑（余弦 = 1.0 或 0.0）。
    """
    out = []
    for t in texts:
        vec = [1.0, 0.0, 0.0] if "novel framework" in t else [0.0, 1.0, 0.0]
        out.append(vec)
    return out


class TestSemanticDuplication:
    def test_rewrite_flagged(self, db_session, monkeypatch):
        monkeypatch.setattr(
            "mock_api.semantic_search.embed_batch", _fake_embed_batch
        )
        _seed_paper(db_session, "p-sem-a", "Semantic A", _TEXT_A)
        _seed_paper(db_session, "p-sem-b", "Semantic B", _TEXT_REWRITE)
        findings = detect_semantic_duplication(db_session, "p-sem-a")
        assert len(findings) == 1
        f = findings[0]
        assert f["type"] == "SEMANTIC_DUPLICATION_CANDIDATE"
        assert f["severity"] == "high"
        assert f["needs_human_review"] is True
        assert f["normal_explanation"]
        assert "p-sem-b" in f["computed"]
        assert "cosine" in f["computed"] and "surface_jaccard" in f["computed"]

    def test_unrelated_not_flagged(self, db_session, monkeypatch):
        monkeypatch.setattr(
            "mock_api.semantic_search.embed_batch", _fake_embed_batch
        )
        _seed_paper(db_session, "p-sem-a", "Semantic A", _TEXT_A)
        _seed_paper(db_session, "p-sem-b", "Semantic B", _TEXT_B)  # 不同语义
        assert detect_semantic_duplication(db_session, "p-sem-a") == []

    def test_surface_similar_not_flagged(self, db_session, monkeypatch):
        """表层已高度重叠（同文本）→ 归表层检测管，语义检测不重复报警。"""
        monkeypatch.setattr(
            "mock_api.semantic_search.embed_batch", _fake_embed_batch
        )
        _seed_paper(db_session, "p-sem-a", "Semantic A", _TEXT_A)
        _seed_paper(db_session, "p-sem-b", "Semantic B", _TEXT_A)
        # 余弦 1.0 但表层 Jaccard=1.0 > 0.35 上限 → 过滤
        assert detect_semantic_duplication(db_session, "p-sem-a") == []

    def test_embed_failure_fail_open(self, db_session, monkeypatch):
        """嵌入调用失败 → 返回空（fail-open），不抛异常。"""

        def _broken(texts):
            raise RuntimeError("embed 服务不可用")

        monkeypatch.setattr("mock_api.semantic_search.embed_batch", _broken)
        _seed_paper(db_session, "p-sem-a", "Semantic A", _TEXT_A)
        _seed_paper(db_session, "p-sem-b", "Semantic B", _TEXT_REWRITE)
        assert detect_semantic_duplication(db_session, "p-sem-a") == []

    def test_service_wiring_records_p013_check(self, db_session):
        """run_paper_audit 接线：checks_run 含 P0-13_semantic_duplication。"""
        _seed_paper(db_session, "p-sem-wired", "Wired", _TEXT_A)
        audit = AuditService().run_paper_audit(db_session, "p-sem-wired")
        by_check = {c["check"]: c for c in audit.checks_run}
        assert "P0-13_semantic_duplication" in by_check
        assert by_check["P0-13_semantic_duplication"]["status"] in ("ok", "skipped")
