"""P2 影子哨兵 —— 全库 dry-run 触发率统计（影子模式，不写主表）。

用途：
  对全库已完成 v4.2 论文评审（depth_reviews_v4，kind='paper'，status='completed'）
  逐篇离线调用 GLM 第二评审（仅读 abstract+full_text 头部），复用生产代码路径
  run_second_opinion（override 强制关闭 = P0 影子态），统计：

    1. 触发 cloud < SENTRY_CLOUD_LOW(0.5) 且 local >= SENTRY_LOCAL_HIGH(0.7) 的篇数；
    2. 触发样本的 gold_score（若命中盲评金标）分布；
    3. 触发样本的本地 verdict 分布；
    4. GLM 云端分的整体分布（验证 cloud<0.5 是否在大库上几乎不出现）。

结果只写入 deliverables/sentry_dryrun_<ts>.json（不写主表），供阈值调整决策。

用法：
  .venv/Scripts/python.exe scripts/calibration/sentry_dryrun.py [--workers 3] [--limit N] [--out PATH]
可重复运行：已完成的 pid 会跳过（断点续跑）。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import mock_api.second_opinion as so  # noqa: E402
from mock_api.database import SessionLocal  # noqa: E402
from mock_api.models import DepthReviewV4, Paper  # noqa: E402


def load_gold_map() -> dict[str, float]:
    """汇总 calib_papers/runs 下所有含 (pid, my_final) 的金标，返回 {pid: gold_score}。"""
    gold: dict[str, float] = {}
    runs_dir = PROJECT_ROOT / "calib_papers" / "runs"
    if not runs_dir.exists():
        return gold
    for p in runs_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for row in data:
            if not isinstance(row, dict):
                continue
            pid = row.get("pid")
            g = row.get("my_final")
            if pid and isinstance(g, (int, float)):
                gold[str(pid)] = float(g)
    return gold


def fetch_reviews():
    """返回 [(pid, local_score, local_verdict, text)]。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(DepthReviewV4.paper_id, DepthReviewV4.final_verdict)
            .filter(
                DepthReviewV4.kind == "paper",
                DepthReviewV4.status == "completed",
                DepthReviewV4.final_verdict.isnot(None),
            )
            .all()
        )
        papers = {p.id: (p.abstract or "", p.full_text or "") for p in db.query(Paper).all()}
    finally:
        db.close()

    out = []
    for pid, fv in rows:
        # final_verdict 是 JSON 列，ORM 已解析为 dict；仅当仍是 str 时才 loads
        if isinstance(fv, str):
            try:
                d = json.loads(fv)
            except Exception:
                continue
        else:
            d = fv or {}
        cs = d.get("calibrated_score")
        if cs is None:
            continue
        try:
            cs = float(cs)
        except Exception:
            continue
        abstract, full = papers.get(pid, ("", ""))
        text = (abstract or "") + "\n\n" + (full or "")
        out.append((pid, cs, d.get("final_verdict"), text))
    return out


def evaluate_one(item):
    pid, local, local_verdict, text = item
    t0 = time.time()
    try:
        out = so.run_second_opinion(
            text,
            primary_score=local,
            primary_verdict=local_verdict,
            kind="paper",
        )
    except Exception as e:  # noqa: BLE001 - 单篇失败不影响整体
        return {
            "pid": pid,
            "local_score": local,
            "local_verdict": local_verdict,
            "error": str(e)[:200],
            "elapsed": round(time.time() - t0, 2),
        }
    return {
        "pid": pid,
        "local_score": local,
        "local_verdict": local_verdict,
        "enabled": out.get("enabled"),
        "cloud_score": out.get("cloud_shadow_score"),
        "sentry_flag": out.get("sentry_flag", False),
        "needs_human_review": out.get("needs_human_review", False),
        "resolved_score": out.get("resolved_score"),
        "second_verdict": out.get("second_verdict"),
        "flag": out.get("flag"),
        "elapsed": round(time.time() - t0, 2),
    }


