"""金标扩样候选采样器 —— 本地边界样本筛选（ADR-017 追加条款 1）。

用途：
  从全库已完成 v4.2 论文评审（depth_reviews_v4，kind='paper'，status='completed'）中
  筛出 local_score（final_verdict.calibrated_score，口径与 scripts/calibration/sentry_dryrun.py 一致）
  落在 [--min, --max] 区间的论文，导出候选清单供后续人工盲评。

  背景：两例边际案例验证了本地在 accept 阈值 0.8 附近存在系统性高估（ADR-017），
  金标扩样 [0.75, 0.85] 区间列为最高优先级，优先覆盖「叙述清晰、实证薄弱」的论文。

  每篇论文取最近一次 completed 评审；附带 title/venue/year/文本长度等元数据，
  并标记 in_gold（是否已命中 PeerRead 金标或 calib_papers/runs 金标），
  便于优先选择未被金标覆盖的新样本。

只读：不写任何主表。输出 deliverables/boundary_expansion_candidates_<ts>.{csv,json}。

用法：
  候选导出：
    .venv/Scripts/python.exe scripts/calibration/sample_boundary_expansion.py \
        [--min 0.75] [--max 0.85] [--kind paper] [--limit N] [--shuffle] [--out PATH]
  盲评批次：
    .venv/Scripts/python.exe scripts/calibration/sample_boundary_expansion.py \
        --blind-batch 20 --seed 20260818

--shuffle：打乱顺序并生成 review_order（盲评顺序，避免按分数排序引入顺序偏差）；
          分数列仍保留在输出中，供盲评结束后解开对照。
--blind-batch N：从候选池（排除已被金标覆盖 + 已人工判定的种子案例）中按 local_score
          分层随机抽 N 篇，产出盲评清单（calib_papers/blind_batch_<ts>.jsonl，仅含
          blind_order/paper_id/title/abstract/category/source，**不含任何分数**）与
          盲评密钥（deliverables/blind_batch_<ts>_key.json，含分数映射，供盲评后解开）。
          分层：按 local_score 分 [0.75,0.78) / [0.78,0.82) / [0.82,0.85] 三层，
          按各层候选比例分配名额（最大余数法），层内随机抽样（固定 --seed 可复现）。
          已人工判定案例默认排除：pr_1406.1765 / pr_1605.02442（ADR-017，避免污染盲评独立性）。
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mock_api.database import SessionLocal  # noqa: E402
from mock_api.models import DepthReviewV4, Paper  # noqa: E402

# 已人工判定的种子案例（ADR-017），盲评批次默认排除，避免污染评价独立性
DEFAULT_EXCLUDED = {"pr_1406.1765", "pr_1605.02442"}

# 盲评分层（local_score 半开区间；顶层含上界）
_BLIND_LAYERS: list[tuple[float, float, bool]] = [
    (0.75, 0.78, False),
    (0.78, 0.82, False),
    (0.82, 0.85, True),
]

OUT_TS = datetime.now().strftime("%Y%m%d_%H%M%S")


def parse_final_verdict(fv) -> tuple[float | None, str | None]:
    """从 final_verdict JSON 列取 (calibrated_score, final_verdict)，口径同 sentry_dryrun。"""
    if isinstance(fv, str):
        try:
            d = json.loads(fv)
        except Exception:
            return None, None
    else:
        d = fv or {}
    cs = d.get("calibrated_score")
    if cs is None:
        return None, None
    try:
        return float(cs), d.get("final_verdict")
    except (TypeError, ValueError):
        return None, None


def load_gold_sets() -> set[str]:
    """汇总已覆盖的金标 pid：PeerRead 金标 + calib_papers/runs 金标。"""
    gold: set[str] = set()

    peerread = PROJECT_ROOT / "deliverables" / "gold" / "peerread_verdict_gold.json"
    if peerread.exists():
        try:
            data = json.loads(peerread.read_text(encoding="utf-8"))
            for s in data.get("samples", []):
                if isinstance(s, dict) and s.get("paper_id"):
                    gold.add(str(s["paper_id"]))
        except Exception:
            pass

    runs_dir = PROJECT_ROOT / "calib_papers" / "runs"
    if runs_dir.exists():
        for p in runs_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            for row in data:
                if isinstance(row, dict) and row.get("pid"):
                    gold.add(str(row["pid"]))
    return gold


def fetch_latest_reviews(kind: str, lo: float, hi: float):
    """每篇论文取最近一次 completed 评审，过滤 local_score ∈ [lo, hi]。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(
                DepthReviewV4.paper_id,
                DepthReviewV4.final_verdict,
                DepthReviewV4.completed_at,
            )
            .filter(
                DepthReviewV4.kind == kind,
                DepthReviewV4.status == "completed",
                DepthReviewV4.final_verdict.isnot(None),
            )
            .order_by(DepthReviewV4.completed_at.asc())
            .all()
        )
        papers = {p.id: p for p in db.query(Paper).all()}
    finally:
        db.close()

    # 每篇保留最近一次 completed
    latest: dict[str, tuple] = {}
    for pid, fv, completed_at in rows:
        cs, verdict = parse_final_verdict(fv)
        if cs is None:
            continue
        latest[pid] = (cs, verdict, completed_at)

    out = []
    for pid, (cs, verdict, completed_at) in latest.items():
        if not (lo <= cs <= hi):
            continue
        p = papers.get(pid)
        out.append(
            {
                "pid": pid,
                "title": (p.title if p else None) or "",
                "journal": (p.journal if p else None) or "",
                "source": (p.source if p else None) or "",
                "year": (p.year if p else None),
                "local_score": cs,
                "local_verdict": verdict,
                "abstract": (p.abstract if p else "") or "",
                "abstract_chars": len((p.abstract or "") if p else ""),
                "text_chars": len((p.full_text or "") if p else ""),
                "reviewed_at": completed_at.isoformat() if completed_at else "",
            }
        )
    return out


