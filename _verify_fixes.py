"""修复后验证：用**真实生产代码路径**重放 624 篇论文，量化四项修复的效果。

与 `_diag_counterfactual.py` 的区别（P2 验证行为而非表象）：
  旧脚本自己重新实现了 offset / 裁决逻辑（是"模型"）；
  本脚本直接调用生产函数：
    - mock_api.depth_calibration.correct_final_score   （真实 offset 层）
    - mock_api.depth_eval_v4.DepthReviewer._apply_hard_verdict （真实裁决层）
  唯一无法真实重放的是 LLM 的 severity 标注（需要重跑模型），故用关键词启发式
  模拟「提示词修正后模型不再把常见局限标成 fatal」这一预期效果，并明确标注为估计。

用法：
    .venv/Scripts/python.exe _verify_fixes.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics as st
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mock_api.depth_calibration import correct_final_score  # noqa: E402
from mock_api.depth_eval_v4 import (  # noqa: E402
    FATAL_VETO_DOWNGRADE_FLOOR,
    FATAL_VETO_MIN,
    CritiquePoint,
    DepthReviewer,
    Q5cResult,
)

DB = ROOT / "mock_api" / "paperforge_mock.db"
OUT_DIR = ROOT / "deliverables" / "diag"

# 与提示词新定义对齐：「论证充分性」类问题 → minor（非 F1~F4）
COMMON_LIMITATION_RE = re.compile(
    "|".join([
        r"消融", r"ablation",
        r"缺[乏少]|未(与|和|对).{0,25}(对比|比较|验证)|没有(对比|基线)|基线(不足|单一|缺失)|缺乏对比",
        r"敏感性|sensitivity",
        r"理论|证明|bound|上界|下界|形式化",
        r"复现|可复现|细节(不足|缺失)|超参|未(说明|给出|提供|报告|披露)",
        r"数据(集)?(不足|单一|规模)|样本(不足|少)|规模(小|不足)|仅在一个|仅在仿真",
        r"创新(性)?(不足|薄弱|有限)",
        r"假设.{0,10}未|隐含假设|未验证",
        r"表述|写作|语言|排版|格式|夸大",
    ]),
    re.I,
)


def is_common_limitation(point: str) -> bool:
    return bool(COMMON_LIMITATION_RE.search(point or ""))


def main() -> None:
    print("=" * 78)
    print("修复后验证（真实生产代码路径）")
    print("=" * 78)
    print(f"生效阈值: FATAL_VETO_MIN={FATAL_VETO_MIN}  "
          f"FATAL_VETO_DOWNGRADE_FLOOR={FATAL_VETO_DOWNGRADE_FLOOR}")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT r.paper_id, r.final_verdict, r.q5a_result
        FROM depth_reviews_v4 r
        JOIN (
            SELECT paper_id, MAX(created_at) AS mx FROM depth_reviews_v4
            WHERE status='completed' AND version IN ('v4.1','v4.2')
              AND final_verdict IS NOT NULL AND final_verdict != ''
            GROUP BY paper_id
        ) t ON r.paper_id=t.paper_id AND r.created_at=t.mx
        """
    ).fetchall()
    meta = {r["id"]: (r["source"], r["year"])
            for r in cur.execute("SELECT id, source, year FROM papers")}

    reviewer = DepthReviewer(llm_func=lambda *a, **kw: "")

    old_scores, new_scores = [], []
    old_verdicts, new_verdicts = [], []
    veto_before = veto_after = 0
    fatal_total = fatal_common = 0

    for r in rows:
        try:
            fv = json.loads(r["final_verdict"])
        except Exception:
            continue
        raw = fv.get("calibrated_score_raw")
        if raw is None:
            continue
        q5a = r["q5a_result"] or {}
        try:
            q5a = json.loads(q5a) if isinstance(q5a, str) else q5a
        except Exception:
            q5a = {}
        pts = [c for c in (q5a.get("critique_points") or []) if isinstance(c, dict)]

        # ── 修复后：提示词修正 → 常见局限不再标 fatal ──
        new_cp, n_common = [], 0
        for c in pts:
            text = str(c.get("point") or "")
            sev = c.get("severity")
            if sev == "fatal":
                fatal_total += 1
                if is_common_limitation(text):
                    fatal_common += 1
                    n_common += 1
                    sev = "minor"  # 提示词修正后模型应标 minor
            new_cp.append(CritiquePoint(point=text[:40] or "问题", severity=sev))

        src, yr = meta.get(r["paper_id"], (None, None))
        paper_ctx = {"source": src, "year": yr}

        # ── 真实生产函数：offset 层 ──
        old_s = fv.get("calibrated_score")
        new_s = correct_final_score(raw, paper=paper_ctx)

        # ── 真实生产函数：裁决层 ──
        llm_v = fv.get("llm_verdict") or "major_revision"
        q5c_new = Q5cResult(
            calibrated_score=new_s, llm_verdict=llm_v, verdict=llm_v, delta=0.0
        )
        v_new = reviewer._apply_hard_verdict(q5c_new, new_cp).final_verdict

        if isinstance(old_s, (int, float)):
            old_scores.append(old_s)
        new_scores.append(new_s)
        old_verdicts.append(fv.get("final_verdict"))
        new_verdicts.append(v_new)

        # 否决计数：修复前看原 fatal 数，修复后看修正后 fatal 数
        if sum(1 for c in pts if c.get("severity") == "fatal") >= 2:
            veto_before += 1
        if sum(1 for c in new_cp if c.severity == "fatal") >= FATAL_VETO_MIN:
            veto_after += 1

    n = len(new_scores)
    print(f"\n样本论文数: {n}")
    print(f"fatal 条目: {fatal_total} 条，其中'常见局限'型 {fatal_common} "
          f"({fatal_common/max(1,fatal_total)*100:.1f}%) → 提示词修正后应回落为 minor")
    print(f"触发一票否决: 修复前 {veto_before} ({veto_before/n*100:.1f}%) "
          f"→ 修复后 {veto_after} ({veto_after/n*100:.1f}%)")

    print("\n" + "=" * 78)
    print(f"{'指标':<22}{'修复前':>14}{'修复后':>14}")
    print("=" * 78)
    print(f"{'均分':<22}{st.mean(old_scores)*100:>14.1f}{st.mean(new_scores)*100:>14.1f}")
    print(f"{'中位分':<22}{st.median(old_scores)*100:>14.1f}{st.median(new_scores)*100:>14.1f}")
    for lo in (70, 75, 80):
        print(f"{'≥'+str(lo)+'分论文数':<22}"
              f"{sum(1 for v in old_scores if v>=lo/100):>14}"
              f"{sum(1 for v in new_scores if v>=lo/100):>14}")

    print("\n" + "=" * 78)
    print("判决分布")
    print("=" * 78)
    keys = ["accept", "minor_revision", "major_revision", "reject"]
    oc, nc = Counter(old_verdicts), Counter(new_verdicts)
    print(f"{'判决':<18}{'修复前':>18}{'修复后':>18}")
    for k in keys:
        print(f"{k:<18}{oc.get(k,0):>10}({oc.get(k,0)/n*100:>4.1f}%)"
              f"{nc.get(k,0):>10}({nc.get(k,0)/n*100:>4.1f}%)")

    print("\n分档对比（卡片显示分）")
    print(f"{'分档':<12}{'修复前':>10}{'修复后':>10}")
    o_b, n_b = Counter(), Counter()
    for v in old_scores:
        o_b[int(v * 100 // 10) * 10] += 1
    for v in new_scores:
        n_b[int(v * 100 // 10) * 10] += 1
    for lo in range(20, 90, 10):
        print(f"{f'{lo}-{lo+9}':<12}{o_b.get(lo,0):>10}{n_b.get(lo,0):>10}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "fix_verification_20260916.json"
    out.write_text(json.dumps({
        "fatal_veto_min": FATAL_VETO_MIN,
        "fatal_veto_downgrade_floor": FATAL_VETO_DOWNGRADE_FLOOR,
        "sample_papers": n,
        "fatal_total": fatal_total,
        "fatal_common_limitation": fatal_common,
        "veto_before": veto_before,
        "veto_after": veto_after,
        "mean_before": round(st.mean(old_scores), 4),
        "mean_after": round(st.mean(new_scores), 4),
        "ge70_before": sum(1 for v in old_scores if v >= 0.7),
        "ge70_after": sum(1 for v in new_scores if v >= 0.7),
        "verdicts_before": dict(oc),
        "verdicts_after": dict(nc),
        "note": (
            "offset 层与裁决层调用真实生产函数；severity 重判为启发式估计"
            "（无法在无模型环境下真实重放 LLM 标注）。"
        ),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已落盘: {out}")


if __name__ == "__main__":
    main()
