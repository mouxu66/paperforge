"""存量评审分数回填：把 2026-09-16 四项修复应用到历史记录。

背景
----
四项修复（offset 覆盖 bug / Q5a 严重度 / 一票否决阈值 / delta 对称性）只作用于
**新审稿**。存量 695 条 `depth_reviews_v4` 仍带着被错误下压的分数，
卡片因此继续显示 20~60 分。本脚本按修复后的逻辑重算。

口径
----
- **只重算分数与判决**，调用真实生产函数：
    - `mock_api.depth_calibration.correct_final_score`（offset 层）
    - `mock_api.depth_eval_v4.DepthReviewer._apply_hard_verdict`（裁决层）
- **不动 `q5a_result`**：fatal 标签是 LLM 当时的原始输出。修正它们需要重跑模型
  （提示词已改，但历史标签只能靠重审刷新）。故本脚本的一票否决仍基于**旧** fatal 计数，
  受益仅限于「门槛 2→3」这一项。
- **可逆**：原值写入 `final_verdict["_pre_fix_20260916"]`，重复运行会跳过已回填行。
- **可审计**：逐行记录 old/new，落盘 JSON（P5：模型可见 ⟺ 有日志）。

用法
----
    .venv/Scripts/python.exe backfill_scores_after_fix.py --dry-run   # 只看影响面
    .venv/Scripts/python.exe backfill_scores_after_fix.py --apply     # 实际写入
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as st
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from mock_api.depth_calibration import correct_final_score  # noqa: E402
from mock_api.depth_eval_v4 import (  # noqa: E402
    FATAL_VETO_MIN,
    CritiquePoint,
    DepthReviewer,
    Q5cResult,
)

DB = ROOT / "mock_api" / "paperforge_mock.db"
OUT_DIR = ROOT / "deliverables" / "diag"
MARKER = "_pre_fix_20260916"


def build_critique_points(q5a_raw) -> list[CritiquePoint]:
    """从已存 q5a_result 还原 critique_points（不修改 severity）。"""
    if not q5a_raw:
        return []
    try:
        d = json.loads(q5a_raw) if isinstance(q5a_raw, str) else q5a_raw
    except Exception:
        return []
    out: list[CritiquePoint] = []
    for c in (d.get("critique_points") or []):
        if not isinstance(c, dict):
            continue
        text = str(c.get("point") or "").strip()
        sev = c.get("severity")
        if sev not in ("fatal", "minor"):
            sev = "minor"
        if text:
            out.append(CritiquePoint(point=text[:40], severity=sev))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="存量评审分数回填（修复后口径）")
    ap.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写入（默认行为）")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    print("=" * 78)
    print(f"存量分数回填 ｜ 模式={'APPLY（写入）' if apply else 'DRY-RUN（只读）'}")
    print(f"生效阈值: FATAL_VETO_MIN={FATAL_VETO_MIN}")
    print("=" * 78)

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    meta = {r["id"]: (r["source"], r["year"])
            for r in cur.execute("SELECT id, source, year FROM papers")}

    rows = cur.execute(
        """SELECT id, paper_id, final_verdict, q5a_result, created_at
           FROM depth_reviews_v4
           WHERE status='completed' AND final_verdict IS NOT NULL AND final_verdict != ''"""
    ).fetchall()
    print(f"待处理评审记录: {len(rows)} 条")

    reviewer = DepthReviewer(llm_func=lambda *a, **kw: "")

    changes = []
    skipped_already = skipped_no_raw = 0
    old_scores, new_scores = [], []
    old_verdicts, new_verdicts = [], []

    for r in rows:
        try:
            fv = json.loads(r["final_verdict"])
        except Exception:
            skipped_no_raw += 1
            continue
        if MARKER in fv:
            skipped_already += 1
            continue
        raw = fv.get("calibrated_score_raw")
        if not isinstance(raw, (int, float)):
            skipped_no_raw += 1
            continue

        src, yr = meta.get(r["paper_id"], (None, None))
        new_score = correct_final_score(raw, paper={"source": src, "year": yr})

        cps = build_critique_points(r["q5a_result"])
        llm_v = fv.get("llm_verdict") or "major_revision"
        q5c = Q5cResult(calibrated_score=new_score, llm_verdict=llm_v, verdict=llm_v, delta=0.0)
        fv_new = reviewer._apply_hard_verdict(q5c, cps)

        old_score = fv.get("calibrated_score")
        old_verdict = fv.get("final_verdict")
        if isinstance(old_score, (int, float)):
            old_scores.append(old_score)
        new_scores.append(new_score)
        old_verdicts.append(old_verdict)
        new_verdicts.append(fv_new.final_verdict)

        changed = (
            (not isinstance(old_score, (int, float)))
            or abs(old_score - new_score) > 1e-9
            or old_verdict != fv_new.final_verdict
        )
        if changed:
            changes.append({
                "review_id": r["id"],
                "paper_id": r["paper_id"],
                "old_score": old_score,
                "new_score": round(new_score, 4),
                "old_verdict": old_verdict,
                "new_verdict": fv_new.final_verdict,
                "new_override_reason": fv_new.override_reason,
                "preserved": {
                    "calibrated_score": old_score,
                    "final_verdict": old_verdict,
                    "override_reason": fv.get("override_reason"),
                },
            })

    n = len(new_scores)
    print(f"已回填过(跳过): {skipped_already} ｜ 无 raw 分数(跳过): {skipped_no_raw}")
    print(f"需要变更: {len(changes)} 条")

    if n:
        print("\n" + "=" * 78)
        print(f"{'指标':<20}{'回填前':>14}{'回填后':>14}")
        print("=" * 78)
        print(f"{'均分':<20}{st.mean(old_scores)*100:>14.1f}{st.mean(new_scores)*100:>14.1f}")
        print(f"{'中位分':<20}{st.median(old_scores)*100:>14.1f}{st.median(new_scores)*100:>14.1f}")
        for lo in (70, 75, 80):
            print(f"{'≥'+str(lo)+'分':<20}"
                  f"{sum(1 for v in old_scores if v>=lo/100):>14}"
                  f"{sum(1 for v in new_scores if v>=lo/100):>14}")

        print("\n判决分布")
        keys = ["accept", "minor_revision", "major_revision", "reject"]
        oc, nc = Counter(old_verdicts), Counter(new_verdicts)
        print(f"{'判决':<18}{'回填前':>16}{'回填后':>16}")
        for k in keys:
            print(f"{k:<18}{oc.get(k,0):>8}({oc.get(k,0)/n*100:>4.1f}%)"
                  f"{nc.get(k,0):>8}({nc.get(k,0)/n*100:>4.1f}%)")

    if apply and changes:
        for c in changes:
            row = cur.execute(
                "SELECT final_verdict FROM depth_reviews_v4 WHERE id=?", (c["review_id"],)
            ).fetchone()
            fv = json.loads(row["final_verdict"])
            fv[MARKER] = c["preserved"]
            fv["calibrated_score"] = c["new_score"]
            fv["final_verdict"] = c["new_verdict"]
            fv["override_reason"] = c["new_override_reason"]
            fv["_backfilled_at"] = datetime.now().isoformat(timespec="seconds")
            cur.execute(
                "UPDATE depth_reviews_v4 SET final_verdict=? WHERE id=?",
                (json.dumps(fv, ensure_ascii=False), c["review_id"]),
            )
        conn.commit()
        print(f"\n✅ 已写入 {len(changes)} 条")
    elif apply:
        print("\n无变更需要写入")
    else:
        print("\n（DRY-RUN，未写入任何数据；加 --apply 执行）")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"score_backfill_{stamp}.json"
    out.write_text(json.dumps({
        "mode": "apply" if apply else "dry-run",
        "timestamp": stamp,
        "fatal_veto_min": FATAL_VETO_MIN,
        "total_reviews": len(rows),
        "changed": len(changes),
        "skipped_already_backfilled": skipped_already,
        "skipped_no_raw": skipped_no_raw,
        "mean_before": round(st.mean(old_scores), 4) if old_scores else None,
        "mean_after": round(st.mean(new_scores), 4) if n else None,
        "verdicts_before": dict(Counter(old_verdicts)),
        "verdicts_after": dict(Counter(new_verdicts)),
        "changes": changes,
        "note": (
            "只重算 calibrated_score 与 final_verdict；q5a_result（fatal 标签）保持原样，"
            "修正它需要重跑模型。原值存于 final_verdict['_pre_fix_20260916']，可逆。"
        ),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")
    conn.close()


if __name__ == "__main__":
    main()
