"""PeerRead 校准脚本（阶段1：verdict 二分类一致性）。

流程：
  1. 遍历 .peerread_cache/data/**/reviews/*.json，提取 {id, title, accepted, conference, venue, split}
     -> 输出 deliverables/peerread_gold.json
  2. 加载 deliverables/depth_scores_415.json（DEPTH v4 基准，414 篇）
  3. 归一化标题精确匹配（双向），输出匹配子集
  4. 计算 DEPTH verdict vs PeerRead accepted 的二分类指标：
     - 严格：DEPTH accept vs accepted
     - 宽松：DEPTH accept/minor vs accepted
     - Cohen's κ、准确率、混淆矩阵、Youden J、McNemar(近似)

仅用标准库。运行：
  python scripts/peerread_calibrate.py --peerread .peerread_cache --build-gold
  python scripts/peerread_calibrate.py --peerread .peerread_cache --match
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD_PATH = ROOT / "deliverables" / "peerread_gold.json"
DEPTH_PATH = ROOT / "deliverables" / "depth_scores_415.json"
MATCH_PATH = ROOT / "deliverables" / "peerread_depth_match.json"


def norm_title(t: str | None) -> str:
    if not t:
        return ""
    t = t.lower()
    t = t.replace("’", "'").replace("“", '"').replace("”", '"')
    # 去标点、压缩空白
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def build_gold(peerread_dir: Path) -> list[dict]:
    papers: list[dict] = []
    seen = set()
    rev_files = sorted(peerread_dir.rglob("reviews/*.json"))
    print(f"[gold] 扫描 reviews JSON: {len(rev_files)} 个文件")
    for fp in rev_files:
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        # venue/split 从路径推断
        parts = fp.parts
        # .../data/<venue>/<split>/reviews/<id>.json
        venue = splitn = ""
        for i, p in enumerate(parts):
            if p == "data" and i + 1 < len(parts):
                venue = parts[i + 1]
            if p in ("train", "test", "dev") and i + 1 < len(parts) and parts[i + 1] == "reviews":
                splitn = p
        pid = obj.get("id") or fp.stem
        title = (obj.get("title") or "").strip()
        accepted = obj.get("accepted")
        if isinstance(accepted, str):
            accepted = accepted.strip().lower() in ("true", "1", "yes")
        nt = norm_title(title)
        key = (venue, nt) if nt else (venue, pid)
        if key in seen:
            continue
        seen.add(key)
        papers.append({
            "id": pid,
            "title": title,
            "title_norm": nt,
            "accepted": bool(accepted) if accepted is not None else None,
            "conference": obj.get("conference", ""),
            "venue": venue,
            "split": splitn,
        })
    print(f"[gold] 去重后论文数: {len(papers)}")
    acc = [p for p in papers if p["accepted"] is not None]
    print(f"[gold] 含 accepted 标记: {len(acc)} (accept={sum(1 for p in acc if p['accepted'])} / reject={sum(1 for p in acc if not p['accepted'])})")
    return papers


def cohen_kappa(a: list[int], b: list[int]) -> float:
    """a,b 为 0/1 列表，计算 Cohen's kappa。"""
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pa = sum(a) / n
    pb = sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def confusion(a: list[int], b: list[int]) -> dict:
    """行=DEPTH(0/1)，列=PeerRead(0/1)。tp=DEPTH1&PR1..."""
    tp = sum(1 for x, y in zip(a, b) if x == 1 and y == 1)
    fn = sum(1 for x, y in zip(a, b) if x == 1 and y == 0)
    fp = sum(1 for x, y in zip(a, b) if x == 0 and y == 1)
    tn = sum(1 for x, y in zip(a, b) if x == 0 and y == 0)
    n = len(a)
    acc = (tp + tn) / n if n else 0.0
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    youden = (tp / n if n else 0) + (tn / n if n else 0) - 1
    # McNemar 近似 (|b-c|-1)^2/(b+c)
    if (fp + fn) > 0:
        mcn = (abs(fp - fn) - 1) ** 2 / (fp + fn)
    else:
        mcn = 0.0
    return {
        "n": n, "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "accuracy": round(acc, 4), "precision": round(prec, 4),
        "recall": round(rec, 4), "f1": round(f1, 4),
        "youden_j": round(youden, 4), "mcnemar_chi2": round(mcn, 4),
    }


