"""figures-OFF 大规模测试：在 PeerRead 大样本上验证 acc 稳定性。

之前 17 篇小样本 acc=0.706 可能偶然。本脚本在 12205 篇 PeerRead 索引里
分层抽样 100-200 篇，跑 figures-OFF（DEPTH_FIGURE_EVIDENCE_ENABLED=false）拿
大样本 baseline。

不依赖 PaperFigure 表（figures-OFF 不消费图证据）。

用法：
    python scripts/calibration/figures_off_large_scale.py --samples 100
    python scripts/calibration/figures_off_large_scale.py --samples 200 --stratified
    python scripts/calibration/figures_off_large_scale.py --calibrate
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# 关键开关（必须在 import depth_eval_v4 之前）
os.environ.setdefault("PAPERFORGE_DISABLE_FIGURE_TRIGGER", "1")
os.environ.setdefault("PAPERFORGE_LLM_TEMPERATURE", "0")
os.environ.setdefault("PAPERFORGE_DEPTH_QF_NODE_ENABLED", "true")
os.environ.setdefault("PAPERFORGE_DEPTH_FIGURE_WEIGHT", "0.0")
os.environ.setdefault("PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED", "false")
os.environ.setdefault("DEPTH_FIGURE_EVIDENCE_ENABLED", "false")

# 生产 offset
DEFAULT_OFFSET_TABLE = '{"default":-0.09,"peerread":0.18}'
DEFAULT_SCORE_OFFSET = "-0.09"
DEFAULT_CAP_THRESHOLD = "0.80"
DEFAULT_CAP_TAPER = "0.15"

IDX = ROOT / "deliverables" / "peerread_index.json"
RESULTS = ROOT / "deliverables" / "figures_off_large_scale.jsonl"


def stratified_sample(idx: list[dict], n: int, seed: int = 42) -> list[dict]:
    """分层抽样：accept/reject 比例与全集一致。

    PeerRead 全集 accept:reject ≈ 25:75，按此比例分层抽样。
    """
    rng = random.Random(seed)
    accept = [j for j in idx if j["accepted"]]
    reject = [j for j in idx if not j["accepted"]]
    # 按全集比例分配
    n_accept = round(n * len(accept) / len(idx))
    n_reject = n - n_accept
    rng.shuffle(accept)
    rng.shuffle(reject)
    sampled = accept[:n_accept] + reject[:n_reject]
    rng.shuffle(sampled)
    return sampled


def extract_full_text(parsed_path: str) -> tuple[str, str]:
    o = json.loads(Path(parsed_path).read_text(encoding="utf-8"))
    md = o.get("metadata", o)
    sections = md.get("sections", []) or []
    parts = []
    for s in sections:
        h = s.get("heading", "") or ""
        t = s.get("text", "") or ""
        if h:
            parts.append(f"\n## {h}\n")
        if t:
            parts.append(t)
    full = "\n".join(parts).strip()
    abstract = (md.get("abstractText") or "").strip()
    return full, abstract


def verdict_to_accept_int(verdict: str | None, tier: str = "strict") -> int:
    if not verdict:
        return 0
    v = verdict.replace("_revision", "")
    if tier == "strict":
        return 1 if v == "accept" else 0
    return 1 if v in ("accept", "minor") else 0


def confusion(y_pred, y_true) -> dict:
    tp = sum(1 for p, t in zip(y_pred, y_true) if p == 1 and t == 1)
    fn = sum(1 for p, t in zip(y_pred, y_true) if p == 1 and t == 0)
    fp = sum(1 for p, t in zip(y_pred, y_true) if p == 0 and t == 1)
    tn = sum(1 for p, t in zip(y_pred, y_true) if p == 0 and t == 0)
    n = len(y_pred)
    return {
        "n": n, "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "accuracy": round((tp + tn) / n, 4) if n else 0,
        "precision": round(tp / (tp + fp), 4) if (tp + fp) else 0,
        "recall": round(tp / (tp + fn), 4) if (tp + fn) else 0,
        "f1": round(2 * tp / (2 * tp + fp + fn), 4) if (2 * tp + fp + fn) else 0,
    }


def cohen_kappa_2tier(y_pred, y_true) -> float:
    n = len(y_pred)
    if n == 0:
        return float("nan")
    po = sum(1 for p, t in zip(y_pred, y_true) if p == t) / n
    pa = sum(y_pred) / n
    pb = sum(y_true) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def run(papers: list[dict], fresh: bool) -> None:
    if fresh and RESULTS.exists():
        RESULTS.unlink()

    os.environ["PAPERFORGE_DEPTH_OFFSET_TABLE"] = DEFAULT_OFFSET_TABLE
    os.environ["PAPERFORGE_DEPTH_SCORE_OFFSET"] = DEFAULT_SCORE_OFFSET
    os.environ["PAPERFORGE_DEPTH_CAP_THRESHOLD"] = DEFAULT_CAP_THRESHOLD
    os.environ["PAPERFORGE_DEPTH_CAP_TAPER"] = DEFAULT_CAP_TAPER

    from mock_api.depth_eval_v4 import DepthReviewer

    done = set()
    if RESULTS.exists():
        for line in RESULTS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    done.add(r["stem"])
                except Exception:
                    pass

    reviewer = DepthReviewer(compute_mode="deep")
    out = open(RESULTS, "a", encoding="utf-8")
    total = len(papers)
    print(f"\n=== figures-OFF 大规模测试（n={total}）===")
    print(f"  DEPTH_FIGURE_EVIDENCE_ENABLED=false")
    print(f"  已完成: {len(done)}，待跑: {total - len(done)}")

    for i, j in enumerate(papers, 1):
        stem = j["stem"]
        if stem in done:
            continue
        try:
            full, abstract = extract_full_text(j["parsed_path"])
        except Exception as e:
            print(f"  [{i}/{total}] {stem} 全文提取失败: {e}")
            continue
        if len(full) < 1500:
            print(f"  [{i}/{total}] {stem} 全文过短({len(full)})，跳过")
            continue
        try:
            paper_meta = {"source": "peerread", "year": j.get("year")}
            result = asyncio.run(reviewer.review_async_dag(
                paper_id=f"pr_{stem}", title=j["title"] or "", full_text=full,
                abstract=abstract, paper_meta=paper_meta,
            ))
            rd = result.model_dump()
            rec = {
                "stem": stem, "title": j["title"], "venue": j["venue"],
                "human_accepted": j["accepted"], "full_text_len": len(full),
                "depth_calibrated_score": rd.get("calibrated_score"),
                "depth_base_score": rd.get("base_score"),
                "depth_verdict": rd.get("final_verdict"),
                "novelty": rd.get("novelty_score"),
                "rigor": rd.get("rigor_score"),
                "influence": rd.get("influence_score"),
                "reproducibility": rd.get("reproducibility_score"),
                "objectivity": rd.get("objectivity_score"),
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            v = rec["depth_verdict"]
            print(f"  [{i}/{total}] {stem} score={rec['depth_calibrated_score']} "
                  f"verdict={v} human={j['accepted']}")
        except Exception as e:
            print(f"  [{i}/{total}] {stem} DEPTH 失败: {e}")
    out.close()
    print(f"\n[done] 结果写入 {RESULTS}")


def calibrate() -> None:
    if not RESULTS.exists():
        print("[err] 无结果文件")
        return
    rows = [json.loads(l) for l in RESULTS.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        print("[err] 结果为空")
        return

    print(f"\n=== figures-OFF 大规模校准（n={len(rows)}）===")

    y_true = [1 if r["human_accepted"] else 0 for r in rows]

    for tier in ("strict", "loose"):
        y_pred = [verdict_to_accept_int(r["depth_verdict"], tier) for r in rows]
        cm = confusion(y_pred, y_true)
        k = cohen_kappa_2tier(y_pred, y_true)
        print(f"\n  --- {tier} 口径 ---")
        print(f"  acc={cm['accuracy']}  κ={k:+.4f}")
        print(f"  TP={cm['tp']} FN={cm['fn']} FP={cm['fp']} TN={cm['tn']}")
        print(f"  prec={cm['precision']} rec={cm['recall']} F1={cm['f1']}")

    # verdict 分布
    dist = Counter(r["depth_verdict"] for r in rows)
    print(f"\n  verdict 分布: {dict(dist)}")
    # human 分布
    print(f"  human 分布: accept={sum(y_true)}, reject={len(y_true)-sum(y_true)}")

    # 按分数段看准确率
    print(f"\n  --- 按 DEPTH 分数段 ---")
    bins = [(0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 1.01)]
    for lo, hi in bins:
        seg = [r for r in rows if lo <= (r["depth_calibrated_score"] or 0) < hi]
        if not seg:
            continue
        correct = sum(1 for r in seg if (verdict_to_accept_int(r["depth_verdict"], "strict") == 1) == r["human_accepted"])
        print(f"  [{lo:.2f}, {hi:.2f}): n={len(seg)}, acc={correct/len(seg):.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=int, default=100, help="抽样数量")
    ap.add_argument("--seed", type=int, default=42, help="随机种子")
    ap.add_argument("--fresh", action="store_true", help="清空结果重跑")
    ap.add_argument("--calibrate", action="store_true", help="仅读已有结果算指标")
    ap.add_argument("--accept-only", action="store_true", help="只跑 accept 论文（reject 太多时用）")
    args = ap.parse_args()

    if args.calibrate:
        calibrate()
        return

    idx = json.loads(IDX.read_text(encoding="utf-8"))
    if args.accept_only:
        idx = [j for j in idx if j["accepted"]]
    papers = stratified_sample(idx, args.samples, args.seed)
    print(f"[准备] 抽样 {len(papers)} 篇")
    print(f"  accept: {sum(1 for p in papers if p['accepted'])}")
    print(f"  reject: {sum(1 for p in papers if not p['accepted'])}")
    run(papers, args.fresh)


if __name__ == "__main__":
    main()
