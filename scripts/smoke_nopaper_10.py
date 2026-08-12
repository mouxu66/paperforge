"""同一批 10 篇、无论文传参（paper_text=""）跑 4 维 —— 与线上传参版对比区分度。

回答：如果不看原论文，这 10 篇的分数相差大吗？
"""
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
OUT_CSV = "deliverables/smoke_nopaper_10.csv"
DIMS = ("understanding_accuracy", "analysis_depth", "innovative_insights", "evidence_support")


def main() -> None:
    sids = [r["sid"] for r in csv.DictReader(
        open("deliverables/human_benchmark_full.csv", encoding="utf-8-sig"))][:10]
    sids += ["999900000007"]  # 对齐标题+摘要版样本集（补 4 篇极端样本里的 2109）

    gold = {}
    with open("deliverables/human_benchmark_full.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            gold[row["sid"]] = float(row["total"])

    conn = sqlite3.connect(DB)
    results = []
    for sid in sids:
        full_sid = sid if sid.startswith("2023") else f"20230300{sid}"
        cur = conn.cursor()
        cur.execute(
            "SELECT p.id, p.full_text, s.id, s.full_text FROM papers p JOIN papers s ON s.id = p.source_paper_id WHERE p.id = ?",
            (f"reflection_{full_sid}",),
        )
        row = cur.fetchone()
        if not row:
            continue
        rid, rep, _, _ = row
        t0 = time.time()
        # 无论文：paper_text="" 且 paper_supplement=""
        r = ReflectionReviewer().review(
            paper_id=rid, title=sid, full_text=rep, paper_text="", paper_supplement="",
        )
        sc = r.scores
        avg = sum(float(sc.get(d, 0)) for d in DIMS) / 4
        rec = {
            "sid": sid,
            "human": gold.get(full_sid, float("nan")),
            "avg": round(avg, 4),
            "UA": sc.get("understanding_accuracy", ""),
            "AD": sc.get("analysis_depth", ""),
            "II": sc.get("innovative_insights", ""),
            "ES": sc.get("evidence_support", ""),
            "ev": r.effective_evidence_count,
        }
        results.append(rec)
        fresh = not os.path.exists(OUT_CSV) or os.path.getsize(OUT_CSV) == 0
        with open(OUT_CSV, "a", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rec.keys()))
            if fresh:
                w.writeheader()
            w.writerow(rec)
        print(f"  {sid}: UA={sc.get('understanding_accuracy',0):.2f} "
              f"AD={sc.get('analysis_depth',0):.2f} II={sc.get('innovative_insights',0):.2f} "
              f"ES={sc.get('evidence_support',0):.2f} avg={avg:.3f} (human={gold.get(full_sid):.3f}) "
              f"[ev={r.effective_evidence_count} {time.time()-t0:.0f}s]", flush=True)
    conn.close()

    print("\n=== 无论文 10 篇汇总 ===")
    avgs = [x["avg"] for x in results]
    humans = [x["human"] for x in results]
    print(f"avg 跨度: {max(avgs)-min(avgs):.3f} ({min(avgs):.3f}~{max(avgs):.3f}) | "
          f"human 跨度: {max(humans)-min(humans):.3f}")
    mae = sum(abs(x["avg"] - x["human"]) for x in results) / len(results)
    print(f"MAE vs human: {mae:.3f}")

    # 与线上传参版对比
    online = {}
    if os.path.exists("deliverables/smoke_online_10.csv"):
        for r in csv.DictReader(open("deliverables/smoke_online_10.csv", encoding="utf-8-sig")):
            online[r["sid"]] = sum(float(r[d]) for d in ("UA", "AD", "II", "ES")) / 4
        ov = [online[x["sid"]] for x in results if x["sid"] in online]
        print(f"\n对比（同 10 篇）:")
        print(f"  无论文:         avg 跨度={max(avgs)-min(avgs):.3f} mean={sum(avgs)/len(avgs):.3f}")
        print(f"  线上(全文+supp): avg 跨度={max(ov)-min(ov):.3f} mean={sum(ov)/len(ov):.3f}")


if __name__ == "__main__":
    main()
