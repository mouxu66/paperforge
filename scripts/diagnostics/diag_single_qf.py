"""单篇诊断：跑 1506.02690 figures-ON，dump 完整 QF reasoning + figure_items。

1506.02690 是 QF 减分最多的反例：
  - 只有 1 张图，caption 100% + ocr 100%（证据最丰富 rich=0.75）
  - 但 QF 减分 -0.147（accept -> minor_revision）
  - 需确认 QF 减分是否合理（发现真实图文不一致）还是误判
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# figures-ON 配置
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

STEM = "1506.02690"
PEERREAD_IDX = ROOT / "deliverables" / "peerread_index.json"


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


def dump_figure_items(paper_id: str) -> None:
    """直接查 PaperFigure，dump 给 LLM 的 figure_items 内容。"""
    import sqlite3
    conn = sqlite3.connect(str(ROOT / "mock_api" / "paperforge_mock.db"))
    cur = conn.cursor()
    cur.execute("""
        SELECT page, figure_index, caption_text, ocr_text, qwen_summary, axis_info, source, vlm_decision
        FROM paper_figures WHERE paper_id = ? ORDER BY page, figure_index
    """, (paper_id,))
    print(f"\n=== PaperFigure 内容（{paper_id}）===")
    for i, (page, idx, cap, ocr, qs, axis, src, vlm) in enumerate(cur.fetchall(), 1):
        print(f"\n--- 图 {i} (p{page} fig{idx}, src={src}, vlm={vlm}) ---")
        print(f"caption_text: {(cap or '')[:300]}")
        print(f"ocr_text: {(ocr or '')[:300]}")
        print(f"qwen_summary: {(qs or '')[:300]}")
        print(f"axis_info: {(axis or '')[:300]}")
    conn.close()


def main():
    idx = json.loads(PEERREAD_IDX.read_text(encoding="utf-8"))
    paper = next(j for j in idx if j["stem"] == STEM)
    print(f"=== 单篇诊断: {STEM} ===")
    print(f"title: {paper['title']}")
    print(f"human_accepted: {paper['accepted']}")

    # dump figure 内容
    dump_figure_items(f"pr_{STEM}")

    # 跑 figures-ON DEPTH
    full, abstract = extract_full_text(paper["parsed_path"])
    print(f"\nfull_text_len: {len(full)}")

    from mock_api.depth_eval_v4 import DepthReviewer
    reviewer = DepthReviewer(compute_mode="deep")

    # monkey-patch _log 捕获 QF 日志
    qf_logs = []
    orig_log = reviewer._log
    def capture_log(msg, *a, **kw):
        if "[QF]" in str(msg) or "figure" in str(msg).lower():
            qf_logs.append(str(msg))
        orig_log(msg, *a, **kw)
    reviewer._log = capture_log

    print(f"\n=== 跑 figures-ON DEPTH ===")
    result = asyncio.run(reviewer.review_async_dag(
        paper_id=f"pr_{STEM}", title=paper["title"] or "", full_text=full,
        abstract=abstract, paper_meta={"source": "peerread"},
    ))

    rd = result.model_dump()
    print(f"\n=== result 关键字段 ===")
    for k in sorted(rd.keys()):
        v = rd[k]
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)[:200]
        print(f"  {k}: {v}")

    print(f"\n=== QF 日志 ===")
    for log in qf_logs:
        print(f"  {log}")

    # 找 QF result
    if hasattr(result, "figure_consistency_score") or "figure_consistency_score" in rd:
        print(f"\nfigure_consistency_score: {rd.get('figure_consistency_score')}")


if __name__ == "__main__":
    main()
