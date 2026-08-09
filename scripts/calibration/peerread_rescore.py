"""PeerRead 重打分校准管线（阶段1：verdict 校准，真人类金标）。

思路：PeerRead 论文自己有全文(parsed_pdfs) + 人类决定(reviews/accepted)。
抽 N 篇 -> 用 DEPTH v4 直接对全文打分 -> 对比人类 accepted，算二分类一致性。
这取代了"20 样本 LLM 盲评"，是真·人类金标。

子命令：
  index    扫描 .peerread_cache，按论文 stem 关联 reviews(accepted) 与 parsed_pdfs(全文)，
           输出 deliverables/peerread_index.json（仅含可关联论文清单）。
  sample   N=40 分层抽样（accept/reject 各半，优先 arxiv 子集），
           输出 deliverables/peerread_sample.json。
  run      对 sample 逐篇：取全文 -> DepthReviewer.review_async_dag -> 存结果
           （增量写 deliverables/peerread_rescore_results.jsonl，可断点续跑）。
  calibrate 读 results，算 DEPTH verdict vs 人类 accepted 的 κ/准确率/混淆矩阵。

运行：
  python scripts/peerread_rescore.py index
  python scripts/peerread_rescore.py sample --n 40
  python scripts/peerread_rescore.py run
  python scripts/peerread_rescore.py calibrate
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

# 让脚本在 scripts/ 下也能 import mock_api（项目根加入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── 安全默认值（必须在 import depth_eval_v4 之前设）──
# 禁用 bulk DEPTH 内联图触发：图只走独立小批量 pass（figure_understanding worker），
# DEPTH 仅【消费】已存在的 paper_figures 证据，不自己触发抽取（避免与 Qwen 在 8GB 上互斥）。
os.environ.setdefault("PAPERFORGE_DISABLE_FIGURE_TRIGGER", "1")
# 温度固定 greedy：确定性打分才能干净测偏移前后差（否则 run-to-run 方差淹没 κ）。
os.environ.setdefault("PAPERFORGE_LLM_TEMPERATURE", "0")
# 注意：偏移 / 封顶 / 图证据在 run() 内按 --baseline / --with-figures 动态设置，
# 必须在 import depth_eval_v4 之前落地，故不在此模块级写死。

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".peerread_cache"
IDX = ROOT / "deliverables" / "peerread_index.json"
SAMPLE = ROOT / "deliverables" / "peerread_sample.json"
RESULTS = ROOT / "deliverables" / "peerread_rescore_results.jsonl"

VENUE_PREF = ["arxiv.cs.cl_2007-2017", "arxiv.cs.ai_2007-2017", "arxiv.cs.lg_2007-2017", "iclr_2017"]


def list_jsons(d: Path):
    return sorted(d.rglob("*.json"))


def stem_of(path: Path, suffix: str) -> str:
    # reviews/<id>.json  -> id ; parsed_pdfs/<id>.pdf.json -> id
    name = path.name
    if name.endswith(suffix):
        name = name[: -len(suffix)]
    return name


def build_index():
    cache = CACHE
    # reviews: stem -> {accepted, title, venue, split}
    reviews_map = {}
    for f in list_jsons(cache / "data"):
        if f.parent.name != "reviews":
            continue
        try:
            o = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        stem = stem_of(f, ".json")
        acc = o.get("accepted")
        if isinstance(acc, str):
            acc = acc.strip().lower() in ("true", "1", "yes")
        # 仅保留确实含 accepted 布尔的（reviews/ 目录有；部分 meta 可能缺）
        reviews_map[stem] = {
            "accepted": bool(acc) if acc is not None else None,
            "title": (o.get("title") or "").strip(),
            "venue": o.get("venue") or _venue_from_path(f),
            "split": o.get("split") or _split_from_path(f),
            "has_accepted": acc is not None,
        }
    # parsed_pdfs: stem -> path
    parsed_map = {}
    for f in list_jsons(cache / "data"):
        if f.parent.name != "parsed_pdfs":
            continue
        stem = stem_of(f, ".pdf.json")
        parsed_map[stem] = f
    # join
    joined = []
    for stem, rv in reviews_map.items():
        if not rv["has_accepted"]:
            continue
        if stem in parsed_map:
            joined.append({
                "stem": stem,
                "accepted": rv["accepted"],
                "title": rv["title"],
                "venue": rv["venue"],
                "split": rv["split"],
                "parsed_path": str(parsed_map[stem]),
            })
    json.dump(joined, open(IDX, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[index] 可关联论文（有 accepted + 有全文）: {len(joined)}")
    print("  venue 分布:", Counter(j["venue"] for j in joined))
    print("  accepted 分布:", Counter(j["accepted"] for j in joined))
    return joined


def _venue_from_path(f: Path) -> str:
    parts = f.parts
    for i, p in enumerate(parts):
        if p == "data" and i + 1 < len(parts):
            return parts[i + 1]
    return ""


def _split_from_path(f: Path) -> str:
    parts = f.parts
    for p in parts:
        if p in ("train", "test", "dev"):
            return p
    return ""


def sample(n=40):
    joined = json.loads(IDX.read_text(encoding="utf-8")) if IDX.exists() else build_index()
    # 优先 arxiv 子集（DEPTH 输入质量好），其次 iclr
    def pref(v):
        return VENUE_PREF.index(v) if v in VENUE_PREF else 99
    joined.sort(key=lambda x: (pref(x["venue"]), x["stem"]))
    acc = [j for j in joined if j["accepted"]]
    rej = [j for j in joined if not j["accepted"]]
    half = n // 2
    # 尽量从优先 venue 取，各取 half
    def take(lst, k):
        return lst[:k]
    sel = take(acc, half) + take(rej, n - half)
    # 若某类不足，用另一类补足
    if len(sel) < n:
        used = {id(j) for j in sel}
        for j in joined:
            if len(sel) >= n:
                break
            if id(j) not in used:
                sel.append(j)
    json.dump(sel, open(SAMPLE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[sample] 选定 {len(sel)} 篇（accept={sum(1 for j in sel if j['accepted'])} / reject={sum(1 for j in sel if not j['accepted'])}）")
    return sel


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


def run(out_path: str | None = None, with_figures: bool = False, baseline: bool = False):
    """对 sample 逐篇跑 DEPTH v4。

    with_figures: 开启 DEPTH_FIGURE_EVIDENCE_ENABLED（消费已存在的 paper_figures
                  qwen_summary，进入 M0 含图口径）。图必须已通过 figure_understanding
                  预处理写入 DB，本步不再触发抽取。
    baseline:     产出“真·offset=0”基线——清零偏移表 + 全局偏移 + 关闭封顶，
                  供 scripts/offset_scan.py 做确定性加法偏移扫描。
    """
    # ── 偏移 / 封顶 / 图证据：必须在 import depth_eval_v4 之前落地 ──
    if baseline:
        # 清零两路偏移 + 关闭封顶（阈值设 2.0 使 apply_top_tier_cap 不触发），
        # 得到未被任何偏移/封顶污染的原始 DEPTH 分。
        os.environ["PAPERFORGE_DEPTH_OFFSET_TABLE"] = '{"default":0,"peerread":0}'
        os.environ["PAPERFORGE_DEPTH_SCORE_OFFSET"] = "0"
        os.environ["PAPERFORGE_DEPTH_CAP_THRESHOLD"] = "2.0"
    else:
        # 2026-08-09 重扫：peerread 偏移在 0.6 阈值下为 0.0（旧 +0.18 是 0.8 阈值产物）
        os.environ["PAPERFORGE_DEPTH_OFFSET_TABLE"] = '{"default":-0.09,"peerread":0.0}'
        os.environ["PAPERFORGE_DEPTH_SCORE_OFFSET"] = "-0.09"
        os.environ["PAPERFORGE_DEPTH_CAP_THRESHOLD"] = "0.80"
    os.environ["PAPERFORGE_DEPTH_CAP_TAPER"] = "0.15"
    os.environ["DEPTH_FIGURE_EVIDENCE_ENABLED"] = "true" if with_figures else "false"

    if not SAMPLE.exists():
        sample()
    sel = json.loads(SAMPLE.read_text(encoding="utf-8"))
    from mock_api.depth_eval_v4 import DepthReviewer

    results_path = Path(out_path) if out_path else RESULTS
    done = set()
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    done.add(json.loads(line)["stem"])
                except Exception:
                    pass
    reviewer = DepthReviewer(compute_mode="deep")
    out = open(results_path, "a", encoding="utf-8")
    total = len(sel)
    for i, j in enumerate(sel, 1):
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
            # 分档偏移上下文：验证 PeerRead(2007-2017) 需 +0.34 正向偏移（source 级命中）
            paper_meta = {"source": "peerread", "year": j.get("year")}
            result = asyncio.run(reviewer.review_async_dag(
                paper_id=f"pr_{stem}", title=j["title"] or "", full_text=full,
                abstract=abstract, paper_meta=paper_meta))
            rd = result.model_dump()
            rec = {
                "stem": stem, "title": j["title"], "venue": j["venue"],
                "human_accepted": j["accepted"], "full_text_len": len(full),
                "depth_calibrated_score": rd.get("calibrated_score"),
                "depth_base_score": rd.get("base_score"),
                "depth_delta": rd.get("delta"),
                "depth_verdict": rd.get("final_verdict"),
                "novelty": rd.get("novelty_score"), "rigor": rd.get("rigor_score"),
                "influence": rd.get("influence_score"),
                "reproducibility": rd.get("reproducibility_score"),
                "objectivity": rd.get("objectivity_score"),
            }
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            print(f"  [{i}/{total}] {stem} score={rec['depth_calibrated_score']} verdict={rec['depth_verdict']} human_accept={j['accepted']}")
        except Exception as e:
            print(f"  [{i}/{total}] {stem} DEPTH 失败: {e}")
    out.close()
    print(f"[run] 完成，结果见 {RESULTS}")


def cohen_kappa(a, b):
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


def confuse(a, b):
    tp = sum(1 for x, y in zip(a, b) if x == 1 and y == 1)
    fn = sum(1 for x, y in zip(a, b) if x == 1 and y == 0)
    fp = sum(1 for x, y in zip(a, b) if x == 0 and y == 1)
    tn = sum(1 for x, y in zip(a, b) if x == 0 and y == 0)
    n = len(a)
    return {"n": n, "tp": tp, "fn": fn, "fp": fp, "tn": tn,
            "accuracy": round((tp + tn) / n, 4) if n else 0,
            "precision": round(tp / (tp + fp), 4) if (tp + fp) else 0,
            "recall": round(tp / (tp + fn), 4) if (tp + fn) else 0,
            "f1": round(2 * tp / (2 * tp + fp + fn), 4) if (2 * tp + fp + fn) else 0}


def calibrate():
    rows = [json.loads(l) for l in RESULTS.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"[calibrate] 有效结果 {len(rows)} 篇")
    acc = [1 if r["human_accepted"] else 0 for r in rows]
    strict = [1 if r["depth_verdict"] == "accept" else 0 for r in rows]
    loose = [1 if r["depth_verdict"] in ("accept", "minor_revision") else 0 for r in rows]
    res = {
        "n": len(rows),
        "strict_accept_only": {"kappa": round(cohen_kappa(strict, acc), 4), **confuse(strict, acc)},
        "loose_accept_minor": {"kappa": round(cohen_kappa(loose, acc), 4), **confuse(loose, acc)},
        "mean_depth_score_accepted": round(sum(r["depth_calibrated_score"] for r in rows if r["human_accepted"]) / max(1, sum(acc)), 4),
        "mean_depth_score_rejected": round(sum(r["depth_calibrated_score"] for r in rows if not r["human_accepted"]) / max(1, len(acc) - sum(acc)), 4),
    }
    print("[calibrate] 严格(DEPTH accept vs 人类 accepted):", res["strict_accept_only"])
    print("[calibrate] 宽松(DEPTH accept/minor vs 人类 accepted):", res["loose_accept_minor"])
    print("[calibrate] DEPTH 分均值: accepted=", res["mean_depth_score_accepted"], "rejected=", res["mean_depth_score_rejected"])
    # 对比基准：20 样本 LLM 盲评 κ=0.19（全系统）/ 0.189（分数层）
    print("[calibrate] 对比：20 样本 LLM 盲评 verdict κ≈0.19；本真人类金标 κ 见上。")
    json.dump(res, open(ROOT / "deliverables" / "peerread_calibration_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["index", "sample", "run", "calibrate"])
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--out", type=str, default=None,
                    help="run 结果输出路径（验证分档偏移用，避免覆盖基准 jsonl）")
    ap.add_argument("--with-figures", action="store_true",
                    help="run: 开启 DEPTH_FIGURE_EVIDENCE_ENABLED（M0 含图口径，消费已存在图证据）")
    ap.add_argument("--baseline", action="store_true",
                    help="run: 产出 offset=0 基线（清零偏移表+封顶），供 offset_scan 重扫")
    args = ap.parse_args()
    if args.cmd == "index":
        build_index()
    elif args.cmd == "sample":
        sample(args.n)
    elif args.cmd == "run":
        run(args.out, with_figures=args.with_figures, baseline=args.baseline)
    elif args.cmd == "calibrate":
        calibrate()


if __name__ == "__main__":
    main()
