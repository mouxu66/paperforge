"""批量诊断 QF 减分论文：dump figure 数据 + 跑 figures-ON，验证 QF 减分合理性。

复用 diag_single_qf.py 的逻辑，对减分最多的论文（除已诊断的 1506.02690 外）
批量执行 DEPTH 评审，捕获 QF reasoning，人工验证信号合理性。

减分论文列表（按 delta 排序，来自 qf_signal_directional_analysis.json）：
  - 1502.02367: delta=-0.165 (13 figs, cap=30.8%, ocr=0%, rich=0.327)  ← 最减分
  - 1307.0060: delta=-0.155 (7 figs, cap=85.7%, ocr=28.6%, rich=0.536) ← 翻转 accept->minor
  - 1611.01587: delta=-0.065 (7 figs, cap=14.3%, ocr=14.3%, rich=0.321)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# figures-ON 配置（与 diag_single_qf.py / figures_qf_test.py 一致）
os.environ.setdefault("PAPERFORGE_DISABLE_FIGURE_TRIGGER", "1")
os.environ.setdefault("PAPERFORGE_LLM_TEMPERATURE", "0")
os.environ.setdefault("PAPERFORGE_DEPTH_QF_NODE_ENABLED", "true")
os.environ.setdefault("PAPERFORGE_DEPTH_FIGURE_WEIGHT", "0.1")
os.environ["PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED"] = "true"
os.environ["DEPTH_FIGURE_EVIDENCE_ENABLED"] = "true"
os.environ["PAPERFORGE_DEPTH_OFFSET_TABLE"] = '{"default":-0.09,"peerread":0.18}'
os.environ["PAPERFORGE_DEPTH_SCORE_OFFSET"] = "-0.09"
os.environ["PAPERFORGE_DEPTH_CAP_THRESHOLD"] = "0.80"
os.environ["PAPERFORGE_DEPTH_CAP_TAPER"] = "0.15"

STEMS = ["1502.02367", "1307.0060"]
PEERREAD_IDX = ROOT / "deliverables" / "peerread_index.json"
RESULTS_OFF = ROOT / "deliverables" / "figures_qf_test_results_off.jsonl"


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


def dump_figure_items(paper_id: str) -> list[dict]:
    """直接查 PaperFigure，dump 给 LLM 的 figure_items 内容。"""
    import sqlite3
    conn = sqlite3.connect(str(ROOT / "mock_api" / "paperforge_mock.db"))
    cur = conn.cursor()
    cur.execute("""
        SELECT page, figure_index, caption_text, ocr_text, qwen_summary, axis_info, source, vlm_decision
        FROM paper_figures WHERE paper_id = ? ORDER BY page, figure_index
    """, (paper_id,))
    items = []
    print(f"\n=== PaperFigure 内容（{paper_id}）===")
    for i, (page, idx, cap, ocr, qs, axis, src, vlm) in enumerate(cur.fetchall(), 1):
        print(f"\n--- 图 {i} (p{page} fig{idx}, src={src}, vlm={vlm}) ---")
        print(f"caption_text: {(cap or '')[:300]}")
        print(f"ocr_text: {(ocr or '')[:300]}")
        print(f"qwen_summary: {(qs or '')[:300]}")
        print(f"axis_info: {(axis or '')[:300]}")
        items.append({
            "page": page, "idx": idx, "cap": cap or "", "ocr": ocr or "",
            "qs": qs or "", "axis": axis or "", "src": src, "vlm": vlm,
        })
    conn.close()
    return items


def load_off_baseline(stem: str) -> dict:
    """加载 figures-OFF 模式的 baseline 结果用于对比。"""
    if not RESULTS_OFF.exists():
        return {}
    for line in RESULTS_OFF.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("stem") == stem:
            return r
    return {}


async def run_one(stem: str, paper: dict) -> dict:
    """跑单篇 figures-ON DEPTH 评审，返回关键字段。"""
    print(f"\n{'='*60}")
    print(f"=== 诊断: {stem} ===")
    print(f"title: {paper['title']}")
    print(f"human_accepted: {paper['accepted']}")
    print(f"{'='*60}")

    # dump figure 内容
    fig_items = dump_figure_items(f"pr_{stem}")

    # 跑 figures-ON DEPTH
    full, abstract = extract_full_text(paper["parsed_path"])
    print(f"\nfull_text_len: {len(full)}")

    from mock_api.depth_eval_v4 import DepthReviewer
    reviewer = DepthReviewer(compute_mode="deep")

    # monkey-patch _log 捕获 QF 日志
    qf_logs = []
    orig_log = reviewer._log
    def capture_log(msg, *a, **kw):
        if "[QF]" in str(msg) or "figure" in str(msg).lower() or "图表" in str(msg):
            qf_logs.append(str(msg))
        orig_log(msg, *a, **kw)
    reviewer._log = capture_log

    print(f"\n=== 跑 figures-ON DEPTH ===")
    result = await reviewer.review_async_dag(
        paper_id=f"pr_{stem}", title=paper["title"] or "", full_text=full,
        abstract=abstract, paper_meta={"source": "peerread"},
    )

    rd = result.model_dump()
    print(f"\n=== result 关键字段 ===")
    for k in ["base_score", "calibrated_score", "final_verdict", "llm_verdict",
              "figure_consistency_score", "figure_flags", "qf_reasoning",
              "evidence_checks", "evidence_ids"]:
        v = rd.get(k)
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)[:300]
        print(f"  {k}: {v}")

    print(f"\n=== QF 日志 ===")
    for log in qf_logs:
        print(f"  {log}")

    # baseline 对比
    off = load_off_baseline(stem)
    off_score = off.get("depth_calibrated_score")
    off_verdict = off.get("depth_verdict")
    on_score = rd.get("calibrated_score")
    delta = (on_score - off_score) if off_score is not None else None

    print(f"\n=== baseline 对比（figures-OFF -> ON）===")
    print(f"  score: {off_score} -> {on_score} (Δ={delta:+.4f})" if delta is not None else f"  score: ON={on_score} (off baseline 缺失)")
    print(f"  verdict: {off_verdict} -> {rd.get('final_verdict')}")
    print(f"  human: accepted={paper['accepted']}")

    return {
        "stem": stem,
        "title": paper["title"],
        "human_accepted": paper["accepted"],
        "off_score": off_score,
        "on_score": on_score,
        "delta": delta,
        "off_verdict": off_verdict,
        "on_verdict": rd.get("final_verdict"),
        "llm_verdict": rd.get("llm_verdict"),
        "figure_consistency_score": rd.get("figure_consistency_score"),
        "figure_flags": rd.get("figure_flags"),
        "qf_reasoning": rd.get("qf_reasoning"),
        "fig_items_count": len(fig_items),
        "fig_items": fig_items,
        "qf_logs": qf_logs,
    }


async def main():
    idx = json.loads(PEERREAD_IDX.read_text(encoding="utf-8"))
    by_stem = {j["stem"]: j for j in idx}

    results = []
    for stem in STEMS:
        paper = by_stem.get(stem)
        if not paper:
            print(f"[skip] {stem} 不在 peerread_index.json")
            continue
        try:
            r = await run_one(stem, paper)
            results.append(r)
        except Exception as e:
            print(f"[error] {stem}: {e}")
            import traceback
            traceback.print_exc()

    # 汇总
    print(f"\n\n{'='*60}")
    print(f"=== 批量诊断汇总 ===")
    print(f"{'='*60}")
    print(f"{'stem':<14} {'human':>6} {'off':>7} {'on':>7} {'Δ':>8} {'verdict_off→on':<25} {'qf_score':>9} | 合理性")
    print("-" * 110)
    for r in results:
        delta_str = f"{r['delta']:+.4f}" if r['delta'] is not None else "N/A"
        verdict_str = f"{r['off_verdict']}->{r['on_verdict']}"
        qf_score = r['figure_consistency_score']
        # 合理性判断：human accept 但 QF 减分到 minor/reject 是否有充分理由
        flags = r.get('figure_flags') or []
        qf_reasoning = r.get('qf_reasoning') or ''
        if r['human_accepted'] and r['on_verdict'] not in ('accept',):
            if '冲突' in qf_reasoning or '缺失' in qf_reasoning or '错位' in qf_reasoning or flags:
                reasonable = "✓ 合理（figure 数据有冲突/缺失）"
            else:
                reasonable = "? 待人工复查"
        else:
            reasonable = "✓ 未翻转/未减分"
        print(f"{r['stem']:<14} {'Y' if r['human_accepted'] else 'N':>6} "
              f"{r['off_score'] or 0:>7.3f} {r['on_score'] or 0:>7.3f} {delta_str:>8} "
              f"{verdict_str:<25} {qf_score or 0:>9.3f} | {reasonable}")

    # 保存
    out_path = ROOT / "deliverables" / "diag_qf_down_batch.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] 结果保存到 {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