def summarize(records, gold, args):
    total = len(records)
    ok = [r for r in records if r.get("enabled")]
    skipped = [r for r in records if not r.get("enabled") and "error" not in r]
    errored = [r for r in records if "error" in r]
    cloud_vals = [r["cloud_score"] for r in ok if isinstance(r.get("cloud_score"), (int, float))]
    n_local_ge = sum(1 for r in ok if (r.get("local_score") or 0) >= so.SENTRY_LOCAL_HIGH)
    triggered = [r for r in ok if r.get("sentry_flag")]
    n_cloud_lt = sum(1 for v in cloud_vals if v < so.SENTRY_CLOUD_LOW)

    def dist(vals):
        if not vals:
            return {}
        vals_sorted = sorted(vals)
        q = statistics.quantiles(vals, n=4) if len(vals) >= 4 else vals_sorted
        return {
            "n": len(vals),
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
            "mean": round(statistics.mean(vals), 4),
            "median": round(statistics.median(vals), 4),
            "q1": round(q[0], 4) if len(vals) >= 4 else round(vals_sorted[0], 4),
            "q3": round(q[2], 4) if len(vals) >= 4 else round(vals_sorted[-1], 4),
        }

    # 直方图（10 桶）
    buckets = Counter()
    for v in cloud_vals:
        b = min(9, int(v * 10))
        buckets[b] += 1
    hist = {f"[{b/10:.1f},{(b+1)/10:.1f})": buckets.get(b, 0) for b in range(10)}

    # 触发样本：gold / 本地 verdict 分布
    trig_gold = [gold.get(r["pid"]) for r in triggered if r["pid"] in gold]
    trig_local_verdict = Counter(r.get("local_verdict") for r in triggered)
    trig_detail = [
        {
            "pid": r["pid"],
            "local_score": r["local_score"],
            "cloud_score": r["cloud_score"],
            "local_verdict": r.get("local_verdict"),
            "second_verdict": r.get("second_verdict"),
            "gold_score": gold.get(r["pid"]),
        }
        for r in triggered
    ]

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "thresholds": {
            "SENTRY_CLOUD_LOW": so.SENTRY_CLOUD_LOW,
            "SENTRY_LOCAL_HIGH": so.SENTRY_LOCAL_HIGH,
        },
        "totals": {
            "reviews_evaluated": total,
            "glm_enabled_ok": len(ok),
            "glm_skipped_no_text_or_disabled": len(skipped),
            "glm_errored": len(errored),
            "local_ge_threshold": n_local_ge,
        },
        "trigger": {
            "n_triggered": len(triggered),
            "rate_over_all_evaluated": round(len(triggered) / total, 4) if total else 0,
            "rate_over_local_high": round(len(triggered) / n_local_ge, 4) if n_local_ge else 0,
        },
        "cloud_score_distribution": dist(cloud_vals),
        "n_cloud_lt_threshold": n_cloud_lt,
        "cloud_histogram": hist,
        "triggered_gold_distribution": dist(trig_gold),
        "triggered_local_verdict_distribution": dict(trig_local_verdict),
        "triggered_detail": trig_detail,
        "gold_overlap": {
            "evaluable_in_gold": sum(1 for r in ok if r["pid"] in gold),
            "gold_coverage": len(gold),
        },
    }
    return summary, trig_detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0, help="仅评估前 N 篇（调试用）")
    ap.add_argument("--out", type=str, default="")
    args = ap.parse_args()

    # 影子模式：强制开启 GLM 调用、强制关闭 override（= P0 生产意图）
    so.is_enabled = lambda: True
    so.override_enabled = lambda: False

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = Path(args.out) if args.out else (
        PROJECT_ROOT / "deliverables" / f"sentry_dryrun_{ts}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    gold = load_gold_map()
    reviews = fetch_reviews()
    if args.limit:
        reviews = reviews[: args.limit]

    # 断点续跑：读取已有结果
    done: dict[str, dict] = {}
    if out_path.exists():
        try:
            prev = json.loads(out_path.read_text(encoding="utf-8"))
            for r in prev.get("records", []):
                done[r["pid"]] = r
        except Exception:
            pass

    todo = [r for r in reviews if r[0] not in done]
    print(
        f"[sentry_dryrun] 评审总数={len(reviews)} 已完={len(done)} 待跑={len(todo)} "
        f"workers={args.workers} gold覆盖={len(gold)}",
        flush=True,
    )

    if todo:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(evaluate_one, it): it[0] for it in todo}
            finished = 0
            for fut in as_completed(futs):
                r = fut.result()
                done[r["pid"]] = r
                finished += 1
                if finished % 25 == 0 or finished == len(todo):
                    print(
                        f"[sentry_dryrun] 进度 {finished}/{len(todo)} "
                        f"最近={r['pid']} cloud={r.get('cloud_score')} sentry={r.get('sentry_flag')}",
                        flush=True,
                    )
                # 周期性落盘（容错）
                if finished % 50 == 0:
                    out_path.write_text(
                        json.dumps({"records": list(done.values())}, ensure_ascii=False),
                        encoding="utf-8",
                    )

    records = list(done.values())
    summary, _ = summarize(records, gold, args)
    payload = {"summary": summary, "records": records}
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    t = summary["trigger"]
    print(
        f"[sentry_dryrun] 完成 → {out_path}\n"
        f"  评估={summary['totals']['reviews_evaluated']} "
        f"本地>=0.7={summary['totals']['local_ge_threshold']} "
        f"触发={t['n_triggered']} "
        f"触发率(全库)={t['rate_over_all_evaluated']} "
        f"触发率(本地高)={t['rate_over_local_high']}\n"
        f"  GLM云端分<0.5 的篇数={summary['n_cloud_lt_threshold']} "
        f"云端分中位={summary['cloud_score_distribution'].get('median')}",
        flush=True,
    )


if __name__ == "__main__":
    main()
