"""PeerRead 全量金标分层抽样（当前模型 Ornstein-V2 重跑前驱，ADR-011/014 校准升级）。

背景：
  - 现有金标 N=198 太小且分数来自旧模型（2026-08-09 GLM-4.7-Flash 时代 rescore）。
  - 已从 .peerread_cache 构建全量人类金标 deliverables/peerread_gold.json（12,198 篇
    accept/reject）。本脚本按分层随机从 deliverables/peerread_index.json
    （有 accepted + 有 parsed 全文，12,205 篇）抽出 N 篇，供 peerread_rescore.py run 用
    当前模型（llama-server@8080 Ornstein-V2）重新打分，再跑 offset_scan / 阈值扫描。

分层设计（2026-08-18 定稿）：
  - iclr_2017：全部纳入（427 篇；唯一带连续评分 RECOMMENDATION 1-10 的子集，
    能直接提供边界分数，对 0.7/0.8 阈值验证尤其有用）。
  - 其余 venue（arxiv.cs.lg/ai/cl）：按各 venue 人口比例分配名额（最大余数法），
    venue 内 accept/reject 各半（平衡标签，避免 25:75 基率淹没 κ/混淆矩阵）。
  - 固定随机种子，可复现。

输出：deliverables/peerread_sample_<n>_<ts>.json
  字段与 peerread_rescore.py run() 兼容（stem/title/accepted/venue/split/parsed_path），
  另附 human_label / recommendation_score（仅 iclr_2017，取评审 RECOMMENDATION 均值）。

用法：
  .venv/Scripts/python.exe scripts/calibration/sample_peerread_full.py [--n 500] [--seed 20260818]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
IDX_PATH = ROOT / "deliverables" / "peerread_index.json"
CACHE = ROOT / ".peerread_cache"

ICLR_VENUE = "iclr_2017"
ARXIV_VENUES = ["arxiv.cs.lg_2007-2017", "arxiv.cs.ai_2007-2017", "arxiv.cs.cl_2007-2017"]


def load_index() -> list[dict]:
    if not IDX_PATH.exists():
        print(f"[sample_peerread_full] 缺少 {IDX_PATH}，请先运行 peerread_rescore.py index")
        sys.exit(1)
    return json.loads(IDX_PATH.read_text(encoding="utf-8"))


def iclr_recommendation(stem: str) -> float | None:
    """iclr_2017 评审 RECOMMENDATION 均值（1-10），缺失返回 None。"""
    for f in CACHE.glob(f"data/{ICLR_VENUE}/*/reviews/{stem}.json"):
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None
        vals = []
        for r in obj.get("reviews", []):
            if not isinstance(r, dict):
                continue
            v = r.get("RECOMMENDATION")
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
        if vals:
            return round(sum(vals) / len(vals), 2)
        return None
    return None


def largest_remainder(total: int, weights: dict[str, int]) -> dict[str, int]:
    """按 population 比例分配 total 个名额（最大余数法）。"""
    s = sum(weights.values())
    if s == 0:
        return {k: 0 for k in weights}
    alloc = {k: total * w // s for k, w in weights.items()}
    remain = total - sum(alloc.values())
    for k in sorted(weights, key=lambda k: (weights[k] * total / s) - alloc[k], reverse=True):
        if remain <= 0:
            break
        alloc[k] += 1
        remain -= 1
    return alloc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=500, help="总样本量，默认 500")
    ap.add_argument("--seed", type=int, default=20260818, help="随机种子，默认 20260818")
    ap.add_argument("--out", default="", help="输出路径（默认 deliverables/peerread_sample_<n>_<ts>.json）")
    ap.add_argument("--iclr-all", action="store_true", default=True, help="iclr_2017 全量纳入（默认开启）")
    args = ap.parse_args()

    idx = load_index()
    rng = random.Random(args.seed)

    iclr = [j for j in idx if j["venue"] == ICLR_VENUE]
    others = [j for j in idx if j["venue"] in ARXIV_VENUES]
    print(f"[sample_peerread_full] 索引 {len(idx)}：iclr_2017={len(iclr)}，arxiv 三 venue={len(others)}")

    selected: list[dict] = []

    # 1. iclr_2017 全量
    if args.iclr_all:
        for j in iclr:
            rec = dict(j)
            rec["human_label"] = "accept" if j["accepted"] else "reject"
            rec["recommendation_score"] = iclr_recommendation(j["stem"])
            selected.append(rec)
        print(f"[sample_peerread_full] iclr_2017 全量纳入: {len(selected)} 篇")
    else:
        selected = []

    # 2. 其余 venue 按人口比例分配剩余名额
    remain = args.n - len(selected)
    if remain < 0:
        print(f"[sample_peerread_full] 错误：--n({args.n}) < iclr 全量({len(selected)})，请加大样本量")
        sys.exit(1)
    pop = Counter(j["venue"] for j in others)
    alloc = largest_remainder(remain, dict(pop))
    for venue, cnt in alloc.items():
        pool = [j for j in others if j["venue"] == venue]
        acc = [j for j in pool if j["accepted"]]
        rej = [j for j in pool if not j["accepted"]]
        half = cnt // 2
        take_a = rng.sample(acc, min(half, len(acc)))
        take_r = rng.sample(rej, min(cnt - len(take_a), len(rej)))
        # 若某类不足，用另一类补足
        used = {id(j) for j in take_a + take_r}
        for j in rng.sample(pool, len(pool)):
            if len(take_a) + len(take_r) >= cnt or id(j) in used:
                continue
            used.add(id(j))
            (take_a if j["accepted"] else take_r).append(j)
        for j in take_a + take_r:
            rec = dict(j)
            rec["human_label"] = "accept" if j["accepted"] else "reject"
            rec["recommendation_score"] = None
            selected.append(rec)
        print(f"[sample_peerread_full] {venue}: 名额 {cnt}，抽 {len(take_a) + len(take_r)} "
              f"(accept={len(take_a)}/reject={len(take_r)})")

    # 3. 打乱输出顺序（盲评无关，仅避免 venue 聚类）
    rng.shuffle(selected)
    for i, rec in enumerate(selected, 1):
        rec["paper_id"] = f"pr_{rec['stem']}"

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else ROOT / "deliverables" / f"peerread_sample_{args.n}_{ts}.json"
    payload = {
        "sample_id": f"peerread_{args.n}_{ts}",
        "n": len(selected),
        "seed": args.seed,
        "design": "iclr_2017 全量 + arxiv 三 venue 按人口比例分配，venue 内 accept/reject 平衡",
        "papers": selected,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    lbl = Counter(r["human_label"] for r in selected)
    ven = Counter(r["venue"] for r in selected)
    has_rec = sum(1 for r in selected if r.get("recommendation_score") is not None)
    print(f"[sample_peerread_full] 总 {len(selected)} 篇 | accept={lbl.get('accept', 0)} reject={lbl.get('reject', 0)}")
    print(f"[sample_peerread_full] venue: {dict(ven)}")
    print(f"[sample_peerread_full] 带 RECOMMENDATION 连续分: {has_rec} 篇")
    print(f"[sample_peerread_full] 写出 → {out}")


if __name__ == "__main__":
    main()
