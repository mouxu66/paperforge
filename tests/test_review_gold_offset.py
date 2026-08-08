"""P5 金标驱动偏移 · 真实数据集回归测试（ADR-014）。

直接消费本仓真实盲评金标数据集（非合成占位）：
  - calib_papers/runs/calib_set_20.json   : 20 篇分层抽样论文的「我的盲评」金标
  - calib_papers/runs/calib_pool_415.json : 415 篇 DEPTH 全量分数（pid -> score）

验证点：
  1) gold_samples_to_calibration 能解析真实 CalibrationSample 格式；
  2) 偏移前 verdict Cohen κ ≈ 0.189（干净基线）；
  3) 采用 δ=-0.09 后 κ ≥ 0.35（12/20 一致，与 blind_review_comparison_2026-07-24.md §8 一致）；
  4) 数据最优 δ=-0.18 对应 κ ≥ 0.40（20 小样本过拟合最优，仅作断言复现，不采用）；
  5) recommend_offset_from_gold 在真实数据集上等价于 auto_offset_from_calibration。

数据文件缺失时整体 skip（不误触发 LLM、不依赖外部网络）。
"""
import json
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REVIEW = os.path.join(_REPO, "calib_papers", "runs", "calib_set_20.json")
_POOL = os.path.join(_REPO, "calib_papers", "runs", "calib_pool_415.json")

sys.path.insert(0, _REPO)
import mock_api.depth_calibration as dc  # noqa: E402


def _kappa(a, b):
    n = len(a)
    if n == 0:
        return 0.0
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    labs = sorted(set(a) | set(b))
    pa = sum((a.count(l) / n) * (b.count(l) / n) for l in labs)
    return (po - pa) / (1 - pa) if pa < 1 else 1.0


@pytest.fixture(scope="module")
def data():
    if not (os.path.exists(_REVIEW) and os.path.exists(_POOL)):
        pytest.skip("真实盲评金标数据缺失（calib_set_20.json / calib_pool_415.json）")
    review = json.load(open(_REVIEW, encoding="utf-8"))
    pool = json.load(open(_POOL, encoding="utf-8"))
    pm = {r["pid"]: r["score"] for r in pool}
    samples = dc.load_calibration_set(_REVIEW)
    pids = [s.paper_id for s in samples]
    depth_raw = [pm[p] for p in pids]
    my_v = [s.expert_verdict for s in samples]
    return samples, depth_raw, my_v


def test_gold_samples_parse_real_format(data):
    samples, _, _ = data
    assert len(samples) == 20
    assert all(s.expert_verdict in {"accept", "minor_revision", "major_revision", "reject"} for s in samples)
    # 真实格式应含 5 维 expert_scores
    assert all("final" in s.expert_scores for s in samples)


def test_kappa_baseline_and_adopted_offset(data):
    _, depth_raw, my_v = data

    def score_fn():
        return depth_raw

    k_raw = _kappa([dc.offset_corrected_verdict(s, 0.0) for s in depth_raw], my_v)
    # 干净基线 ≈ 0.189
    assert abs(k_raw - 0.189) < 0.02

    k_adopted = _kappa([dc.offset_corrected_verdict(s, -0.09) for s in depth_raw], my_v)
    # 采用稳健 δ=-0.09 后 κ 显著回升（≥0.35，与报告 §8.1 一致）
    assert k_adopted >= 0.35
    assert k_adopted > k_raw


def test_data_optimal_offset_reproduces(data):
    samples, depth_raw, my_v = data

    def score_fn():
        return depth_raw

    best_o, best_k = dc.auto_offset_from_calibration(samples, score_fn)
    # 数据最优应为 -0.18（20 小样本过拟合），对应 κ ≥ 0.40
    assert abs(best_o - (-0.18)) < 0.02
    assert best_k >= 0.40


def test_recommend_offset_matches_auto(data, tmp_path):
    samples, depth_raw, _ = data
    gold = {"samples": [{"paper_id": s.paper_id, "scores": {"calibrated": s.expert_scores.get("final", 0.5)}, "verdict": s.expert_verdict} for s in samples]}
    gold_path = tmp_path / "gold.json"
    gold_path.write_text(json.dumps(gold), encoding="utf-8")

    def score_fn():
        return depth_raw

    res = dc.recommend_offset_from_gold(str(gold_path), score_fn)
    # 通过合成 schema 包装后，recommend 仍应复现数据最优 -0.18
    assert abs(res["recommended_offset"] - (-0.18)) < 0.02
    assert res["n_samples"] == 20
