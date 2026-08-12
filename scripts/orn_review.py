"""Ornstein V2 全量 41 篇感悟报告评分（vs Qwen & 人工基准）。

增量落盘 + 断点续跑，输出 deliverables/ornstein_vs_human_full.csv。
"""
import argparse
import csv
import os
import sqlite3
import time

import requests

OUT_CSV = "deliverables/ornstein_vs_human_full.csv"
DB = "mock_api/paperforge_mock.db"
BASE = "http://127.0.0.1:8080/v1/chat/completions"


def orn_llm(prompt: str) -> str:
    r = requests.post(
        BASE,
        json={
            "model": "local",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 5000,
            "temperature": 0.0,
        },
        timeout=600,
    )
    return r.json()["choices"][0]["message"]["content"]


def load_done() -> set[str]:
    if not os.path.exists(OUT_CSV):
        return set()
    with open(OUT_CSV, encoding="utf-8") as f:
        try:
            return {r["sid"] for r in csv.DictReader(f)}
        except Exception:
            return set()


def append_row(row: dict) -> None:
    fresh = not os.path.exists(OUT_CSV) or os.path.getsize(OUT_CSV) == 0
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if fresh:
            w.writeheader()
        w.writerow(row)


def main() -> None:
    from mock_api.depth_eval_reflection import ReflectionReviewer

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    gold = list(csv.DictReader(open("deliverables/human_benchmark_full.csv", encoding="utf-8-sig")))
    done = load_done()
    reviewer = ReflectionReviewer(llm_func=orn_llm)
    todo = [g for g in gold if g["sid"] not in done]
    if not todo:
        print("全部完成")
        return

    block = todo[: args.limit]
    conn = sqlite3.connect(DB)
    print(f"本次 {len(block)} 篇, 已完成 {len(gold) - len(todo)}/{len(gold)}")
    for i, g in enumerate(block, 1):
        sid = g["sid"]
        cur = conn.cursor()
        cur.execute(
            "SELECT p.id, p.full_text, s.full_text FROM papers p JOIN papers s ON s.id = p.source_paper_id WHERE p.id = ?",
            (f"reflection_{sid}",),
        )
        row = cur.fetchone()
        if not row:
            print(f"[skip] {sid}")
            continue
        rid, rep, paper = row
        t0 = time.time()
        try:
            res = reviewer.review(rid, sid, rep, paper_text=paper)
            sc = res.scores
            avg = sum(sc.values()) / len(sc) if sc else 0
            append_row(
                {
                    "sid": sid,
                    "human_total": g["total"],
                    "human_UA": g["understanding_accuracy"],
                    "human_AD": g["analysis_depth"],
                    "human_II": g["innovative_insights"],
                    "human_ES": g["evidence_support"],
                    "orn_UA": sc.get("understanding_accuracy", ""),
                    "orn_AD": sc.get("analysis_depth", ""),
                    "orn_II": sc.get("innovative_insights", ""),
                    "orn_ES": sc.get("evidence_support", ""),
                    "orn_avg": round(avg, 4),
                    "seconds": round(time.time() - t0, 1),
                }
            )
            print(f"[{i}/{len(block)}] {sid} human={g['total']} orn={avg:.3f} ({time.time()-t0:.0f}s)")
        except Exception as e:
            print(f"[{i}/{len(block)}] {sid} 失败: {e}")
    conn.close()


if __name__ == "__main__":
    main()
