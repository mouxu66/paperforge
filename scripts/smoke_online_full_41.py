"""真实线上完整口径全量跑：调 analyze_reflection_file 处理全部 41 篇 docx。

与 smoke_online_full_11.py 同构，唯一差异：样本 = human_benchmark_full.csv 全部 41 篇。
输出 6 维加权总分（coverage 0.35 / ii 0.35 / ad 0.15 / ua 0.05 / es 0.05 / fid 0.05），
验证改动后（Ornstein 只喂标题+摘要 + 真语义嵌入）线上真实总分的区分度与 MAE。

用法：
  python scripts/smoke_online_full_41.py [--limit N]
"""
import argparse
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
OUT_CSV = "deliverables/smoke_online_full_41.csv"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=41)
    args = ap.parse_args()

    gold = {}
    with open("deliverables/human_benchmark_full.csv", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            gold[row["sid"]] = row
    sids = list(gold.keys())[: args.limit]

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
    writer.writerow(["sid", "human_total", "UA", "AD", "II", "ES", "fidelity", "coverage",
                     "average", "verdict", "seconds"])

    results = []
    for i, sid in enumerate(sids, 1):
        path = paths.get(sid)
        if not path or not os.path.exists(path):
            print(f"[{i}/{len(sids)}] {sid}: docx 不存在 ({path})", flush=True)
            continue
        db = SessionLocal()
        t0 = time.time()
        try:
            res = analyze_reflection_file(path=path, db=db, source_paper_id=None)
            scores = res.get("scores") or {}
            avg = res.get("average")
            rec = {
                "sid": sid,
                "human_total": gold.get(sid, {}).get("total", ""),
                "UA": scores.get("understanding_accuracy"),
                "AD": scores.get("analysis_depth"),
                "II": scores.get("innovative_insights"),
                "ES": scores.get("evidence_support"),
                "fidelity": scores.get("fidelity"),
                "coverage": scores.get("coverage"),
                "average": avg,
                "verdict": res.get("verdict"),
                "seconds": round(time.time() - t0, 1),
            }
            results.append(rec)
            writer.writerow([rec[k] for k in
                             ("sid", "human_total", "UA", "AD", "II", "ES", "fidelity",
                              "coverage", "average", "verdict", "seconds")])
            out_f.flush()
            print(f"[{i}/{len(sids)}] {sid}: UA={rec['UA']} AD={rec['AD']} II={rec['II']} "
                  f"ES={rec['ES']} fid={rec['fidelity']} cov={rec['coverage']} "
                  f"avg={avg} (human={rec['human_total']}) [{rec['seconds']}s]", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[{i}/{len(sids)}] {sid} 失败: {type(e).__name__}: {e}", flush=True)
        finally:
            try:
                db.close()
            except Exception:  # noqa: BLE001
                pass

    out_f.close()

    # 汇总
    print("\n" + "=" * 70)
    print("=== 线上完整口径（6 维加权）全量 ===")
    avgs = [float(r["average"]) for r in results if r.get("average")]
    humans = [float(r["human_total"]) for r in results if r.get("human_total")]
    if avgs:
        print(f"线上总分 avg 跨度: {max(avgs)-min(avgs):.3f} ({min(avgs):.3f}~{max(avgs):.3f})")
    if humans:
        print(f"人工金标   跨度: {max(humans)-min(humans):.3f} ({min(humans):.3f}~{max(humans):.3f})")
    if avgs and humans:
        mae = sum(abs(float(r["average"]) - float(r["human_total"])) for r in results
                  if r.get("average") and r.get("human_total")) / len(results)
        print(f"MAE vs human: {mae:.3f}")
    covs = [float(r["coverage"]) for r in results if r.get("coverage")]
    if covs:
        print(f"coverage 跨度: {max(covs)-min(covs):.3f} ({min(covs):.3f}~{max(covs):.3f})")
    fids = [float(r["fidelity"]) for r in results if r.get("fidelity")]
    if fids:
        print(f"fidelity 跨度: {max(fids)-min(fids):.3f} ({min(fids):.3f}~{max(fids):.3f})")


if __name__ == "__main__":
    main()
