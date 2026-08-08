"""感悟报告严谨化回归测试（ADR-014 · P3，reflection 侧）。

覆盖：
1. 人工金标 CSV schema 校验（deliverables/human_benchmark_full.csv，41 篇人工基准）。
2. 金标内部一致性：加权总分与各维度分的关系（6 维均落 [0,1]，总分 = W 加权平均）。
3. 分数不确定性基建可复现（bootstrap CI 固定 seed 下一致）。
4. （可选）全流水线回归：仅当 PAPERFORGE_GOLD_RUN=1 且报告 docx 可定位时运行，
   断言系统分与人工分的 Spearman ρ 达到阈值；否则自动 skip（避免 CI/无 LLM 误触发）。

设计原则：本测试**不依赖 LLM / 数据库**即可验证金标与严谨化基建（W4/W11）。
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

import pytest

from mock_api.depth_calibration import spearman_corr
from mock_api.stats.bootstrap import bootstrap_ci

# 41 篇人工基准（实验 C 用）；13 篇旧版子集作为兜底。
_GOLD_CANDIDATES = [
    Path(__file__).resolve().parent.parent / "deliverables" / "human_benchmark_full.csv",
    Path(__file__).resolve().parent.parent / "deliverables" / "human_benchmark.csv",
]
_GOLD_PATH = next((p for p in _GOLD_CANDIDATES if p.exists()), None)

_DIMS = (
    "understanding_accuracy",
    "analysis_depth",
    "innovative_insights",
    "evidence_support",
    "fidelity",
    "coverage",
)


def _load_gold() -> list[dict]:
    """加载人工金标 CSV → list[dict]，数值列转 float。"""
    assert _GOLD_PATH is not None, "找不到人工金标 CSV（deliverables/human_benchmark*.csv）"
    rows: list[dict] = []
    with open(_GOLD_PATH, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows.append(r)
    return rows


@pytest.mark.skipif(_GOLD_PATH is None, reason="人工金标 CSV 不存在，跳过")
def test_gold_schema_valid():
    """金标 CSV 结构正确：含 sid + 6 维 + total，全部数值落 [0,1]。"""
    rows = _load_gold()
    assert len(rows) >= 10
    for r in rows:
        assert r.get("sid"), f"样本缺失 sid: {r}"
        for dim in _DIMS:
            v = float(r[dim])
            assert 0.0 <= v <= 1.0, f"{r['sid']}.{dim}={v} 越界"
        total = float(r["total"])
        assert 0.0 <= total <= 1.0, f"{r['sid']}.total={total} 越界"


@pytest.mark.skipif(_GOLD_PATH is None, reason="人工金标 CSV 不存在，跳过")
def test_gold_total_matches_generating_weights():
    """金标 total 应与生成脚本（deliverables/analyze_expC.py）的权重一致。

    注意：该 CSV 的 total 是实验 C 旧口径权重（0.15×5 维 + 0.25×coverage）
    生成的，与当前流水线 W（创新/覆盖主导，2026-08-04 重校）不同。
    这里校验的是「金标文件内部自洽」，不是当前 W——权重漂移由
    test_gold_verdict_ordering_consistent 间接兜底。
    """
    _GOLD_WEIGHTS = {
        "understanding_accuracy": 0.15,
        "analysis_depth": 0.15,
        "innovative_insights": 0.15,
        "evidence_support": 0.15,
        "fidelity": 0.15,
        "coverage": 0.25,
    }
    # 豁免：以下 4 篇的 total 是「混元」独立评审直接给分（不经权重公式），
    # 见 deliverables/evaluator_comparison_10samples.md（Human/混元列 0.598/0.683/0.633/0.675）。
    # 豁免行仍有防漂移护栏：断言其 total 精确等于文档记录的混元直接分。
    _HUMAN_DIRECT_TOTAL = {
        "999900000012": 0.598,
        "999900000015": 0.683,
        "999900000019": 0.633,
        "999900000037": 0.675,
    }
    rows = _load_gold()
    for r in rows:
        if r["sid"] in _HUMAN_DIRECT_TOTAL:
            assert abs(float(r["total"]) - _HUMAN_DIRECT_TOTAL[r["sid"]]) < 0.002, (
                f"{r['sid']}: 混元直接分 total={r['total']} 偏离文档记录 "
                f"{_HUMAN_DIRECT_TOTAL[r['sid']]}（见 evaluator_comparison_10samples.md）"
            )
            continue
        weighted = sum(float(r[d]) * _GOLD_WEIGHTS[d] for d in _DIMS)
        assert abs(float(r["total"]) - weighted) < 0.02, (
            f"{r['sid']}: 金标 total={r['total']} 与生成权重口径 {weighted:.4f} 偏差过大"
        )


@pytest.mark.skipif(_GOLD_PATH is None, reason="人工金标 CSV 不存在，跳过")
def test_gold_verdict_ordering_consistent():
    """金标 total 排序应与维度分排序正相关（Spearman ρ > 0，粗排一致性）。"""
    rows = _load_gold()
    totals = [float(r["total"]) for r in rows]
    for dim in _DIMS:
        xs = [float(r[dim]) for r in rows]
        rho = spearman_corr(xs, totals)
        assert rho is not None and rho > 0.0, f"金标维度 {dim} 与 total 排序相关性异常 ρ={rho}"


def test_bootstrap_ci_reproducible():
    """bootstrap 95% CI 在固定 seed 下可复现（P2 基建）。"""
    vals = [0.8, 0.8, 0.79, 0.81, 0.8, 0.78, 0.82]
    ci1 = bootstrap_ci(vals, n_boot=500, seed=42)
    ci2 = bootstrap_ci(vals, n_boot=500, seed=42)
    assert ci1 == ci2
    assert 0.0 <= ci1[0] <= ci1[1] <= 1.0


@pytest.mark.skipif(
    os.environ.get("PAPERFORGE_GOLD_RUN") != "1",
    reason="全流水线回归需 PAPERFORGE_GOLD_RUN=1 且报告 docx 已就绪；默认 skip 以免误触发 LLM",
)
def test_full_pipeline_regression(tmp_path, monkeypatch):
    """（可选）对金标报告跑真实感悟流水线，断言与人工分的 Spearman ρ 在容差内。

    需要：① PAPERFORGE_GOLD_RUN=1 ② 报告 docx 可定位（默认 ~/Desktop/Word文档，
    可用 PAPERFORGE_REFLECTION_SOURCE_DIR 覆盖）③ 可用 LLM + 绑定 DB。
    否则本测试被 skip。运行命令示例：
        PAPERFORGE_GOLD_RUN=1 python -m pytest tests/test_reflection_gold_regression.py::test_full_pipeline_regression
    """
    source_dir = Path(
        os.environ.get("PAPERFORGE_REFLECTION_SOURCE_DIR", str(Path.home() / "Desktop" / "Word文档"))
    )
    if not source_dir.is_dir():
        pytest.skip(f"报告目录不存在: {source_dir}")

    from mock_api.database import SessionLocal
    from mock_api.reflection_pipeline import analyze_reflection_file

    import re as _re

    gold = {r["sid"]: float(r["total"]) for r in _load_gold()}
    docx_by_sid: dict[str, Path] = {}
    for f in source_dir.glob("*.docx"):
        m = _re.search(r"(\d{12})", f.name)
        if m and m.group(1) in gold:
            docx_by_sid.setdefault(m.group(1), f)

    if not docx_by_sid:
        pytest.skip(f"报告目录中找不到金标学号的 docx: {source_dir}")

    db = SessionLocal()
    preds, truth = [], []
    try:
        for sid, f in sorted(docx_by_sid.items()):
            res = analyze_reflection_file(str(f), db)
            avg = res.get("average")
            if avg is None:
                continue  # LLM 故障等：该样本不可用，跳过
            preds.append(float(avg))
            truth.append(gold[sid])
    finally:
        db.close()
    if len(preds) < 5:
        pytest.skip(f"有效样本不足（{len(preds)} < 5）")
    rho = spearman_corr(preds, truth)
    # 宽松断言：至少 > 0（比随机好）；真实金标替换后应显著更高（参考实验 C ρ≈0.476）。
    assert rho is not None and rho > 0.0, f"系统分与人工分排序相关性异常 ρ={rho}"
