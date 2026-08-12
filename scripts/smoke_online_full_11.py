"""真实线上完整口径冒烟：调 analyze_reflection_file 处理 11 篇 docx。

输出 6 维加权总分（W 权重：coverage 0.35 / ii 0.35 / ad 0.15 / ua 0.05 / es 0.05 / fid 0.05），
验证改动后线上真实总分的区分度。

用法：
  python scripts/smoke_online_full_11.py [--limit N]
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

DB = "mock_api/paperforge_mock.db"
OUT_CSV = "deliverables/smoke_online_full_11.csv"
DIMS4 = ("understanding_accuracy", "analysis_depth", "innovative_insights", "evidence_support")


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
    sids = base + ["999900000007"]

    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT id, reflection_docx_path FROM papers WHERE id IN (%s)"
                % ",".join("?" * len(sids)), [f"reflection_{s}" for s in sids])
    paths = {r[0].replace("reflection_", ""): r[1] for r in cur.fetchall()}
    conn.close()

    # docx 路径可能指向 Desktop 相对位置，存在则直接用，不存在则按学号前缀重找
    import glob
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

    results = []
    for i, sid in enumerate(sids, 1):
        path = paths.get(sid)
        if not path or not os.path.exists(path):
            print(f"[{i}/{len(sids)}] {sid}: docx 不存在 ({path})", flush=True)
            continue
        db = SessionLocal()
        t0 = time.time()
        try:
            # 完整线上入口：解析 docx + fidelity + coverage + 4 维 LLM + verdict。
            # 不传 source_paper_id：让 pipeline 按 docx 路径反查 DB 里的论文绑定
            # （线上真实行为；学号不是论文 ID，传了反而查不到全文）。
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

    # 汇总
    print("\n" + "=" * 70)
    print("=== 线上完整口径（6 维加权）11 篇 ===")
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
    # coverage 单独看（确定性维度是否有区分度）
    covs = [float(r["coverage"]) for r in results if r.get("coverage")]
    if covs:
        print(f"coverage 跨度: {max(covs)-min(covs):.3f} ({min(covs):.3f}~{max(covs):.3f})")
    fids = [float(r["fidelity"]) for r in results if r.get("fidelity")]
    if fids:
        print(f"fidelity 跨度: {max(fids)-min(fids):.3f} ({min(fids):.3f}~{max(fids):.3f})")


if __name__ == "__main__":
    main()
