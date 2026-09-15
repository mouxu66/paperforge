"""「只喂标题+摘要」实验：Ornstein-V2 打分时只给论文标题+摘要，不喂正文/补充。

对比三档（同 11 篇）：
  A) 标题+摘要（本实验）：paper_text = "标题\n摘要"，supplement 空
  B) 无论文：paper_text=""（复用 smoke_nopaper_10 的结果，本脚本顺带重跑保证同温同批）
  C) 线上全文：paper_text=full + supplement（复用 smoke_online_10 的已落盘结果）

同时检查 UA 是否失真：对比标题+摘要版的 UA 与无论文版、人工金标 UA。
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
OUT_CSV = "deliverables/smoke_title_abstract_11.csv"
DIMS = ("understanding_accuracy", "analysis_depth", "innovative_insights", "evidence_support")


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
    ap.add_argument("--limit", type=int, default=11)
    args = ap.parse_args()

    gold = {}
    with open("deliverables/human_benchmark_full.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            gold[row["sid"]] = row
    base = [r["sid"] for r in csv.DictReader(
        open("deliverables/human_benchmark_full.csv", encoding="utf-8-sig"))][:10]
    sids = base + ["999900000007"]  # 前 10 + 4 篇极端样本里缺的 2109

    done = load_done()
    todo = [s for s in sids if s not in done][: args.limit]
    if not todo:
        print("全部完成")
    else:
        conn = sqlite3.connect(DB)
        for i, sid in enumerate(todo, 1):
            cur = conn.cursor()
            cur.execute(
                "SELECT p.id, p.full_text, s.title, s.abstract FROM papers p "
                "JOIN papers s ON s.id = p.source_paper_id WHERE p.id = ?",
                (f"reflection_{sid}",),
            )
            row = cur.fetchone()
            if not row:
                print(f"[skip] {sid} NOT FOUND", flush=True)
                continue
            rid, rep, title, abstract = row
            # 只喂标题+摘要（≈600 字，远小于 4000 基础预览）
            paper_summary = f"论文标题：{title or ''}\n\n论文摘要：{abstract or ''}"
            t0 = time.time()
            try:
                r = ReflectionReviewer().review(
                    paper_id=rid, title=sid, full_text=rep,
                    paper_text=paper_summary, paper_supplement="",
                )
                sc = r.scores
                rec = {
                    "sid": sid,
                    "human_total": gold.get(sid, {}).get("total", ""),
                    "human_UA": gold.get(sid, {}).get("understanding_accuracy", ""),
                    "paper_summary_chars": len(paper_summary),
                    "paper_preview_chars": r.paper_preview_chars,
                    "effective_evidence": r.effective_evidence_count,
                    "UA": sc.get("understanding_accuracy", ""),
                    "AD": sc.get("analysis_depth", ""),
                    "II": sc.get("innovative_insights", ""),
                    "ES": sc.get("evidence_support", ""),
                    "avg": round(sum(float(sc.get(d, 0)) for d in DIMS) / 4, 4),
                    "seconds": round(time.time() - t0, 1),
                }
                append_row(rec)
                print(f"[{i}/{len(todo)}] {sid}: UA={rec['UA']} AD={rec['AD']} II={rec['II']} "
                      f"ES={rec['ES']} avg={rec['avg']:.3f} (human={rec['human_total']}) "
                      f"[prev={r.paper_preview_chars} ev={r.effective_evidence_count} "
                      f"{time.time()-t0:.0f}s]", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[{i}/{len(todo)}] {sid} 失败: {type(e).__name__}: {e}", flush=True)
        conn.close()

    summarize()


def summarize() -> None:
    rows = list(csv.DictReader(open(OUT_CSV, encoding="utf-8-sig")))
    if not rows:
        print("无数据")
        return
    print("\n" + "=" * 70)
    print("=== 三档对比（同一批 11 篇）===")

    # A) 标题+摘要（本实验）
    ta = [float(r["avg"]) for r in rows if r.get("avg")]
    ua_a = [float(r["UA"]) for r in rows if r.get("UA")]
    print(f"A 标题+摘要: avg 跨度={max(ta)-min(ta):.3f} ({min(ta):.3f}~{max(ta):.3f}) "
          f"mean={sum(ta)/len(ta):.3f} | UA 跨度={max(ua_a)-min(ua_a):.2f}")

    # B) 无论文（smoke_nopaper_10 同批）
    if os.path.exists("deliverables/smoke_nopaper_10.csv"):
        np_rows = {r["sid"]: r for r in csv.DictReader(
            open("deliverables/smoke_nopaper_10.csv", encoding="utf-8-sig"))}
    else:
        np_rows = {}
    if np_rows:
        nb = [float(np_rows[s]["avg"]) for s in (r["sid"] for r in rows) if s in np_rows and np_rows[s].get("avg")]
        print(f"B 无论文:     avg 跨度={max(nb)-min(nb):.3f} ({min(nb):.3f}~{max(nb):.3f}) "
              f"mean={sum(nb)/len(nb):.3f}")

    # C) 线上全文（smoke_online_10 同批）
    if os.path.exists("deliverables/smoke_online_10.csv"):
        on_rows = {r["sid"]: r for r in csv.DictReader(
            open("deliverables/smoke_online_10.csv", encoding="utf-8-sig"))}
        oc = [sum(float(on_rows[s][d]) for d in ("UA", "AD", "II", "ES")) / 4
              for s in (r["sid"] for r in rows) if s in on_rows]
        if oc:
            print(f"C 线上全文:   avg 跨度={max(oc)-min(oc):.3f} ({min(oc):.3f}~{max(oc):.3f}) "
                  f"mean={sum(oc)/len(oc):.3f}")

    # 人工
    humans = [float(r["human_total"]) for r in rows if r.get("human_total")]
    print(f"人工金标:    avg 跨度={max(humans)-min(humans):.3f} ({min(humans):.3f}~{max(humans):.3f})")

    # UA 失真检查：标题+摘要 vs 人工 UA 的 MAE
    ua_diffs = [abs(float(r["UA"]) - float(r["human_UA"])) for r in rows
                if r.get("UA") and r.get("human_UA")]
    if ua_diffs:
        print(f"\nUA 失真: 标题+摘要版 vs 人工 UA 的 MAE = {sum(ua_diffs)/len(ua_diffs):.3f} "
              f"({len(ua_diffs)} 篇)")
        for r in sorted(rows, key=lambda x: abs(float(x["UA"]) - float(x["human_UA"])), reverse=True)[:3]:
            print(f"  UA 偏差最大 {r['sid']}: Ornstein-V2={r['UA']} human={r['human_UA']} "
                  f"(Δ{float(r['UA'])-float(r['human_UA']):+.2f})")


if __name__ == "__main__":
    main()
