"""真实 PeerRead 金标回归（ADR-014 · P3，诚实化替代合成占位）。

背景：mock_api/depth_gold.json 是 PLACEHOLDER_SYNTHETIC（合成占位，仅跑骨架）。
本测试用真实人类标注金标（deliverables/gold/peerread_verdict_gold.json，
N=198，ACL/arXiv 2007-2017，offset=0 真基线，由 scripts/gold/build_peerread_gold.py 生成）做回归：

1. schema 校验：samples 结构、verdict 合法、calibrated 分数齐全。
2. 基线一致率（acc / Cohen κ）：DEPTH 分 + 生产阈值(accept≥0.6) vs 人类标签。
   2026-08-09 重扫后：采纳偏移 0.0 → acc=68.2% / κ=0.364；数据最优 -0.02 → 70.7% / 0.414。
   该数字应被持续监控——若明显下滑说明模型/阈值配置漂移。
3. 防回退闸门：旧 peerread 偏移 +0.18（0.8 阈值时代产物）在 0.6 阈值下过度接受
   （56.6%），测试锁死生产表 peerread=0.0，防止误回退。
4. 金标驱动偏移推荐（recommend_offset_from_gold）在真实金标上可运行并落盘。

诚实口径：人类只给了二进制 accept/reject，因此本金标只校准裁决边界，
不冒充多维人工分。基线一致率是「模型 vs 真实人类」的诚实底线。

运行：pytest tests/test_peerread_gold_regression.py -v
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mock_api.depth_calibration import (
    gold_samples_to_calibration,
    recommend_offset_from_gold,
)

_ROOT = Path(__file__).resolve().parent.parent
_GOLD_PATH = _ROOT / "deliverables" / "gold" / "peerread_verdict_gold.json"

# 生产阈值（settings.py，与 DEPTH 裁决口径保持一致）
_ACCEPT_THRESHOLD = 0.6
_REJECT_THRESHOLD = 0.5


def _load_gold() -> dict:
    if not _GOLD_PATH.exists():
        pytest.skip(f"真实金标不存在（{_GOLD_PATH}），请先运行 scripts/gold/build_peerread_gold.py")
    with open(_GOLD_PATH, encoding="utf-8") as f:
        return json.load(f)


def _predicted_accept(calibrated: float, offset: float = 0.0) -> bool:
    s = max(0.0, min(1.0, float(calibrated) + offset))
    return s >= _ACCEPT_THRESHOLD


def _agreement(gold: dict, offset: float = 0.0) -> tuple[int, int, float]:
    """在真实金标上计算 DEPTH 分（施加偏移后）与人类 accept/reject 的一致率。"""
    samples = gold["samples"]
    agree = 0
    for s in samples:
        pred = _predicted_accept(float(s["scores"]["calibrated"]), offset)
        if pred == bool(s["human_accepted"]):
            agree += 1
    return agree, len(samples), agree / len(samples)


def test_gold_schema_valid() -> None:
    gold = _load_gold()
    assert gold.get("schema_version") == 1
    samples = gold.get("samples", [])
    assert len(samples) >= 20, "真实金标样本过少"
    for s in samples:
        assert "paper_id" in s and s["paper_id"]
        assert s["verdict"] in {"accept", "reject"}, "人类标签应映射为 accept/reject"
        assert "human_accepted" in s
        assert isinstance(s["scores"].get("calibrated"), (int, float)), "必须有 DEPTH 实测分"
        assert 0.0 <= float(s["scores"]["calibrated"]) <= 1.0


def test_gold_is_not_placeholder() -> None:
    gold = _load_gold()
    assert "PLACEHOLDER" not in gold.get("source", "").upper()
    assert gold.get("source", "").startswith("peerread"), "必须来自真实 PeerRead 数据"


def _binary_kappa(gold: dict, offset: float = 0.0) -> float:
    """二分类 Cohen's κ：DEPTH 预测（阈值 0.6）vs 人类标签。"""
    pred = [1 if _predicted_accept(float(s["scores"]["calibrated"]), offset) else 0 for s in gold["samples"]]
    truth = [1 if s["human_accepted"] else 0 for s in gold["samples"]]
    n = len(pred)
    po = sum(1 for x, y in zip(pred, truth) if x == y) / n
    pa = (sum(pred) / n) * (sum(truth) / n) + (1 - sum(pred) / n) * (1 - sum(truth) / n)
    return (po - pa) / (1 - pa) if pa < 1 else (1.0 if po == 1.0 else 0.0)


