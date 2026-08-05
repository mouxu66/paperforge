"""快速诊断单篇：确认 qwen_summary 兜底是否真的进了 QF LLM 输入。

跑 figures-ON DEPTH，dump:
  1. PaperFigure 表中 qwen_summary/ocr_text 实际内容
  2. QF 节点的 figure_items 组装内容（看 qwen_summary 是否进入 LLM prompt）
  3. QF 节点的最终 score / reasoning
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

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

STEMS = ["1304.1574", "1506.03340"]  # 减分最大的 2 个 FN 论文


def dump_figure_items(paper_id):
    import sqlite3
    conn = sqlite3.connect(str(ROOT / "mock_api" / "paperforge_mock.db"))
    cur = conn.cursor()
    cur.execute("""
        SELECT page, figure_index, caption_text, ocr_text, qwen_summary, axis_info, source, vlm_decision
        FROM paper_figures WHERE paper_id = ? ORDER BY page, figure_index
    """, (paper_id,))
    print(f"\n=== PaperFigure 表实际内容（{paper_id}）===")
    for i, (page, idx, cap, ocr, qs, axis, src, vlm) in enumerate(cur.fetchall(), 1):
        print(f"\n--- 图 {i} (p{page} fig{idx}, src={src}, vlm={vlm}) ---")
        print(f"caption_text: {(cap or '')[:200]}")
        print(f"ocr_text:     {(ocr or '')[:200]}")
        print(f"qwen_summary: {(qs or '')[:200]}")
        print(f"axis_info:    {(axis or '')[:200]}")
    conn.close()


async def diag_one(stem, reviewer_cls, idx):
    print(f"\n{'='*70}")
    print(f"=== 诊断 {stem}: qwen_summary 兜底是否真的进了 QF LLM ===")
    print(f"{'='*70}")

    dump_figure_items(f"pr_{stem}")

    reviewer = reviewer_cls(compute_mode="deep")

    qf_logs = []
    orig_log = reviewer._log
    def capture(msg, *a, **kw):
        if "[QF]" in str(msg) or "figure" in str(msg).lower() or "图表" in str(msg):
            qf_logs.append(str(msg))
        orig_log(msg, *a, **kw)
    reviewer._log = capture

    paper = next(j for j in idx if j["stem"] == stem)

    o = json.loads(Path(paper["parsed_path"]).read_text(encoding="utf-8"))
    md = o.get("metadata", o)
    sections = md.get("sections", []) or []
    parts = []
    for s in sections:
        if s.get("heading"):
            parts.append(f"\n## {s['heading']}\n")
        if s.get("text"):
            parts.append(s["text"])
    full = "\n".join(parts).strip()
    abstract = (md.get("abstractText") or "").strip()

    print(f"\n=== 跑 figures-ON DEPTH ===")
    result = await reviewer.review_async_dag(
        paper_id=f"pr_{stem}", title=paper["title"] or "", full_text=full,
        abstract=abstract, paper_meta={"source": "peerread"},
    )

    rd = result.model_dump()
    print(f"\n=== QF 节点输出 ===")
    print(f"figure_consistency_score: {rd.get('figure_consistency_score')}")
    print(f"figure_flags: {rd.get('figure_flags')}")
    print(f"qf_reasoning: {rd.get('qf_reasoning')}")
    print(f"base_score: {rd.get('base_score')}")
    print(f"calibrated_score: {rd.get('calibrated_score')}")
    print(f"final_verdict: {rd.get('final_verdict')}")

    print(f"\n=== QF 日志 ===")
    for log in qf_logs:
        print(f"  {log}")


async def main():
    from mock_api.depth_eval_v4 import DepthReviewer
    idx = json.loads((ROOT / "deliverables" / "peerread_index.json").read_text(encoding="utf-8"))
    for stem in STEMS:
        try:
            await diag_one(stem, DepthReviewer, idx)
        except Exception as e:
            print(f"\n!!! {stem} 诊断失败: {e}")


if __name__ == "__main__":
    asyncio.run(main())
