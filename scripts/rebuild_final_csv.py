#!/usr/bin/env python3
"""从 DB 重建干净的反思报告评分 CSV（替代破旧的 reflection_anchored.csv）。

数据来源：
  - 评分核心列（4 维 / average / verdict / fidelity / coverage / report_chars 等）
    取自 `depth_reviews_v4.reflection_result` JSON（8/3 全量重导入，已是真实展开分数，无全 0.3 污染）。
  - 文档结构列（file / paper_title / copy_ratio / truncated）取自原 anchored CSV（结构性事实，非 LLM 分数，可复用）。

校验：
  - report_chars > 0（排除导出 bug 导致的全 0）
  - 4 维不全为 0.3（排除 P2 失效模式：引错来源→0.3 封顶）
  - average 在合理区间
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3

DB = "mock_api/paperforge_mock.db"
ANCHORED = "deliverables/reflection_anchored.csv"
OUT = "deliverables/reflection_final_clean.csv"

FIELDS = [
    "sid", "name", "file", "paper_title", "bound_paper_id", "bound_status",
    "report_chars", "paper_chars",
    "understanding_accuracy", "analysis_depth", "innovative_insights", "evidence_support",
    "fidelity", "coverage", "average", "verdict",
    "copy_ratio", "stray_claims", "truncated",
    "fidelity_status", "coverage_status",
    "effective_evidence", "hardcoded_overrides",
    "llm_calls", "llm_empty", "elapsed_s", "ev_from_paper", "ev_not_found",
    "error",
]


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_anchored():
    """sid -> {file, paper_title, copy_ratio, truncated}（结构性字段）"""
    out = {}
    if not os.path.exists(ANCHORED):
        return out
    with open(ANCHORED, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            sid = (r.get("sid") or "").strip()
            if not sid:
                continue
            out[sid] = {
                "file": r.get("file", ""),
                "paper_title": r.get("paper_title", ""),
                "copy_ratio": r.get("copy_ratio", ""),
                "truncated": r.get("truncated", ""),
            }
    return out


def build_row(sid: str, d: dict, anchored: dict) -> dict:
    sc = d.get("scores") or {}
    a = anchored.get(sid, {})
    row = {k: "" for k in FIELDS}
    row["sid"] = sid
    row["name"] = d.get("student_name") or a.get("name", "")
    row["file"] = a.get("file", "")
    row["paper_title"] = a.get("paper_title", "")
    row["bound_paper_id"] = d.get("source_arxiv_id", "")
    row["bound_status"] = "ok" if d.get("source_arxiv_id") else ""
    row["report_chars"] = d.get("report_chars", "")
    row["paper_chars"] = d.get("paper_chars", "")
    row["understanding_accuracy"] = sc.get("understanding_accuracy", "")
    row["analysis_depth"] = sc.get("analysis_depth", "")
    row["innovative_insights"] = sc.get("innovative_insights", "")
    row["evidence_support"] = sc.get("evidence_support", "")
    row["fidelity"] = d.get("fidelity", "")
    row["coverage"] = d.get("coverage", "")
    row["average"] = d.get("average", "")
    row["verdict"] = d.get("verdict", "")
    row["copy_ratio"] = a.get("copy_ratio", "")
    row["stray_claims"] = d.get("stray_claims_count", "")
    row["truncated"] = a.get("truncated", "")
    row["fidelity_status"] = d.get("fidelity_status", "")
    row["coverage_status"] = d.get("coverage_status", "")
    row["effective_evidence"] = d.get("effective_evidence_count", "")
    # 以下字段在 8/3 导入 schema 中不存在，留空（非污染，仅缺诊断列）
    row["hardcoded_overrides"] = ""
    row["llm_calls"] = ""
    row["llm_empty"] = ""
    row["elapsed_s"] = ""
    row["ev_from_paper"] = ""
    row["ev_not_found"] = ""
    row["error"] = ""
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--anchored", default=ANCHORED)
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    anchored = load_anchored()
    con = sqlite3.connect(args.db)
    # 取所有 reflection_<sid> 行
    rows = []
    for (pid,) in con.execute(
        "select paper_id from depth_reviews_v4 where paper_id like 'reflection_%'"
    ):
        sid = pid.replace("reflection_", "")
        raw = con.execute(
            "select reflection_result from depth_reviews_v4 where paper_id=?", (pid,)
        ).fetchone()
        if not raw or not raw[0]:
            continue
        try:
            d = json.loads(raw[0])
        except Exception:
            continue
        rows.append((sid, d))
    con.close()

    out_rows = []
    problems = []
    for sid, d in rows:
        if sid == "999900000020":
            # 123 无任何数据（无 docx、DB 无 full_text，原 anchored 那行 avg=0.18 是垃圾）→ 剔除
            problems.append((sid, "EXCLUDED: 无 docx/无 DB 数据（原始垃圾行）"))
            continue
        row = build_row(sid, d, anchored)
        # 校验
        rc = _to_float(row["report_chars"])
        if rc is None or rc <= 0:
            problems.append((sid, "report_chars 为空/<=0（导出 bug 特征）"))
        dims = [_to_float(row[k]) for k in
                ("understanding_accuracy", "analysis_depth",
                 "innovative_insights", "evidence_support")]
        if all(x is not None and abs(x - 0.3) < 1e-9 for x in dims):
            problems.append((sid, "⚠ 4 维全为 0.3（P2 失效模式疑似）"))
        avg = _to_float(row["average"])
        if avg is not None and not (0.0 <= avg <= 1.0):
            problems.append((sid, f"average 越界: {avg}"))
        out_rows.append(row)

    # 按 sid 排序
    out_rows.sort(key=lambda r: r["sid"])

    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)

    print(f"\n===== 重建校验报告 =====")
    print(f"导出总行数: {len(out_rows)}（已剔除 123）")
    if problems:
        print(f"⚠ 发现问题 {len(problems)} 项:")
        for sid, msg in problems:
            print(f"   {sid}: {msg}")
    else:
        print("✓ 无 report_chars=0、无 4 维全 0.3、average 均合法")
    print(f"输出: {args.out}")


if __name__ == "__main__":
    main()
