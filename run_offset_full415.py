"""偏移层落地验证：对 415 全量应用 δ=-0.09 偏移，验证 κ 提升外推 + 分布变化。

不重新调用 LLM——偏移只改「最终分 → verdict 推导」，基于已有的 415 全量分数重算。
验证点：
  1) 20 篇盲评样本：偏移前 κ(分数层)=0.189 → 偏移后 κ≈0.375（框架能力复现回归结论）
  2) 415 全量：verdict 分布偏移前后变化（尤其 band A 垃圾论文是否被压到 reject）
  3) auto_offset_from_calibration 在 20 篇上搜出的 δ 是否 ≈-0.09（交叉验证）
"""
import importlib.util
import json
import os
from collections import Counter

ROOT = os.path.dirname(os.path.abspath(__file__))


def _load_dc():
    spec = importlib.util.spec_from_file_location(
        "depth_calibration",
        os.path.join(ROOT, "mock_api", "depth_calibration.py"),
    )
    dc = importlib.util.module_from_spec(spec)
    # 注册到 sys.modules，避免 @dataclass 元数据查找失败
    import sys
    sys.modules["depth_calibration"] = dc
    spec.loader.exec_module(dc)
    return dc


def fixed_verdict(s: float) -> str:
    if s >= 0.8:
        return "accept"
    if s >= 0.7:
        return "minor_revision"
    if s >= 0.5:
        return "major_revision"
    return "reject"


def kappa(a, b):
    labs = sorted(set(a) | set(b))
    n = len(a)
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa = sum((a.count(l) / n) * (b.count(l) / n) for l in labs)
    return (po - pa) / (1 - pa) if pa < 1 else 1.0


def main():
    dc = _load_dc()
    print(f"框架 SCORE_OFFSET = {dc.SCORE_OFFSET} (激活需 PAPERFORGE_DEPTH_SCORE_OFFSET 或 DEPTH_AUTO_OFFSET=1)\n")

    pool = json.load(open(os.path.join(ROOT, "calib_pool_415.json"), encoding="utf-8"))
    mine = json.load(open(os.path.join(ROOT, "calib_papers", "runs", "calib_my_review.json"), encoding="utf-8"))
    mm = {d["pid"]: d for d in mine}
    pm = {r["pid"]: r for r in pool}

    # ── 1) 20 篇盲评样本 κ 验证 ──
    sample_pids = list(mm.keys())
    depth_raw = [pm[p]["score"] for p in sample_pids]
    depth_off = [dc.apply_score_offset(s, -0.09) for s in depth_raw]
    my_v = [mm[p]["my_verdict"] for p in sample_pids]
    depth_v_raw = [fixed_verdict(s) for s in depth_raw]
    depth_v_off = [fixed_verdict(s) for s in depth_off]

    k_raw = kappa(depth_v_raw, my_v)
    k_off = kappa(depth_v_off, my_v)
    print("=== 20 篇盲评样本 κ 验证 ===")
    print(f"  偏移前 (分数层, κ基线): {k_raw:.3f}")
    print(f"  偏移后 (δ=-0.09):       {k_off:.3f}   ← 应≈0.375，验证框架落地正确")
    agree_off = sum(1 for x, y in zip(depth_v_off, my_v) if x == y)
    print(f"  偏移后一致: {agree_off}/{len(sample_pids)}\n")

    # ── 3) auto_offset_from_calibration 交叉验证 ──
    # 构造 CalibrationSample + score_fn（直接从已加载的 dc 模块取类，避免触发 mock_api 包依赖）
    CalibrationSample = dc.CalibrationSample
    samples = []
    for p in sample_pids:
        d = mm[p]
        samples.append(CalibrationSample(
            paper_id=p, text_hash="",
            expert_scores={"final": d["my_final"]},
            expert_verdict=d["my_verdict"], weight=1.0,
        ))

    def _score_fn():
        return depth_raw

    best_o, best_k = dc.auto_offset_from_calibration(samples, _score_fn)
    print("=== auto_offset_from_calibration (20 篇) ===")
    print(f"  搜索最优 δ = {best_o:+.2f}, 对应 κ = {best_k:.3f}   ← 应≈-0.09\n")

    # ── 2) 415 全量分布变化 ──
    all_raw = [r["score"] for r in pool]
    all_off = [dc.apply_score_offset(s, -0.09) for s in all_raw]
    v_raw = Counter(fixed_verdict(s) for s in all_raw)
    v_off = Counter(fixed_verdict(s) for s in all_off)
    print("=== 415 全量 verdict 分布 (偏移前 → 后) ===")
    order = ["accept", "minor_revision", "major_revision", "reject"]
    for v in order:
        a, b = v_raw.get(v, 0), v_off.get(v, 0)
        print(f"  {v:<16} {a:>4} → {b:>4}   (Δ{b - a:+d})")

    # band A 垃圾论文（测试/占位，全文极短）
    bandA = [r for r in pool if r["band"].startswith("A")]
    print(f"\n=== band A 垃圾/测试论文 ({len(bandA)} 篇) 偏移后 verdict ===")
    for r in bandA:
        s = r["score"]
        print(f"  {r['pid'][:40]:<42} score={s:.3f} → off={dc.apply_score_offset(s,-0.09):.3f}  verdict={fixed_verdict(dc.apply_score_offset(s,-0.09))}")

    # 全量均值/极值（偏移后）
    print(f"\n=== 415 全量分数偏移后统计 ===")
    print(f"  偏移前 mean={sum(all_raw)/len(all_raw):.3f} min={min(all_raw):.3f} max={max(all_raw):.3f}")
    print(f"  偏移后 mean={sum(all_off)/len(all_off):.3f} min={min(all_off):.3f} max={max(all_off):.3f}")
    # 偏移后仍有 accept 但分数<0.8 的（异常？）
    weird = [(r['pid'], s, dc.apply_score_offset(s,-0.09)) for r, s in zip(pool, all_raw)
             if dc.apply_score_offset(s, -0.09) >= 0.8]
    print(f"  偏移后仍≥0.8 (accept) 的论文数: {len(weird)}")

    # 固化校准结果到 calib_offset.json（供生产启用）
    out = {
        "offset": -0.09,
        "kappa_before": round(k_raw, 3),
        "kappa_after": round(k_off, 3),
        "n_samples": len(sample_pids),
        "source": "blind_review_comparison_2026-07-24 (LLM-vs-LLM, 20 stratified samples)",
        "note": "启用方式: 设 PAPERFORGE_DEPTH_SCORE_OFFSET=-0.09 或 DEPTH_AUTO_OFFSET=1",
    }
    with open(os.path.join(ROOT, "calib_papers", "runs", "calib_offset.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n已固化校准偏移 → calib_papers/runs/calib_offset.json (offset=-0.09, κ {k_raw:.3f}→{k_off:.3f})")


if __name__ == "__main__":
    main()