def run_blind_batch(rows: list[dict], n: int, seed: int, excluded: set[str], kind: str) -> None:
    """分层随机抽 N 篇产出盲评清单（不含分数）与盲评密钥（含分数映射）。"""
    pool = [r for r in rows if not r["in_gold"] and r["pid"] not in excluded]
    if n > len(pool):
        print(f"[blind_batch] 错误：候选池仅 {len(pool)} 篇（已排除金标覆盖与种子案例），不足 {n} 篇")
        sys.exit(1)

    # ── 分层 + 最大余数法分配名额 ──
    buckets: dict[tuple, list[dict]] = {}
    for lo, hi, inc in _BLIND_LAYERS:
        if inc:
            b = [r for r in pool if lo <= r["local_score"] <= hi]
        else:
            b = [r for r in pool if lo <= r["local_score"] < hi]
        buckets[(lo, hi)] = b
    buckets = {k: v for k, v in buckets.items() if v}  # 空层剔除

    total = len(pool)
    quota = {k: n * len(v) / total for k, v in buckets.items()}
    alloc = {k: int(q) for k, q in quota.items()}
    # 名额不足时先按层内候选数钳制，再按最大余数补足
    for k in alloc:
        alloc[k] = min(alloc[k], len(buckets[k]))
    remain = n - sum(alloc.values())
    for k in sorted(quota, key=lambda k: quota[k] - alloc[k], reverse=True):
        if remain <= 0:
            break
        free = len(buckets[k]) - alloc[k]
        take = min(free, remain)
        alloc[k] += take
        remain -= take
    if remain > 0:
        print(f"[blind_batch] 警告：{remain} 个名额无法分配（候选不足），实际抽 {n - remain} 篇")
        n -= remain

    rng = random.Random(seed)
    picked: list[dict] = []
    alloc_detail: list[dict] = []
    for (lo, hi), cnt in alloc.items():
        if cnt <= 0:
            continue
        chosen = rng.sample(buckets[(lo, hi)], cnt)
        picked.extend(chosen)
        alloc_detail.append(
            {"layer": [lo, hi], "pool": len(buckets[(lo, hi)]), "picked": cnt,
             "pids": [r["pid"] for r in chosen]}
        )
    rng.shuffle(picked)  # 最终盲评顺序再打乱一次，避免层序泄漏
    for i, r in enumerate(picked, 1):
        r["blind_order"] = i

    # ── 盲评清单：不含任何分数/verdict/置信度 ──
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    blind_path = PROJECT_ROOT / "calib_papers" / f"blind_batch_{ts}.jsonl"
    with blind_path.open("w", encoding="utf-8") as f:
        for r in picked:
            rec = {
                "blind_order": r["blind_order"],
                "paper_id": r["pid"],
                "title": r["title"],
                "abstract": r["abstract"],
                "category": kind,
                "source": r["source"],
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ── 盲评密钥（盲评结束后解开用，与清单分离存放）──
    key_path = PROJECT_ROOT / "deliverables" / f"blind_batch_{ts}_key.json"
    key_payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "seed": seed,
        "n": len(picked),
        "excluded": sorted(excluded),
        "allocation": alloc_detail,
        "samples": [
            {"blind_order": r["blind_order"], "paper_id": r["pid"],
             "local_score": r["local_score"], "local_verdict": r["local_verdict"]}
            for r in picked
        ],
    }
    key_path.write_text(json.dumps(key_payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"[blind_batch] 候选池={total}（排除金标覆盖 + 种子案例后） 抽取={len(picked)}")
    for d in alloc_detail:
        print(f"[blind_batch]   层 {d['layer'][0]}–{d['layer'][1]}：池 {d['pool']} 抽 {d['picked']}")
    print(f"[blind_batch] 盲评清单（不含分数）→ {blind_path}")
    print(f"[blind_batch] 盲评密钥（含分数，盲评后解开）→ {key_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--min", type=float, default=0.75, help="local_score 下界（含），默认 0.75")
    ap.add_argument("--max", type=float, default=0.85, help="local_score 上界（含），默认 0.85")
    ap.add_argument("--kind", default="paper", choices=["paper", "report"], help="评审类型，默认 paper")
    ap.add_argument("--limit", type=int, default=0, help="只输出前 N 条（0=全部）")
    ap.add_argument("--shuffle", action="store_true", help="打乱顺序并生成盲评 review_order")
    ap.add_argument("--seed", type=int, default=42, help="--shuffle 随机种子")
    ap.add_argument("--out", default="", help="输出文件前缀（自动拼 _<ts>.csv / .json），默认 deliverables/boundary_expansion_candidates")
    ap.add_argument("--blind-batch", type=int, default=0, metavar="N", help="盲评批次大小（分层随机抽 N 篇，产出不含分数的盲评清单）")
    ap.add_argument("--exclude", default="", help="额外排除的 pid（逗号分隔）；盲评批次默认还排除已判定种子案例")
    args = ap.parse_args()

    if args.min > args.max:
        print(f"[sample_boundary] 错误：--min({args.min}) > --max({args.max})")
        sys.exit(1)

    gold = load_gold_sets()
    rows = fetch_latest_reviews(args.kind, args.min, args.max)
    for r in rows:
        r["in_gold"] = r["pid"] in gold

    if args.blind_batch > 0:
        excluded = set(DEFAULT_EXCLUDED) | {s.strip() for s in args.exclude.split(",") if s.strip()}
        return run_blind_batch(rows, args.blind_batch, args.seed, excluded, args.kind)

    rows.sort(key=lambda r: r["local_score"], reverse=True)
    if args.shuffle:
        rng = random.Random(args.seed)
        rng.shuffle(rows)
        for i, r in enumerate(rows, 1):
            r["review_order"] = i

    if args.limit > 0:
        rows = rows[: args.limit]

    prefix = args.out or str(PROJECT_ROOT / "deliverables" / "boundary_expansion_candidates")
    csv_path = Path(f"{prefix}_{OUT_TS}.csv")
    json_path = Path(f"{prefix}_{OUT_TS}.json")

    fields = ["review_order", "pid", "title", "journal", "year", "local_score",
              "local_verdict", "in_gold", "abstract_chars", "text_chars", "reviewed_at"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "kind": args.kind,
        "score_range": [args.min, args.max],
        "shuffled": bool(args.shuffle),
        "n_candidates": len(rows),
        "n_in_gold": sum(1 for r in rows if r["in_gold"]),
        "candidates": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    in_gold = sum(1 for r in rows if r["in_gold"])
    print(f"[sample_boundary] kind={args.kind} score∈[{args.min},{args.max}] 候选={len(rows)} 已覆盖金标={in_gold} 新增={len(rows) - in_gold}")
    print(f"[sample_boundary] CSV → {csv_path}")
    print(f"[sample_boundary] JSON → {json_path}")


if __name__ == "__main__":
    main()
