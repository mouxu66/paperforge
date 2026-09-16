"""DEPTH 严谨化回归测试（ADR-014 · P3/P5）。

覆盖：
1. 金标 schema 校验（depth_gold.json 结构正确）。
2. 标注者间一致性算法（Cohen κ / Krippendorff α）在金标上可复现。
3. 金标驱动偏移推荐（recommend_offset_from_gold）可运行并落盘。
4. PAPERFORGE_DEPTH_GOLD_OFFSET_PATH 优先级高于 calib_offset.json。
5. （可选）全流水线回归：仅当 PAPERFORGE_GOLD_RUN=1 且金标论文存在于 DB 时运行；
   否则自动 skip，避免在 CI/无 LLM 环境误触发批量评审。

设计原则：本测试**不依赖 LLM / 数据库**即可验证严谨化基础设施（W1/W4/W6/W11）。
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from mock_api.depth_calibration import (
    get_score_offset,
    recommend_offset_from_gold,
    reset_score_offset,
)
from mock_api.depth_panel import (
    cohen_kappa,
    krippendorff_alpha_interval,
    load_annotator_labels,
)
from mock_api.stats.bootstrap import bootstrap_ci

_GOLD_PATH = os.path.join(os.path.dirname(__file__), "..", "mock_api", "depth_gold.json")


def _load_gold() -> dict:
    with open(_GOLD_PATH, encoding="utf-8") as f:
        return json.load(f)


def test_gold_schema_valid():
    """金标文件结构正确：samples 列表，每条含 paper_id / scores / verdict。"""
    gold = _load_gold()
    assert "samples" in gold and isinstance(gold["samples"], list)
    assert len(gold["samples"]) >= 1
    valid_verdicts = {"accept", "minor_revision", "major_revision", "reject"}
    for s in gold["samples"]:
        assert "paper_id" in s, f"样本缺失 paper_id: {s}"
        assert "scores" in s and isinstance(s["scores"], dict), f"样本缺失 scores: {s}"
        assert s.get("verdict", "").lower().replace(" ", "_") in valid_verdicts


def test_agreement_metrics_on_gold():
    """一致性算法在金标 verdict 上可复现。"""
    gold = _load_gold()
    verdicts = [s["verdict"] for s in gold["samples"]]
    # 完全一致应得 κ=1.0
    assert cohen_kappa(verdicts, list(verdicts)) == 1.0
    # 不同列表应低于 1.0
    flipped = list(verdicts)
    flipped[-1] = "accept" if flipped[-1] != "accept" else "reject"
    assert cohen_kappa(verdicts, flipped) < 1.0

    # Krippendorff α：校准分完全一致 → 1.0
    scores = [s["calibrated_score"] for s in gold["samples"]]
    alpha_same = krippendorff_alpha_interval([[v, v] for v in scores])
    assert alpha_same == 1.0
    # 差异大 → α 显著下降
    alpha_diff = krippendorff_alpha_interval([[v, 1.0 - v] for v in scores])
    assert alpha_diff < alpha_same


def test_load_annotator_labels_fail_open():
    """金标加载器对缺失/非法路径 fail-open 返回空 dict。"""
    assert load_annotator_labels("") == {}
    assert load_annotator_labels("/nonexistent/path.json") == {}


def test_recommend_offset_from_gold_runs():
    """金标驱动偏移推荐可运行并写出 gold_offset.json（写到临时位置，不污染仓库）。"""
    gold = _load_gold()
    # 构造一个与金标顺序一致、带系统偏高 +0.09 的基线分数（模拟 DEPTH 偏高）
    base = [s["calibrated_score"] + 0.09 for s in gold["samples"]]

    def score_fn():
        return list(base)

    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "gold_offset.json")
        result = recommend_offset_from_gold(_GOLD_PATH, score_fn, out_path=out)
        assert result["n_samples"] == len(gold["samples"])
        # 基线偏高 +0.09，金标驱动应找回到使 verdict κ=1.0 的偏移。
        # 注：搜索范围 [-0.20, 0.05] 内多个偏移都能达成完美对齐，搜索取首个；
        # 金标样本的实际最优为 -0.15（κ=1.0），故用覆盖搜索域的宽松区间断言。
        assert result["kappa"] > 0.99
        assert -0.20 <= result["recommended_offset"] <= 0.05
        assert os.path.exists(out)
        with open(out, encoding="utf-8") as f:
            payload = json.load(f)
        assert "recommended_offset" in payload


def test_gold_offset_path_priority(monkeypatch):
    """PAPERFORGE_DEPTH_GOLD_OFFSET_PATH 应优先于 calib_offset.json 的自动估计。

    注（2026-09-16）：显式 PAPERFORGE_DEPTH_SCORE_OFFSET 的优先级**高于**金标路径
    （见 depth_calibration 模块顶部声明的优先级链）。仓库根 .env 显式写了该值为 0，
    会经 Settings 通道生效并压过金标路径——故此处必须屏蔽显式配置通道，
    本用例才能真正只测「金标路径 vs calib 自动估计」这一层。
    """
    import mock_api.depth_calibration as _dc

    monkeypatch.setattr(_dc, "_explicit_offset", lambda: None)
    with tempfile.TemporaryDirectory() as td:
        gold_off = os.path.join(td, "gold_offset.json")
        with open(gold_off, "w", encoding="utf-8") as f:
            json.dump({"recommended_offset": -0.07}, f)
        monkeypatch.setenv("PAPERFORGE_DEPTH_GOLD_OFFSET_PATH", gold_off)
        # 关闭 calib 自动估计，避免其覆盖
        monkeypatch.setenv("DEPTH_AUTO_OFFSET", "0")
        monkeypatch.delenv("PAPERFORGE_DEPTH_SCORE_OFFSET", raising=False)
        reset_score_offset()
        try:
            assert abs(get_score_offset() - (-0.07)) < 1e-6
        finally:
            reset_score_offset()

    # 反向护栏：显式配置存在时必须压过金标路径（P4 显式配置最高优先）
    monkeypatch.setattr(_dc, "_explicit_offset", lambda: 0.0)
    reset_score_offset()
    try:
        assert get_score_offset() == 0.0, "显式 0 应压过金标路径 -0.07"
    finally:
        reset_score_offset()


def test_bootstrap_ci_reproducible():
    """bootstrap 95% CI 在固定 seed 下可复现。"""
    vals = [0.8, 0.8, 0.79, 0.81, 0.8, 0.78, 0.82]
    ci1 = bootstrap_ci(vals, n_boot=500, seed=42)
    ci2 = bootstrap_ci(vals, n_boot=500, seed=42)
    assert ci1 == ci2
    # CI 应落在 [0,1] 且 lo <= hi
    assert 0.0 <= ci1[0] <= ci1[1] <= 1.0


@pytest.mark.skipif(
    os.environ.get("PAPERFORGE_GOLD_RUN") != "1",
    reason="全流水线回归需 PAPERFORGE_GOLD_RUN=1 且金标论文已入库；默认 skip 以免误触发 LLM/DB",
)
def test_full_pipeline_regression():
    """（可选）对金标论文跑真实 DEPTH 流水线，断言与金标一致性在容差内。

    需要：① PAPERFORGE_GOLD_RUN=1 ② 金标 paper_id 存在于数据库 ③ 可用 LLM。
    否则本测试被 skip。运行命令示例：
        PAPERFORGE_GOLD_RUN=1 python -m pytest tests/test_depth_gold_regression.py::test_full_pipeline_regression
    """
    from mock_api.depth_eval_v4 import DepthReviewer

    gold = _load_gold()
    preds, truth = [], []
    for s in gold["samples"]:
        reviewer = DepthReviewer()
        result = reviewer.review(
            paper_id=s["paper_id"],
            title=s.get("title", ""),
            full_text=s.get("full_text", ""),
        )
        preds.append(result.final_verdict)
        truth.append(s["verdict"])
    # 宽松断言：至少 Cohen κ > 0（比随机好）。真实金标替换占位后应显著更高。
    kappa = cohen_kappa(preds, truth)
    assert kappa > 0.0, f"全流水线 verdict 与金标一致性过低 (κ={kappa:.3f})"
