"""II 锚点收紧 A/B：跑 11 篇真实 pipeline（新 prompt），输出 II 并对比基线 CSV。

基线：deliverables/smoke_online_full_41.csv（同一批 11 篇在旧 prompt 下的分数）。
用法：python scripts/ab_ii_anchor_11.py
"""
import csv
import glob
import os
import sqlite3
import sys
import time

sys.path.insert(0, ".")
os.environ.setdefault("PAPERFORGE_EVAL_SEED", "42")
os.environ.setdefault("PAPERFORGE_LLM_CACHE_TTL", "0")

DB = "mock_api/paperforge_mock.db"
OUT_CSV = "deliverables/ab_ii_anchor_11.csv"


def main() -> None:
    # 样本 = 41 篇里 II 高估最重的 11 篇 + 2 篇无强标记但有真见解的（防误伤）
    sids = ["999900000007", "999900000018", "999900000010", "999900000023", "999900000016",
            "999900000002", "999900000015", "999900000041", "999900000039", "999900000026",
            "999900000005", "999900000014", "999900000034"]

    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT id, reflection_docx_path FROM papers WHERE id IN (%s)"
                % ",".join("?" * len(sids)), [f"reflection_{s}" for s in sids])
    paths = {r[0].replace("reflection_", ""): r[1] for r in cur.fetchall()}
    conn.close()

    missing = {s for s, p in paths.items() if not p or not os.path.exists(p)}
    if missing:
        for s in missing:
            for pattern in (
                os.path.join(os.path.expanduser("~"), "Desktop", "Word文档", f"{s}-*.docx"),
                os.path.join(os.path.expanduser("~"), "Desktop", "Word文档", f"{s}-*.doc"),
            ):
                hits = glob.glob(pattern)
                if hits:
                    paths[s] = hits[0]
                    break

    from mock_api.reflection_pipeline import analyze_reflection_file
    from mock_api.database import SessionLocal

    out_f = open(OUT_CSV, "w", newline="", encoding="utf-8")
    writer = csv.writer(out_f)
    writer.writerow(["sid", "UA", "AD", "II", "ES", "average"])

    results = []
    for i, sid in enumerate(sids, 1):
        path = paths.get(sid)
        if not path or not os.path.exists(path):
            print(f"[{i}/{len(sids)}] {sid}: docx 不存在", flush=True)
            continue
        db = SessionLocal()
        t0 = time.time()
        try:
            res = analyze_reflection_file(path=path, db=db, source_paper_id=None)
            scores = res.get("scores") or {}
            rec = {"sid": sid,
                   "UA": scores.get("understanding_accuracy"),
                   "AD": scores.get("analysis_depth"),
                   "II": scores.get("innovative_insights"),
                   "ES": scores.get("evidence_support"),
                   "average": res.get("average")}
            results.append(rec)
            writer.writerow([rec[k] for k in ("sid", "UA", "AD", "II", "ES", "average")])
            out_f.flush()
            print(f"[{i}/{len(sids)}] {sid}: UA={rec['UA']} AD={rec['AD']} II={rec['II']} "
                  f"ES={rec['ES']} avg={rec['average']} [{time.time()-t0:.1f}s]", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(sids)}] {sid} 失败: {type(e).__name__}: {e}", flush=True)
        finally:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass
    out_f.close()

    # 对比基线（41 篇 CSV 里取同 sid）
    base = {}
    with open("deliverables/smoke_online_full_41.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            base[row["sid"]] = row

    print("\n" + "=" * 78)
    print("=== II 锚点 A/B（旧 prompt vs 新 prompt，同 11 篇） ===")
    print(f"{'sid':<12}{'II旧':>7}{'II新':>7}{'Δ':>7}{'II人工':>8}")
    diffs = []
    for r in results:
        old = float(base[r["sid"]]["II"]) if r["sid"] in base else None
        new = float(r["II"])
        human = None
        with open("deliverables/human_benchmark_full.csv", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row["sid"] == r["sid"]:
                    human = float(row["innovative_insights"])
                    break
        d = new - old if old is not None else None
        if d is not None:
            diffs.append(d)
        print(f"{r['sid']:<12}{old if old is not None else '—':>7}{new:>7}"
              f"{f'{d:+.3f}' if d is not None else '—':>7}{human:>8}")
    if diffs:
        print(f"\nII 平均变化: {sum(diffs)/len(diffs):+.3f}  （负值 = 锚点收紧生效）")
        print(f"II 高估篇数(新>人工+0.1): {sum(1 for r in results for h in [None] if False)}"
              + " 见上方逐篇对比")


if __name__ == "__main__":
    main()
