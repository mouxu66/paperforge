"""真实线上传参冒烟：完全复刻 reflection_pipeline.py 的调用方式跑 10 篇（只跑 A）。

线上传参（2026-08-12 确认）：
  ReflectionReviewer().review(
      rid, title, parse.raw_text,
      student_id=...,
      paper_text=full,            # 原论文全文（始终传入）
      paper_supplement=paper_supplement,  # build_fulltext_context(fast=True) + extract_key_sentences
  )

用法：
  python scripts/smoke_online_pipeline.py [--limit N] [--offset M]
  结果增量落盘 deliverables/smoke_online_10.csv（断点续跑，跳过已完成 sid）。
"""
import argparse
import csv
import os
import sqlite3
import sys
import time

sys.path.insert(0, ".")
os.environ.setdefault("PAPERFORGE_EVAL_SEED", "42")
os.environ.setdefault("PAPERFORGE_LLM_CACHE_TTL", "0")

from mock_api.depth_eval_reflection import ReflectionReviewer

DB = "mock_api/paperforge_mock.db"
OUT_CSV = "deliverables/smoke_online_10.csv"

DIMS = ("understanding_accuracy", "analysis_depth", "innovative_insights", "evidence_support")


def build_supplement(bound, full):
    """复刻 reflection_pipeline.py 的 paper_supplement 构建（fast=True，零 LLM）。"""
    from mock_api.depth_fulltext import (
        build_fulltext_context,
        extract_key_sentences,
        format_supplement,
    )
    from mock_api.depth_eval_v4 import call_llm

    supp = ""
    try:
        ctx = build_fulltext_context(bound, full, llm_func=call_llm, fast=True)
        if ctx is not None:
            default_supp = format_supplement(ctx)
            key_sents = extract_key_sentences(full)
            parts = [p for p in [default_supp, key_sents] if p.strip()]
            supp = "\n\n".join(parts) if parts else ""
    except Exception as e:  # noqa: BLE001 - fail-open
        print(f"    [supplement fail] {type(e).__name__}: {e}", flush=True)
        supp = ""
    return supp


def load_done() -> set[str]:
    if not os.path.exists(OUT_CSV):
        return set()
    with open(OUT_CSV, encoding="utf-8-sig") as f:
        try:
            return {r["sid"] for r in csv.DictReader(f)}
        except Exception:
            return set()


def append_row(row: dict) -> None:
    fresh = not os.path.exists(OUT_CSV) or os.path.getsize(OUT_CSV) == 0
    with open(OUT_CSV, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if fresh:
            w.writeheader()
        w.writerow(row)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--offset", type=int, default=0)
    args = ap.parse_args()

    gold = {}
    with open("deliverables/human_benchmark_full.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            gold[row["sid"]] = float(row["total"])
    all_sids = [r["sid"] for r in csv.DictReader(
        open("deliverables/human_benchmark_full.csv", encoding="utf-8-sig"))]

    done = load_done()
    todo = [s for s in all_sids if s not in done][args.offset:]
    if not todo:
        print("全部完成")
        return

    block = todo[: args.limit]
    conn = sqlite3.connect(DB)
    for i, sid in enumerate(block, 1):
        full_sid = sid if sid.startswith("2023") else f"20230300{sid}"
        cur = conn.cursor()
        cur.execute(
            "SELECT p.id, p.full_text, s.id, s.full_text FROM papers p JOIN papers s ON s.id = p.source_paper_id WHERE p.id = ?",
            (f"reflection_{full_sid}",),
        )
        row = cur.fetchone()
        if not row:
            print(f"[skip] {sid} NOT FOUND", flush=True)
            continue
        rid, rep, bound, paper = row
        supp = build_supplement(bound, paper)

        t0 = time.time()
        try:
            r = ReflectionReviewer().review(
                paper_id=rid, title=sid, full_text=rep,
                paper_text=paper, paper_supplement=supp,
            )
            sc = r.scores
            avg = sum(float(sc.get(d, 0)) for d in DIMS) / 4 if sc else 0
            rec = {
                "sid": sid,
                "human_total": gold.get(full_sid, ""),
                "report_chars": len(rep),
                "paper_chars": len(paper),
                "supplement_chars": len(supp),
                "paper_preview_chars": r.paper_preview_chars,
                "effective_evidence": r.effective_evidence_count,
                "UA": sc.get("understanding_accuracy", ""),
                "AD": sc.get("analysis_depth", ""),
                "II": sc.get("innovative_insights", ""),
                "ES": sc.get("evidence_support", ""),
                "avg": round(avg, 4),
                "verdict": r.verdict,
                "seconds": round(time.time() - t0, 1),
            }
            append_row(rec)
            print(f"[{i}/{len(block)}] {sid}: UA={rec['UA']} AD={rec['AD']} II={rec['II']} "
                  f"ES={rec['ES']} avg={avg:.3f} (human={gold.get(full_sid):.3f}) "
                  f"[supp={len(supp)} prev={r.paper_preview_chars} ev={r.effective_evidence_count} "
                  f"{time.time()-t0:.0f}s]", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(block)}] {sid} 失败: {type(e).__name__}: {e}", flush=True)
    conn.close()

    print("\n--- 汇总 ---", flush=True)
    rows = list(csv.DictReader(open(OUT_CSV, encoding="utf-8-sig")))
    avgs = [float(r["avg"]) for r in rows if r.get("avg")]
    humans = [float(r["human_total"]) for r in rows if r.get("human_total")]
    if avgs:
        print(f"  已跑 {len(rows)} 篇 | avg 跨度={max(avgs)-min(avgs):.3f} "
              f"({min(avgs):.3f}~{max(avgs):.3f}) | human 跨度={max(humans)-min(humans):.3f}", flush=True)
        mae = sum(abs(float(r["avg"]) - float(r["human_total"])) for r in rows
                  if r.get("avg") and r.get("human_total")) / len(rows)
        print(f"  MAE vs human = {mae:.3f}", flush=True)


if __name__ == "__main__":
    main()
