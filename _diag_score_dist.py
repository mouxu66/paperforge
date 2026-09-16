"""诊断：v4 评审分数分布 —— 为什么整体偏低？

拆解层次：
  L0 最终分 calibrated_score 分布（用户看到的二三十分/五六十分）
  L1 分层：base_score（原始加权）→ calibrated_score_raw（+offset前）
          → calibrated_score（+offset后）→ 一票否决
  L2 维度级：novelty / rigor / influence / reproducibility / obj
  L3 否决原因频次
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics as st
from collections import Counter
from pathlib import Path

DB = Path(__file__).resolve().parent / "mock_api" / "paperforge_mock.db"
OUT = Path(__file__).resolve().parent / "deliverables" / "diag"


def pct(vals, q):
    if not vals:
        return float("nan")
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def hist(vals, edges):
    c = Counter()
    for v in vals:
        for lo, hi in zip(edges, edges[1:]):
            if lo <= v < hi:
                c[f"{lo:.2f}-{hi:.2f}"] += 1
                break
        else:
            c[f">={edges[-1]:.2f}"] += 1
    return c


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute(
        """select paper_id, kind, final_verdict, q2_result, q3_result, q4_result,
                  q5c_result, q5a_result, q5b_result, reflection_result
           from depth_reviews_v4
           where final_verdict is not null and final_verdict != ''"""
    ).fetchall()

    recs = []
    for r in rows:
        try:
            fv = json.loads(r["final_verdict"])
        except Exception:
            continue
        rec = {
            "paper_id": r["paper_id"],
            "kind": r["kind"],
            "verdict": fv.get("final_verdict"),
            "final": fv.get("calibrated_score"),
            "raw": fv.get("calibrated_score_raw"),
            "base": fv.get("base_score"),
            "offset_applied": fv.get("offset_applied"),
            "override_reason": fv.get("override_reason") or "",
            "llm_verdict": fv.get("llm_verdict"),
            "has_figures": fv.get("has_figures"),
            "w_fig": fv.get("w_fig"),
        }
        for k in ("q2_result", "q3_result", "q4_result"):
            try:
                rec[k] = json.loads(r[k]) if r[k] else {}
            except Exception:
                rec[k] = {}
        recs.append(rec)

    print(f"总评审数(有裁定): {len(recs)}")

    # ---- L0 最终分分布 ----
    finals = [r["final"] for r in recs if isinstance(r["final"], (int, float))]
    print("\n===== L0 最终分 calibrated_score (0-1) =====")
    print(f"n={len(finals)} mean={st.mean(finals):.4f} median={st.median(finals):.4f} "
          f"min={min(finals):.4f} max={max(finals):.4f} stdev={st.pstdev(finals):.4f}")
    for k, v in hist(finals, [0, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1.0]).items():
        print(f"  {k:>10} : {v:>4}  {'#' * min(60, v)}")

    print("\n  verdict 分布:")
    for k, v in Counter(r["verdict"] for r in recs).most_common():
        print(f"    {k:<18} {v:>4}  ({v/len(recs)*100:.1f}%)")

    # ---- L1 分层 ----
    print("\n===== L1 分层：base → raw → final =====")
    for key, label in (("base", "base_score 原始加权"),
                       ("raw", "calibrated_score_raw"),
                       ("final", "calibrated_score 最终")):
        vs = [r[key] for r in recs if isinstance(r[key], (int, float))]
        print(f"  {label:<28} n={len(vs):>4} mean={st.mean(vs):.4f} "
              f"median={st.median(vs):.4f} p90={pct(vs,.9):.4f} max={max(vs):.4f}")

    print("\n  offset_applied 频次:")
    for k, v in Counter(str(r["offset_applied"]) for r in recs).most_common():
        print(f"    {k:<8} {v:>4}  ({v/len(recs)*100:.1f}%)")

    # 平均下压幅度
    deltas = [r["final"] - r["raw"] for r in recs
              if isinstance(r["final"], (int, float)) and isinstance(r["raw"], (int, float))]
    if deltas:
        print(f"\n  下压幅度 final-raw: mean={st.mean(deltas):.4f} "
              f"median={st.median(deltas):.4f} min={min(deltas):.4f} max={max(deltas):.4f}")
        print("  下压幅度分布:")
        for k, v in hist(deltas, [-1, -.5, -.3, -.2, -.15, -.1, -.05, 0, .05, .5]).items():
            print(f"    {k:>10} : {v:>4}  {'#' * min(60, v)}")

    # ---- L3 否决原因 ----
    print("\n===== L3 override_reason 模式频次 =====")
    pat = Counter()
    fatal_cnt = Counter()
    for r in recs:
        reason = r["override_reason"]
        if not reason:
            pat["(无 override)"] += 1
            continue
        m = re.search(r"存在\s*(\d+)\s*个致命缺陷", reason)
        if m:
            fatal_cnt[int(m.group(1))] += 1
            pat["一票否决(致命缺陷)"] += 1
        elif "分数对齐" in reason or "对齐到" in reason:
            pat["分数-裁定对齐"] += 1
        else:
            pat[reason[:40]] += 1
    for k, v in pat.most_common():
        print(f"  {k:<28} {v:>4}  ({v/len(recs)*100:.1f}%)")
    print("\n  致命缺陷个数分布:", dict(sorted(fatal_cnt.items())))

    # ---- L2 维度级 ----
    print("\n===== L2 维度级分数（从 q2/q3/q4 抽取）=====")
    dim_keys = ["novelty_score", "rigor_score", "influence_score",
                "reproducibility_score", "hotspot_alignment_score",
                "figure_consistency_score"]
    dims = {k: [] for k in dim_keys}

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in dims and isinstance(v, (int, float)):
                    dims[k].append(v)
                else:
                    walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    for r in recs:
        for k in ("q2_result", "q3_result", "q4_result"):
            walk(r.get(k) or {})

    for k in dim_keys:
        vs = dims[k]
        if not vs:
            print(f"  {k:<32} 无数据")
            continue
        z = sum(1 for v in vs if v == 0)
        print(f"  {k:<32} n={len(vs):>5} mean={st.mean(vs):.4f} median={st.median(vs):.4f} "
              f"p90={pct(vs,.9):.4f} max={max(vs):.4f} 零值率={z/len(vs)*100:.1f}%")

    # ---- 高分段是否存在 ----
    print("\n===== 高分论文（final >= 0.7）是否存在 =====")
    hi = [r for r in recs if isinstance(r["final"], (int, float)) and r["final"] >= 0.7]
    print(f"  >=0.70 : {len(hi)} 篇")
    hi6 = [r for r in recs if isinstance(r["final"], (int, float)) and r["final"] >= 0.6]
    print(f"  >=0.60 : {len(hi6)} 篇")
    for r in hi6[:15]:
        print(f"    {r['paper_id'][:44]:<46} final={r['final']:.4f} "
              f"raw={r['raw']:.4f} base={r['base']:.4f} v={r['verdict']}")

    # ---- 天花板分析：base_score 的上限 ----
    print("\n===== 天花板：base_score 分布（offset 前一站）=====")
    bases = [r["base"] for r in recs if isinstance(r["base"], (int, float))]
    for k, v in hist(bases, [0, .2, .3, .4, .5, .6, .7, .8, 1.0]).items():
        print(f"  {k:>10} : {v:>4}  {'#' * min(60, v)}")

    OUT.mkdir(parents=True, exist_ok=True)
    return recs


if __name__ == "__main__":
    main()