def match(peerread_dir: Path, gold: list[dict] | None = None) -> dict:
    if gold is None:
        gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    depth = json.loads(DEPTH_PATH.read_text(encoding="utf-8"))
    gold_by_norm = {}
    for p in gold:
        if p["title_norm"]:
            gold_by_norm.setdefault(p["title_norm"], p)
    depth_by_norm = {}
    for d in depth:
        nt = norm_title(d.get("title"))
        if nt:
            depth_by_norm.setdefault(nt, d)

    matched = []
    used_gold = set()
    for d in depth:
        nt = norm_title(d.get("title"))
        g = gold_by_norm.get(nt) if nt else None
        if g and id(g) not in used_gold:
            used_gold.add(id(g))
            matched.append({
                "depth_id": d["id"], "depth_title": d.get("title"),
                "depth_verdict": d.get("verdict"), "depth_score": d.get("calibrated_score"),
                "peerread_id": g["id"], "peerread_title": g["title"],
                "peerread_accepted": g["accepted"], "peerread_venue": g["venue"],
            })
    print(f"[match] DEPTH 基准 {len(depth)} 篇 -> 命中 PeerRead {len(matched)} 篇 "
          f"(匹配率 {len(matched)/len(depth)*100:.1f}%)")

    # 校准
    def to_bin(verdict: str, accept_set) -> int:
        return 1 if verdict in accept_set else 0

    results = {"n_matched": len(matched), "matched": matched, "metrics": {}}
    if matched:
        acc_arr = [1 if m["peerread_accepted"] else 0 for m in matched]
        # 严格：DEPTH accept vs accepted
        strict = [to_bin(m["depth_verdict"], {"accept"}) for m in matched]
        # 宽松：DEPTH accept/minor vs accepted
        loose = [to_bin(m["depth_verdict"], {"accept", "minor_revision"}) for m in matched]
        results["metrics"]["strict_accept_only"] = {
            "kappa": round(cohen_kappa(strict, acc_arr), 4),
            **confusion(strict, acc_arr),
        }
        results["metrics"]["loose_accept_minor"] = {
            "kappa": round(cohen_kappa(loose, acc_arr), 4),
            **confusion(loose, acc_arr),
        }
        # DEPTH 分数 vs accepted 的 AUC 代理（点二列相关）
        scores = [m["depth_score"] for m in matched]
        results["metrics"]["depth_score_vs_accepted"] = {
            "mean_score_accepted": round(sum(s for s, a in zip(scores, acc_arr) if a) / max(1, sum(acc_arr)), 4),
            "mean_score_rejected": round(sum(s for s, a in zip(scores, acc_arr) if not a) / max(1, len(acc_arr) - sum(acc_arr)), 4),
        }
        print("[match] 严格(accept vs accepted):", results["metrics"]["strict_accept_only"])
        print("[match] 宽松(accept/minor vs accepted):", results["metrics"]["loose_accept_minor"])
    MATCH_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[match] 写出 {MATCH_PATH}")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--peerread", default=".peerread_cache")
    ap.add_argument("--build-gold", action="store_true")
    ap.add_argument("--match", action="store_true")
    args = ap.parse_args()
    pr = ROOT / args.peerread
    if args.build_gold or not GOLD_PATH.exists():
        gold = build_gold(pr)
        GOLD_PATH.write_text(json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[gold] 写出 {GOLD_PATH} ({len(gold)} 篇)")
    else:
        gold = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    if args.match:
        match(pr, gold)


if __name__ == "__main__":
    main()
