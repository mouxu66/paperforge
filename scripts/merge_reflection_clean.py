"""合并「修复后重跑(21 篇)」与「可信组(21 篇)」并做污染校验。

- 重跑组：直接读取 reflection_rerun_fixed.csv（含 2026-08-05 全部新列）。
- 可信组：从 depth_reviews_v4.reflection_result 重建，归一化到同一 FIELDS 表头
  （旧 schema 把 4 维放在 scores.* 子字典，且缺 P1/P2 新列，这里补齐留空）。
- 产出 deliverables/reflection_final_clean.csv（42 行）并打印校验报告。

用法：
    python scripts/merge_reflection_clean.py \
        --rerun deliverables/reflection_rerun_fixed.csv \
        --out  deliverables/reflection_final_clean.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_DB_PATH = ROOT / "mock_api" / "paperforge_mock.db"

# 与 reflection_bench.py 完全一致，否则列对不齐。
FIELDS = [
    "sid", "name", "file", "paper_title", "bound_paper_id", "bound_status",
    "report_chars", "paper_chars",
    "understanding_accuracy", "analysis_depth", "innovative_insights",
    "evidence_support", "fidelity", "coverage", "average", "verdict",
    "copy_ratio", "stray_claims", "truncated", "fidelity_status",
    "coverage_status", "effective_evidence",
    "hardcoded_overrides", "llm_calls", "llm_empty", "elapsed_s",
    "ev_from_paper", "ev_not_found",
    "error",
]

# 本次修复后重跑的 21 篇（与 reflection_bench.py --only 一致）。
RERUN_SIDS = set(
    "999900000010 999900000011 999900000020 999900000024 999900000025 "
    "999900000027 999900000028 999900000029 999900000030 999900000032 "
    "999900000033 999900000034 999900000035 999900000036 999900000037 "
    "999900000038 999900000039 999900000040 999900000041 999900000042 "
    "999900000043".split()
)

# 旧可信组（imported_from=rerun_report.csv，评分有效，非污染）的 21 篇。
TRUSTED_SIDS = [
    "999900000001", "999900000002", "999900000003", "999900000004",
    "999900000005", "999900000006", "999900000007", "999900000008",
    "999900000009", "999900000012", "999900000013", "999900000014",
    "999900000015", "999900000016", "999900000017", "999900000018",
    "999900000019", "999900000021", "999900000022", "999900000023",
    "999900000026",
]


def _blank():
    return {k: "" for k in FIELDS}


def _row_from_rerun(row: dict) -> dict:
    out = _blank()
    for k in FIELDS:
        out[k] = row.get(k, "")
    return out


def _row_from_db(sid: str) -> dict:
    out = _blank()
    out["sid"] = sid
    con = sqlite3.connect(str(_DB_PATH))
    try:
        raw = con.execute(
            "select reflection_result from depth_reviews_v4 where paper_id=?",
            (f"reflection_{sid}",),
        ).fetchone()
        if not raw or not raw[0]:
            out["error"] = "no_db_row"
            return out
        d = json.loads(raw[0])
    except Exception as e:  # noqa: BLE001
        out["error"] = f"db_error:{e}"
        return out
    finally:
        con.close()

    out["name"] = d.get("student_name", "")
    out["bound_paper_id"] = d.get("source_arxiv_id") or d.get("bound_paper_id", "")
    out["report_chars"] = d.get("report_chars", "")
    out["paper_chars"] = d.get("paper_chars", "")
    scores = d.get("scores") or {}
    out["understanding_accuracy"] = scores.get("understanding_accuracy", "")
    out["analysis_depth"] = scores.get("analysis_depth", "")
    out["innovative_insights"] = scores.get("innovative_insights", "")
    out["evidence_support"] = scores.get("evidence_support", "")
    out["fidelity"] = d.get("fidelity", "")
    out["coverage"] = d.get("coverage", "")
    out["average"] = d.get("average", "")
    out["verdict"] = d.get("verdict", "")
    out["stray_claims"] = d.get("stray_claims_count", "")
    out["fidelity_status"] = d.get("fidelity_status", "")
    out["coverage_status"] = d.get("coverage_status", "")
    out["effective_evidence"] = d.get("effective_evidence_count", "")
    # P1/P2 新列在旧 schema 中不存在 → 留空（表示「未重测」）。
    return out


def _is_all_zero_three(row: dict) -> bool:
    """污染特征：4 维全 0.3 且 average 0.3。"""
    def f(x):
        try:
            return abs(float(x) - 0.3) < 1e-9
        except (TypeError, ValueError):
            return False
    dims = [row.get(k, "") for k in
            ("understanding_accuracy", "analysis_depth",
             "innovative_insights", "evidence_support")]
    if all(f(x) for x in dims) and f(row.get("average", "")):
        return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rerun", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    # 1) 重跑组
    rerun_rows = []
    with open(args.rerun, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rerun_rows.append(_row_from_rerun(r))
    rerun_sids = {r["sid"] for r in rerun_rows}
    print(f"[merge] 重跑组读取: {len(rerun_rows)} 行, sids={sorted(rerun_sids)}")

    # 2) 可信组
    trusted_rows = [_row_from_db(s) for s in TRUSTED_SIDS]
    print(f"[merge] 可信组重建: {len(trusted_rows)} 行")

    # 3) 合并（重跑在前，可信在后；顺序与学号无关，仅分组可读）
    all_rows = rerun_rows + trusted_rows

    # 4) 校验
    problems = []
    for r in all_rows:
        if not str(r.get("report_chars", "")).strip() or str(r.get("report_chars")) == "0":
            problems.append((r["sid"], "report_chars 为空/0"))
        if _is_all_zero_three(r):
            problems.append((r["sid"], "全 0.3 污染特征"))
        # error 列里只有非 llm_failed 的才是真正异常（llm_failed 是已知留空，不致命）
        err = str(r.get("error", "")).strip()
        if err and err != "llm_failed":
            problems.append((r["sid"], f"error={err}"))
    # 仅对重跑组检查 ev_from_paper（可信组未重测，留空不算污染）
    ev_paper_hits = [(r["sid"], r["ev_from_paper"]) for r in rerun_rows
                     if str(r.get("ev_from_paper", "")).strip()
                     and str(r.get("ev_from_paper")) not in ("0", "0.0")]
    if ev_paper_hits:
        problems.append(("REALERT", f"重跑组 ev_from_paper 非零: {ev_paper_hits}"))

    # 5) 写出
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in all_rows:
            w.writerow(r)

    # 6) 报告
    print(f"\n===== 合并校验报告 =====")
    print(f"总行数: {len(all_rows)} (重跑 {len(rerun_rows)} + 可信 {len(trusted_rows)})")
    llm_failed = [r["sid"] for r in rerun_rows if str(r.get("error", "")).strip() == "llm_failed"]
    if llm_failed:
        print(f"⚠ 重跑组 LLM 故障(已留空未写0.3, 纳入交付但需人工补评): {llm_failed}")
    else:
        print("✓ 重跑组无 LLM 故障(error 全空)")
    if ev_paper_hits:
        print(f"⚠ 重跑组存在 ev_from_paper>0（模型仍引原论文）: {ev_paper_hits}")
    else:
        print("✓ 重跑组 ev_from_paper 全 0（无引原论文污染）")
    if problems:
        print(f"\n❌ 发现问题 {len(problems)} 项:")
        for sid, msg in problems:
            print(f"   - {sid}: {msg}")
        sys.exit(2)
    else:
        print("\n✅ 校验通过：无全 0.3 污染、report_chars 均 >0、可信组来源干净")
        print(f"   输出: {args.out}")


if __name__ == "__main__":
    main()
