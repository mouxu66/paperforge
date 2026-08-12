"""R1.5 分级封顶 A/B：同一批 LLM 原始输出，离线套三种下游封顶规则对比区分度。

背景（2026-08-12）：现行 R1.5 把「有效证据 2-4 条」的 4 维分数一律封顶 0.85，
41 篇全量里 UA=0.85 的有 36 篇、ES=0.85 有 35 篇 —— 硬帽本身在压平分数。
本脚本用冒烟 4 篇验证：把统一帽换成分级帽（证据 2 条→0.75、3 条→0.80、4 条→0.85）
能否在保留校验语义的同时拉开区分度。

方法：
  1. 每篇跑一次真实 review()（默认配置：无论文预览、温度 0.2），用包装 llm_func
     捕获 LLM 原始 JSON，从原始 JSON 解析 LLM 给的 4 维分数（未经过任何硬帽）。
  2. 用 review() 结果的 effective_evidence_count（snippet 逐字校验后的去重数）。
  3. 离线套三种策略：A=现行统一 0.85 帽 / B=分级帽 / C=无帽（仅 R4.5 无原创标记帽保留）。
  4. 输出三种策略的 avg 跨度 + 每篇分数，看分级帽能否把区分度推得更高。
"""
import csv
import json
import os
import sqlite3
import sys

sys.path.insert(0, ".")
os.environ.setdefault("PAPERFORGE_EVAL_SEED", "42")
os.environ.setdefault("PAPERFORGE_LLM_CACHE_TTL", "0")

from mock_api.depth_eval_reflection import (
    ReflectionReviewer,
    safe_json_parse,
    _has_originality_markers,
    MIN_EVIDENCE_FOR_VALID_REVIEW,
)

DB = "mock_api/paperforge_mock.db"
TARGETS = ["2101", "2109", "2114", "2138"]
DIMS = ("understanding_accuracy", "analysis_depth", "innovative_insights", "evidence_support")

# 现行 R1.5：证据 < 5 → 统一封顶 0.85
CURRENT_CAP = 0.85
# 分级帽：证据数 → 封顶值（2/3/4 条），5+ 不封
GRADED_CAPS = {2: 0.75, 3: 0.80, 4: 0.85}


def apply_policy(raw_scores: dict, effective: int, full_text: str, policy: str) -> dict:
    """对 LLM 原始分数套下游规则。raw_scores 是 LLM 给的原始 4 维分数。

    保留的硬规则（与 _apply_hardcoded_validation 对齐）：
      - R1：effective < 2 → 全维 0.3（本次冒烟 4 篇都是 3-4 条，不会触发）
      - R4.5：II > 0.5 但全文无原创标记 → II 封顶 0.5
    """
    out = {k: float(raw_scores.get(k, 0.5)) for k in DIMS}

    if effective < MIN_EVIDENCE_FOR_VALID_REVIEW:
        out = {k: min(v, 0.3) for k, v in out.items()}

    # R1.5：仅当分数 > 帽时向下压（现行逻辑 `if out_scores[k] > cap`）
    if policy == "nocap":
        pass  # 无帽：完全跳过 R1.5
    elif effective < 5:
        cap = (
            GRADED_CAPS.get(effective, 0.85) if policy == "graded" else CURRENT_CAP
        )
        out = {k: (min(v, cap) if v > cap else v) for k, v in out.items()}

    # R4.5：无原创标记时创新分封顶 0.5
    ii = out["innovative_insights"]
    if ii > 0.5 and full_text and not _has_originality_markers(full_text):
        out["innovative_insights"] = 0.5

    return out


def main() -> None:
    gold = {}
    with open("deliverables/human_benchmark_full.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            gold[row["sid"]] = float(row["total"])

    conn = sqlite3.connect(DB)
    results = []  # {sid, effective, raw, A, B, C, human}
    for sid in TARGETS:
        full_sid = f"20230300{sid}"
        cur = conn.cursor()
        cur.execute(
            "SELECT p.id, p.full_text, s.full_text FROM papers p JOIN papers s ON s.id = p.source_paper_id WHERE p.id = ?",
            (f"reflection_{full_sid}",),
        )
        row = cur.fetchone()
        if not row:
            print(f"  {sid}: NOT FOUND")
            continue
        rid, rep, paper = row

        captured = {}

        def capture_llm(prompt: str) -> str:
            from mock_api.depth_eval_reflection import call_llm

            raw = call_llm(prompt) or ""
            captured["raw"] = raw
            return raw

        reviewer = ReflectionReviewer(llm_func=capture_llm)
        result = reviewer.review(paper_id=rid, title=sid, full_text=rep, paper_text="")

        # 从 LLM 原始 JSON 解析 4 维分数（未经过任何硬帽）
        data = safe_json_parse(captured.get("raw", ""), None) or {}
        raw_scores = {k: float(data.get(k, 0.5)) for k in DIMS}
        effective = result.effective_evidence_count

        a = apply_policy(raw_scores, effective, rep, "current")
        b = apply_policy(raw_scores, effective, rep, "graded")
        c = apply_policy(raw_scores, effective, rep, "nocap")

        results.append(
            {
                "sid": sid,
                "effective": effective,
                "raw": raw_scores,
                "A_current": a,
                "B_graded": b,
                "C_nocap": c,
                "human": gold.get(full_sid, float("nan")),
                "overrides": result.hardcoded_overrides,
            }
        )
        print(f"\n=== {sid} (effective={effective}, human={gold.get(full_sid):.3f}) ===")
        print(f"  LLM 原始: " + " ".join(f"{k}={v:.2f}" for k, v in raw_scores.items()))
        print(f"  A 现行0.85帽: " + " ".join(f"{k}={v:.2f}" for k, v in a.items()))
        print(f"  B 分级帽:   " + " ".join(f"{k}={v:.2f}" for k, v in b.items()))
        print(f"  C 无帽:     " + " ".join(f"{k}={v:.2f}" for k, v in c.items()))
        print(f"  实际overrides: {result.hardcoded_overrides}")

    conn.close()

    # 汇总
    print("\n" + "=" * 60)
    for label, key in [("A 现行0.85帽", "A_current"), ("B 分级帽", "B_graded"), ("C 无帽", "C_nocap")]:
        avgs = [sum(r[key].values()) / 4 for r in results]
        human = [r["human"] for r in results]
        print(f"{label:12s}: avg 跨度={max(avgs)-min(avgs):.3f} "
              f"({min(avgs):.3f}~{max(avgs):.3f})  mean={sum(avgs)/len(avgs):.3f}")
    print("  human:       avg 跨度=%.3f (%.3f~%.3f)" % (
        max(human) - min(human), min(human), max(human)))


if __name__ == "__main__":
    main()
