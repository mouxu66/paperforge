"""测试配置：depth_figure_weight=0.1 + depth_figure_evidence_enabled=False

即 QF 节点独立加载 figure 评分，但不并入 QE 池（避免污染 Q4/Q5c 辩论）。
看 QF 单独加权是否有用。

对比基线：figures_qf_test_results_off.jsonl（weight=0, evidence=false）
本测试输出：figures_qf_test_results_w010_evoff.jsonl（weight=0.1, evidence=false）

用法：
    python scripts/calibration/figures_qf_weight_test.py          # 续跑
    python scripts/calibration/figures_qf_weight_test.py --fresh   # 重跑
    python scripts/calibration/figures_qf_weight_test.py --calibrate  # 只读结果算 κ
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# 关键开关（必须在 import depth_eval_v4 之前落地）
os.environ.setdefault("PAPERFORGE_DISABLE_FIGURE_TRIGGER", "1")
os.environ.setdefault("PAPERFORGE_LLM_TEMPERATURE", "0")
os.environ.setdefault("PAPERFORGE_DEPTH_QF_NODE_ENABLED", "true")
# 测试配置：weight=0.1 + evidence=false（QF 评分但不并入 QE 池）
os.environ.setdefault("PAPERFORGE_DEPTH_FIGURE_WEIGHT", "0.1")
os.environ.setdefault("PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED", "false")
os.environ.setdefault("DEPTH_FIGURE_EVIDENCE_ENABLED", "false")

DEFAULT_OFFSET_TABLE = '{"default":-0.09,"peerread":0.18}'
DEFAULT_SCORE_OFFSET = "-0.09"
DEFAULT_CAP_THRESHOLD = "0.80"
DEFAULT_CAP_TAPER = "0.15"

DB_PATH = ROOT / "mock_api" / "paperforge_mock.db"
PEERREAD_IDX = ROOT / "deliverables" / "peerread_index.json"
RESULTS_OFF = ROOT / "deliverables" / "figures_qf_test_results_off.jsonl"
RESULTS_W010 = ROOT / "deliverables" / "figures_qf_test_results_w010_evoff.jsonl"


def list_fig_stems() -> list[str]:
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT paper_id FROM paper_figures WHERE paper_id LIKE 'pr_%'")
    stems = [r[0][3:] for r in cur.fetchall()]
    conn.close()
    return sorted(stems)


def load_target_papers() -> list[dict]:
    fig_stems = set(list_fig_stems())
    idx = json.loads(PEERREAD_IDX.read_text(encoding="utf-8"))
    by_stem = {j["stem"]: j for j in idx}
    rows = []
    for s in sorted(fig_stems):
        if s in by_stem:
            rows.append(by_stem[s])
    return rows


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


def cohen_kappa_2tier(y_pred: list[int], y_true: list[int]) -> float:
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


def confusion(y_pred: list[int], y_true: list[int]) -> dict:
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


def verdict_to_accept_int(verdict: str | None, tier: str = "strict") -> int:
    if not verdict:
        return 0
    v = verdict.replace("_revision", "")
    if tier == "strict":
        return 1 if v == "accept" else 0
    return 1 if v in ("accept", "minor") else 0


def run_one_mode(papers: list[dict], out_path: Path, fresh: bool) -> None:
    if fresh and out_path.exists():
        out_path.unlink()

    os.environ["PAPERFORGE_DEPTH_OFFSET_TABLE"] = DEFAULT_OFFSET_TABLE
    os.environ["PAPERFORGE_DEPTH_SCORE_OFFSET"] = DEFAULT_SCORE_OFFSET
    os.environ["PAPERFORGE_DEPTH_CAP_THRESHOLD"] = DEFAULT_CAP_THRESHOLD
    os.environ["PAPERFORGE_DEPTH_CAP_TAPER"] = DEFAULT_CAP_TAPER

    from mock_api.depth_eval_v4 import DepthReviewer  # noqa: E402

    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    done.add(json.loads(line)["stem"])
                except Exception:
                    pass

    reviewer = DepthReviewer(compute_mode="deep")
    out = open(out_path, "a", encoding="utf-8")
    total = len(papers)
    print(f"\n=== [w010_evoff] weight=0.1 + evidence=false 模式启动 ===")
    print(f"  DEPTH_FIGURE_WEIGHT={os.environ.get('PAPERFORGE_DEPTH_FIGURE_WEIGHT')}")
    print(f"  DEPTH_FIGURE_EVIDENCE_ENABLED={os.environ.get('PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED')}")
    print(f"  待跑: {total} 篇, 已完成: {len(done)} 篇")

    for i, j in enumerate(papers, 1):
        stem = j["stem"]
        if stem in done:
            print(f"  [{i}/{total}] skip {stem} (已跑)")
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
                "mode": "w010_evoff",
                "depth_calibrated_score": rd.get("calibrated_score"),
                "depth_base_score": rd.get("base_score"),
                "depth_delta": rd.get("delta"),
                "depth_verdict": rd.get("final_verdict"),
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            print(f"  [{i}/{total}] {stem} score={rec['depth_calibrated_score']} "
                  f"verdict={rec['depth_verdict']} human={j['accepted']}")
        except Exception as e:
            print(f"  [{i}/{total}] {stem} DEPTH 失败: {e}")
    out.close()
    print(f"  [w010_evoff] 完成，结果写入 {out_path}")


def calibrate() -> None:
    if not RESULTS_W010.exists():
        print(f"[error] 结果文件不存在: {RESULTS_W010}")
        return
    rows_w = [json.loads(l) for l in RESULTS_W010.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_stem_w = {r["stem"]: r for r in rows_w}

    # 对比基线 OFF
    has_off = RESULTS_OFF.exists()
    by_stem_off = {}
    if has_off:
        rows_off = [json.loads(l) for l in RESULTS_OFF.read_text(encoding="utf-8").splitlines() if l.strip()]
        by_stem_off = {r["stem"]: r for r in rows_off}

    common = sorted(set(by_stem_w) & set(by_stem_off)) if has_off else sorted(by_stem_w)
    print(f"\n=== w010_evoff vs OFF 对比 ===")
    print(f"  w010 完成 {len(rows_w)} 篇, OFF 完成 {len(by_stem_off)} 篇, 共同 {len(common)} 篇")

    if not common:
        print("  无共同样本")
        return

    y_true = [1 if by_stem_w[s]["human_accepted"] else 0 for s in common]

    for tier in ("strict", "loose"):
        print(f"\n  --- {tier} 口径 ---")
        for label, by in (("OFF(w=0,ev=f)", by_stem_off), ("w010(ev=f)", by_stem_w)):
            if not by:
                continue
            y_pred = [verdict_to_accept_int(by[s]["depth_verdict"], tier) for s in common]
            kappa = cohen_kappa_2tier(y_pred, y_true)
            cm = confusion(y_pred, y_true)
            print(f"  [{label}] κ={kappa:+.4f}  acc={cm['accuracy']}  "
                  f"TP={cm['tp']} FN={cm['fn']} FP={cm['fp']} TN={cm['tn']}  "
                  f"prec={cm['precision']} rec={cm['recall']} F1={cm['f1']}")

    # verdict 分布
    print("\n  --- verdict 分布 ---")
    for label, by in (("OFF", by_stem_off), ("w010", by_stem_w)):
        if by:
            dist = Counter(by[s]["depth_verdict"] for s in common)
            print(f"  [{label}] {dict(dist)}")

    # score 差异
    if has_off:
        print("\n  --- 每篇 score 差异（w010 - OFF） ---")
        diffs = []
        for s in common:
            s_off = by_stem_off[s]["depth_calibrated_score"] or 0
            s_w = by_stem_w[s]["depth_calibrated_score"] or 0
            d = s_w - s_off
            diffs.append((s, s_off, s_w, d, by_stem_off[s]["depth_verdict"], by_stem_w[s]["depth_verdict"]))
            if abs(d) > 1e-4:
                print(f"  {s}: OFF={s_off:.3f}({by_stem_off[s]['depth_verdict']}) -> "
                      f"w010={s_w:.3f}({by_stem_w[s]['depth_verdict']}) Δ={d:+.3f}")
        n_changed = sum(1 for d in diffs if abs(d[3]) > 1e-4)
        n_flip = sum(1 for d in diffs if d[4] != d[5])
        print(f"\n  score 改变: {n_changed}/{len(common)}")
        print(f"  verdict 翻转: {n_flip}/{len(common)}")

        # 按 human 分组看偏移
        print("\n  --- 按 human 分组 score 偏移 ---")
        for h in [True, False]:
            label = "accept" if h else "reject"
            grp = [d for d in diffs if by_stem_off[d[0]]["human_accepted"] == h]
            if grp:
                ds = [d[3] for d in grp]
                avg = sum(ds) / len(ds)
                pos = sum(1 for d in ds if d > 0.01)
                neg = sum(1 for d in ds if d < -0.01)
                print(f"  human={label}: {len(grp)} 篇, avg Δ={avg:+.4f}, pos={pos}, neg={neg}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="清空结果文件重跑")
    ap.add_argument("--calibrate", action="store_true", help="仅读已有结果算 κ")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 篇")
    args = ap.parse_args()

    if args.calibrate:
        calibrate()
        return

    papers = load_target_papers()
    if args.limit and args.limit > 0:
        papers = papers[:args.limit]
    print(f"[准备] 有图证据 + 有金标的 PeerRead 论文: {len(papers)} 篇")
    print(f"  accepted: {sum(1 for p in papers if p['accepted'])} accept, "
          f"{sum(1 for p in papers if not p['accepted'])} reject")

    run_one_mode(papers, RESULTS_W010, fresh=args.fresh)


if __name__ == "__main__":
    main()
