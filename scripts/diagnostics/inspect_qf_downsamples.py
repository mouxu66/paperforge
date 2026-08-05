"""抽检 QF 减分论文的 figure 数据 + QF reasoning。

3 篇减分最多：
  1502.02367 (-0.165): 13 figs, cap 30.8%, ocr 0%
  1307.0060  (-0.155): 7 figs, cap 85.7%, ocr 28.6%
  1506.02690 (-0.147): 1 fig, cap 100%, ocr 100%  ← 最反直觉（证据最丰富却减分最多）

输出 QF reasoning + figure 内容，人工判断减分是否合理。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "mock_api" / "paperforge_mock.db"

TARGETS = ["1502.02367", "1307.0060", "1506.02690"]


def dump_figure_content(stem: str) -> None:
    pid = f"pr_{stem}"
    print(f"\n{'='*70}")
    print(f"论文 {stem} (paper_id={pid})")
    print(f"{'='*70}")
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    cur.execute("""
        SELECT page, figure_index, caption_text, ocr_text, qwen_summary, source, vlm_decision
        FROM paper_figures WHERE paper_id = ?
        ORDER BY page, figure_index LIMIT 5
    """, (pid,))
    rows = cur.fetchall()
    print(f"figure 总数（前5）: {len(rows)}")
    for i, (page, idx, cap, ocr, qs, src, vlm) in enumerate(rows, 1):
        print(f"\n--- 图 {i} (p{page} fig{idx}, source={src}, vlm={vlm}) ---")
        print(f"caption: {(cap or '')[:200]}")
        print(f"ocr_text: {(ocr or '')[:200]}")
        print(f"qwen_summary: {(qs or '')[:200]}")
    conn.close()


def dump_qf_reasoning(stem: str) -> None:
    """从 DepthReviewV4 表查 QF reasoning。"""
    pid = f"pr_{stem}"
    print(f"\n--- QF reasoning (DepthReviewV4) ---")
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    # 查最新的 DepthReviewV4
    cur.execute("""
        SELECT created_at, final_verdict FROM depth_review_v4
        WHERE paper_id = ? ORDER BY created_at DESC LIMIT 2
    """, (pid,))
    rows = cur.fetchall()
    print(f"DepthReviewV4 记录数: {len(rows)}")
    for created, fv in rows:
        print(f"\n  [{created}]")
        if isinstance(fv, str):
            try:
                fv = json.loads(fv)
            except Exception:
                pass
        if isinstance(fv, dict):
            # 找 QF 相关字段
            for key in ("qf", "qf_result", "figure_consistency", "figure_consistency_score",
                        "qf_reasoning", "qf_flags", "inconsistency_flags"):
                if key in fv:
                    val = fv[key]
                    if isinstance(val, (dict, list)):
                        val = json.dumps(val, ensure_ascii=False)[:300]
                    print(f"    {key}: {val}")
            # 也 dump 整个 fv 的 keys
            print(f"    final_verdict keys: {list(fv.keys())[:15]}")
    conn.close()


for stem in TARGETS:
    dump_figure_content(stem)
    dump_qf_reasoning(stem)
