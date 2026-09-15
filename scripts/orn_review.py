"""Ornstein-V2 全量 41 篇感悟报告评分（vs 人工基准），输出 ornstein_vs_human_full.csv。

【2026-08-13 口径升级】此前版本喂原论文全文 + temperature 0.0 + 单次采样；现对齐
depth_eval_reflection 的生产口径（标题+摘要，见 reflection_pipeline._get_paper_title_abstract）：
  - paper_text = 论文标题 + 摘要（不再喂全文，避免 9B 模型把「论文质量」混为「报告质量」）
  - temperature 0.2（走 compute_mode 默认，经 call_llm → llama-server）
  - II 维中位数采样（PAPERFORGE_REFLECTION_II_SAMPLES 默认 3，不同 seed 取中位数）
  - R1.5 分级帽（证据 2/3/4 → 0.75/0.80/0.85）

输出口径与 recompute_reflection_dim_offsets.py 对齐：orn_* 列是**未经确定性校准层**
的原始系统分（校准偏移就是拿这些值 vs 人工金标拟合的），human_* 列是人工金标。
额外落盘 orn_II_samples（II 原始样本，逗号分隔）、parse_failed、llm_empty 供离线
A/B 与失败审计。增量落盘 + 断点续跑 + ETA。全量重跑前删除旧 CSV 或手动备份。

用法：
  python scripts/orn_review.py [--limit N] [--reset]
    --limit  : 本次最多跑 N 篇（断点续跑，默认 41）
    --reset  : 先删除旧 CSV 再跑（全量重跑）
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

OUT_CSV = "deliverables/ornstein_vs_human_full.csv"
DB = "mock_api/paperforge_mock.db"


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


def _fmt_eta(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def main() -> None:
    from mock_api.depth_eval_reflection import ReflectionReviewer

    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=41)
    ap.add_argument("--reset", action="store_true", help="删除旧 CSV 后全量重跑")
    args = ap.parse_args()

    if args.reset and os.path.exists(OUT_CSV):
        os.remove(OUT_CSV)
        print(f"已删除旧 CSV: {OUT_CSV}")

    gold = list(csv.DictReader(open("deliverables/human_benchmark_full.csv", encoding="utf-8-sig")))
    done = load_done()
    reviewer = ReflectionReviewer()  # 默认 call_llm：temperature 0.2 + II 中位数采样
    todo = [g for g in gold if g["sid"] not in done]
    if not todo:
        print("全部完成")
        return

    block = todo[: args.limit]
    conn = sqlite3.connect(DB)
    durations: list[float] = []
    failures: list[str] = []
    print(f"本次 {len(block)} 篇, 已完成 {len(gold) - len(todo)}/{len(gold)}")
    for i, g in enumerate(block, 1):
        sid = g["sid"]
        cur = conn.cursor()
        cur.execute(
            "SELECT p.id, p.full_text, s.title, s.abstract FROM papers p "
            "JOIN papers s ON s.id = p.source_paper_id WHERE p.id = ?",
            (f"reflection_{sid}",),
        )
        row = cur.fetchone()
        if not row:
            print(f"[skip] {sid} NOT FOUND")
            continue
        rid, rep, title, abstract = row
        # 生产口径：只喂标题+摘要（对齐 reflection_pipeline._get_paper_title_abstract）
        paper_summary = f"论文标题：{title or ''}\n\n论文摘要：{abstract or ''}"
        t0 = time.time()
        try:
            res = reviewer.review(
                paper_id=rid,
                title=sid,
                full_text=rep,
                paper_text=paper_summary,
                paper_supplement="",
            )
            sc = res.scores
            avg = sum(sc.values()) / len(sc) if sc else 0
            elapsed = time.time() - t0
            durations.append(elapsed)
            if res.parse_failed or res.llm_empty:
                failures.append(
                    f"{sid} parse_failed={res.parse_failed} llm_empty={res.llm_empty}"
                )
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
                    "orn_II_samples": ",".join(f"{s:.4f}" for s in res.ii_samples),
                    "parse_failed": 1 if res.parse_failed else 0,
                    "llm_empty": res.llm_empty,
                    "orn_avg": round(avg, 4),
                    "seconds": round(elapsed, 1),
                }
            )
            avg_s = sum(durations) / len(durations)
            eta = _fmt_eta(avg_s * (len(block) - i))
            print(f"[{i}/{len(block)}] {sid} human={g['total']} orn={avg:.3f} "
                  f"(llm_calls={res.llm_calls}, ev={res.effective_evidence_count}, "
                  f"II样本={len(res.ii_samples)}, {elapsed:.0f}s, ETA~{eta})", flush=True)
        except Exception as e:
            print(f"[{i}/{len(block)}] {sid} 失败: {type(e).__name__}: {e}", flush=True)
    conn.close()

    # 失败汇总（用 ASCII 标记，避免 Windows GBK 控制台编码崩溃）
    print("\n" + "=" * 60)
    print(f"完成 {len(block)} 篇。")
    if failures:
        print(f"[WARN] parse_failed / llm_empty 异常的 {len(failures)} 篇：")
        for f in failures:
            print(f"  - {f}")
    else:
        print("[OK] 无 parse_failed / llm_empty 异常。")


if __name__ == "__main__":
    main()