def test_baseline_agreement_with_humans() -> None:
    """基线一致率：模型分 vs 人类标签（采纳偏移 0.0，生产阈值 0.6）。

    2026-08-09 重扫后采纳偏移=0.0：acc=68.2% / κ=0.364（N=198）。
    这不是"通过"门槛测试——它记录诚实底线并防静默漂移：
    - 若以后有人把 accept 阈值/偏移调得让基线大幅下滑，这里会报警。
    - 68.2% 是 9B 本地模型在 PeerRead 语料上的真实水平（较旧配置 +0.18 的 56.6%
      提升 11.6pp），仍是「需要更强模型 / 双模型复核 / 人工裁决」的依据。
    """
    gold = _load_gold()
    agree, n, acc = _agreement(gold)
    kap = _binary_kappa(gold)
    # 诚实底线：不得明显低于重扫后基线（预留 8pp 抖动，防模型升级误报）
    assert acc >= 0.60, f"基线一致率骤降到 {acc:.3f}，低于重扫后下限 0.60"
    print(f"\n[PeerRead 金标] N={n}, 人类 accept={sum(1 for s in gold['samples'] if s['human_accepted'])}, "
          f"一致率(offset=0, 阈值{_ACCEPT_THRESHOLD})={agree}/{n}={acc:.3f}, κ={kap:.3f}")


def test_production_peerread_offset_not_overaccepting() -> None:
    """防回退闸门：生产表 peerread 偏移已重扫为 0.0。

    旧值 +0.18 是 0.8 阈值时代在 n200_base 上扫出的（历史 offset_scan 建议
    fix_threshold=0.8 → Δ=+0.18）；生产阈值降到 0.6 后该偏移让 DEPTH 过度接受
    （实测 acc=56.6%）。本测试锁死：生产表为 0.0 且一致率 ≥60%，+0.18 必须 <60%。
    """
    from mock_api.depth_calibration import DEFAULT_OFFSET_TABLE

    assert DEFAULT_OFFSET_TABLE.get("peerread", None) == 0.0, (
        "peerread 偏移必须为 0.0（0.6 阈值重扫结论）；旧 +0.18 会过度接受"
    )
    gold = _load_gold()
    agree, n, acc = _agreement(gold, offset=0.0)
    assert acc >= 0.60, f"生产配置一致率不应低于 0.60（实测 {acc:.3f}）"
    _, _, old_acc = _agreement(gold, offset=0.18)
    assert old_acc < 0.60, f"旧 +0.18 配置一致率 {old_acc:.3f}，不应 ≥ 新配置地板"


def test_offset_calibration_on_real_gold() -> None:
    """在真实金标上跑金标驱动偏移推荐（写临时文件，不污染仓库）。"""
    gold = _load_gold()

    def _score_fn() -> list[float]:
        return [float(s["scores"]["calibrated"]) for s in gold["samples"]]

    out = _ROOT / "deliverables" / "gold" / "_test_gold_offset.json"
    result = recommend_offset_from_gold(str(_GOLD_PATH), _score_fn, out_path=str(out))
    try:
        assert "recommended_offset" in result
        assert "kappa" in result
        assert result["n_samples"] == len(gold["samples"])
    finally:
        if out.exists():
            out.unlink()


def test_gold_samples_to_calibration_real() -> None:
    """真实金标可被校准框架消费（gold_samples_to_calibration 兼容格式 B）。"""
    gold = _load_gold()
    cal = gold_samples_to_calibration(gold)
    assert len(cal) == len(gold["samples"])
    verdicts = {c.expert_verdict for c in cal}
    assert verdicts <= {"accept", "reject"}, f"金标映射出意外 verdict: {verdicts}"
    scores = [c.expert_scores["final"] for c in cal]
    assert all(0.0 <= v <= 1.0 for v in scores)
