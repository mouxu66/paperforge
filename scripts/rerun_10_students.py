"""Re-score 10 specific student reflection reports with full diagnostics.

用法：从项目根目录运行
    PAPERFORGE_BENCH_NO_LLM=1 python scripts/rerun_10_students.py

去掉 PAPERFORGE_BENCH_NO_LLM=1 即可跑真实 LLM 评分。
"""

from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mock_api.reflection_pipeline import analyze_reflection_file
from mock_api.database import SessionLocal

UPLOADS = ROOT / "mock_api" / "uploads"
OUT_CSV = ROOT / "deliverables" / "rerun_10_scores.csv"

# 用户指定的10个学号 → 文件映射
STUDENTS = {
    "999900000009": "reflection_008_999900000009-学生09.docx",
    "999900000001": "reflection_000_999900000001-学生01.docx",
    "999900000024": "reflection_022_999900000024-学生23.docx",
    "999900000041": "reflection_038_999900000041-学生39（1）.docx",
    "999900000006": "reflection_005_999900000006-学生06.docx",
    "999900000015": "reflection_014_999900000015-学生15（1）.docx",
    "999900000003": "reflection_002_999900000003-学生04-实验报告.docx",
    "999900000037": "reflection_034_999900000037-学生35（1）.docx",
    "999900000019": "reflection_018_999900000019-学生19.docx",
    "999900000012": "reflection_011_999900000012-学生12（1）.docx",
}


def main():
    results = []
    db = SessionLocal()

    for sid, fname in STUDENTS.items():
        fpath = UPLOADS / fname
        if not fpath.exists():
            print(f"[SKIP] {sid}: file not found: {fpath}")
            continue

        print(f"\n{'='*60}")
        print(f"[{sid}] {fname}")
        t0 = time.time()

        try:
            result = analyze_reflection_file(str(fpath), db)
            elapsed = time.time() - t0

            scores = result.get("scores", {})
            avg = result.get("average")
            verdict = result.get("verdict", "")
            overrides = result.get("hardcoded_overrides", [])
            ev_count = result.get("effective_evidence_count")
            llm_failed = result.get("llm_failed", False)
            copy_ratio = result.get("copy_ratio")
            copy_crushed = result.get("copy_crushed", False)
            fidelity = result.get("fidelity")
            coverage = result.get("coverage")
            ev_rej = result.get("evidence_rejections", {})

            print(f"  Time: {elapsed:.1f}s")
            print(f"  Avg: {avg}  Verdict: {verdict}")
            print(f"  4D: ua={scores.get('understanding_accuracy'):.3f} ad={scores.get('analysis_depth'):.3f} ii={scores.get('innovative_insights'):.3f} es={scores.get('evidence_support'):.3f}")
            print(f"  Fidelity: {fidelity:.3f}  Coverage: {coverage:.3f}")
            print(f"  Copy Ratio: {copy_ratio}  Copy Crushed: {copy_crushed}")
            print(f"  Evidence Count: {ev_count}  LLM Failed: {llm_failed}")
            print(f"  Evidence Rejections: {ev_rej}")
            if overrides:
                for ov in overrides:
                    print(f"  Override: {ov}")
            print(f"  Weights: {result.get('weights', {})}")

            results.append({
                "学号": sid,
                "文件名": fname,
                "avg": round(avg, 4) if avg is not None else None,
                "verdict": verdict,
                "理解准确性": round(scores.get("understanding_accuracy") or 0, 3),
                "分析深度": round(scores.get("analysis_depth") or 0, 3),
                "创新见解": round(scores.get("innovative_insights") or 0, 3),
                "证据支撑": round(scores.get("evidence_support") or 0, 3),
                "忠实度": round(fidelity or 0, 3),
                "覆盖度": round(coverage or 0, 3),
                "照抄率": round(copy_ratio or 0, 3) if copy_ratio is not None else None,
                "照抄封杀": copy_crushed,
                "有效证据数": ev_count,
                "LLM失败": llm_failed,
                "重载规则": "; ".join(overrides) if overrides else "",
                "耗时s": round(elapsed, 1),
            })
        except Exception as e:
            print(f"  [ERROR] {e}")
            import traceback
            traceback.print_exc()
            results.append({"学号": sid, "文件名": fname, "error": str(e)})

    db.close()

    # 输出 CSV
    if results:
        OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)
        print(f"\nCSV written: {OUT_CSV}")

    # 汇总
    valid = [r for r in results if r.get("avg") is not None]
    if valid:
        avgs = [r["avg"] for r in valid]
        print(f"\n总计: {len(valid)}/{len(results)} 成功")
        print(f"平均分: {sum(avgs)/len(avgs):.4f}")
        print(f"范围: {min(avgs):.4f} ~ {max(avgs):.4f}")


if __name__ == "__main__":
    main()
