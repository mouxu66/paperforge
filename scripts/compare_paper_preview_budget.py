"""对比动态预算前后（原论文预览 4000 字 vs ≤16000 字）同一批感悟报告评审分数的变化。

背景（2026-08-11 动态预算改造）：
  - before：PAPER_PREVIEW_BUDGET_RATIO=0  → 论文预览固定为基础 4000 字（无全文补充）
  - after ：PAPER_PREVIEW_BUDGET_RATIO=1.0 → 论文预览 = min(16000, 4000 + 报告省下字数)

方法：
  1. 数据：mock_api/paperforge_mock.db 中所有绑定了 source_paper_id 且原论文
     有 full_text 的感悟报告（同批 41 篇）。
  2. 同一篇报告、同一个 LLM、固定 seed/temp，唯一变量是原论文预览长度；
     不带 paper_supplement（隔离「预览长度」这一个变量，避免 P9 全文补充的干扰）。
  3. 对比 LLM 4 维（understanding_accuracy 等）；coverage 是确定性向量计算
     （同一输入必然相同），作为「不应变化」的对照维一并输出。
  4. 输出：deliverables/paper_preview_budget_compare.csv + stdout 汇总。

用法：
  .venv/Scripts/python.exe scripts/compare_paper_preview_budget.py [--limit N] [--ids id,id]
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 固定 seed / 温度 → 同 prompt 输出确定；跳过交叉验证加分（省一半 LLM 调用）。
# watchdog 120s 会杀掉本机 9B 的长评审调用（返回 None → 走重试 → 结果漂移），
# 提到 300s 让调用完整跑完（实测：120s 时同 prompt 两遍结果不同，300s 时完全一致）。
os.environ.setdefault("PAPERFORGE_EVAL_SEED", "42")
os.environ.setdefault("PAPERFORGE_LLM_TEMPERATURE", "0")
os.environ.setdefault("PAPERFORGE_REFLECTION_SKIP_CROSSVAL", "1")
os.environ.setdefault("PAPERFORGE_LLM_WATCHDOG_TIMEOUT", "300")
os.environ.setdefault("PAPERFORGE_LLM_REQUEST_TIMEOUT", "280")

OUT_CSV = ROOT / "deliverables" / "paper_preview_budget_compare.csv"

DIMENSIONS = (
    "understanding_accuracy",
    "analysis_depth",
    "innovative_insights",
    "evidence_support",
)

_SECTION_MARKERS = ["一、", "二、", "三、", "四、"]


def split_report_sections(text: str) -> dict:
    """把报告全文粗切为 q/tech/exp/reflection 四段（供 coverage 计算；无标记则整段为 q）。"""
    hits = [(m, text.find(m)) for m in _SECTION_MARKERS]
    hits = [(m, i) for m, i in hits if i >= 0]
    if len(hits) < 2:
        return {"q": text or ""}
    hits.sort(key=lambda x: x[1])
    keys = ["q", "tech", "exp", "reflection"]
    sections = {}
    for k, (m, i) in enumerate(hits):
        end = hits[k + 1][1] if k + 1 < len(hits) else len(text)
        sections[keys[k]] = text[i:end]
    return sections


def load_bound_reports(limit: int | None, ids: set[str] | None):
    """从 DB 取绑定了原论文（且原论文有全文）的感悟报告。"""
    import sqlite3

    db = ROOT / "mock_api" / "paperforge_mock.db"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute(
        """
        SELECT p.id, p.full_text, p.source_paper_id, s.full_text AS src_full, s.title AS src_title
        FROM papers p
        JOIN papers s ON s.id = p.source_paper_id
        WHERE p.category = 'report'
          AND p.full_text IS NOT NULL AND length(p.full_text) > 100
          AND s.full_text IS NOT NULL AND length(s.full_text) > 1000
        ORDER BY p.id
        """
    )
    rows = cur.fetchall()
    conn.close()
    if ids:
        rows = [r for r in rows if r[0] in ids]
    if limit:
        rows = rows[:limit]
    return rows


def run_one(reviewer, der, report_id, title, full_text, src_full):
    """同一篇报告在 before(ratio=0) / after(ratio=1.0) 下各评审一次。"""
    out = {}

    der.PAPER_PREVIEW_BUDGET_RATIO = 0.0
    r0 = reviewer.review(
        report_id, title, full_text, paper_text=src_full, paper_supplement=""
    )
    out["preview_before"] = getattr(r0, "paper_preview_chars", 0)
    out["verdict_before"] = r0.verdict
    out["avg_before"] = (r0.scores or {}).get("average")
    for d in DIMENSIONS:
        out[f"{d}_before"] = (r0.scores or {}).get(d)

    der.PAPER_PREVIEW_BUDGET_RATIO = 1.0
    r1 = reviewer.review(
        report_id, title, full_text, paper_text=src_full, paper_supplement=""
    )
    out["preview_after"] = getattr(r1, "paper_preview_chars", 0)
    out["verdict_after"] = r1.verdict
    out["avg_after"] = (r1.scores or {}).get("average")
    for d in DIMENSIONS:
        out[f"{d}_after"] = (r1.scores or {}).get(d)

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 篇（调试用）")
    parser.add_argument("--offset", type=int, default=0, help="跳过前 M 篇（分块续跑）")
    parser.add_argument("--ids", type=str, default=None, help="逗号分隔的报告 id 白名单")
    parser.add_argument("--no-coverage", action="store_true", help="跳过 coverage 计算（省嵌入时间）")
    args = parser.parse_args()

    ids = set(x.strip() for x in args.ids.split(",")) if args.ids else None
    rows = load_bound_reports(None, ids)
    if args.offset:
        rows = rows[args.offset:]
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("没有找到绑定原论文且有全文的感悟报告")
        return
    print(f"共 {len(rows)} 篇报告参与对比（before: preview=4000 / after: preview≤16000）")

    from mock_api.depth_eval_reflection import ReflectionReviewer
    import mock_api.depth_eval_reflection as der

    reviewer = ReflectionReviewer()

    # CSV 增量写入：每篇完成后立即落盘（块内超时也不丢已跑结果）
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    csv_handle = open(OUT_CSV, "a", encoding="utf-8-sig", newline="")
    csv_writer: csv.DictWriter | None = None
    n_written = 0

    for i, (rid, rep_text, src_pid, src_full, src_title) in enumerate(rows, 1):
        print(f"\n[{i}/{len(rows)}] {rid}（论文 {len(src_full)} 字）", flush=True)
        try:
            r = run_one(reviewer, der, rid, src_title or rid, rep_text, src_full)
        except Exception as e:  # noqa: BLE001 - 单篇失败不中断整批
            print(f"  [review fail] {type(e).__name__}: {e}", flush=True)
            continue

        # coverage：确定性计算，before/after 共用一次
        coverage = None
        if not args.no_coverage:
            try:
                from mock_api.reflection_fidelity import compute_coverage

                cov = compute_coverage(split_report_sections(rep_text), src_full)
                coverage = cov.coverage
            except Exception as e:  # noqa: BLE001 - coverage 失败不影响对比
                print(f"  [coverage fail] {type(e).__name__}: {e}", flush=True)

        row = {
            "report_id": rid,
            "paper_title": (src_title or "")[:80],
            "report_chars": len(rep_text),
            "paper_chars": len(src_full),
            "coverage": coverage,
            **r,
        }
        if csv_writer is None:
            csv_writer = csv.DictWriter(csv_handle, fieldnames=list(row.keys()))
            if OUT_CSV.stat().st_size == 0:
                csv_writer.writeheader()
        csv_writer.writerow(row)
        csv_handle.flush()
        n_written += 1
        print(
            f"  preview {row['preview_before']} → {row['preview_after']} 字 | "
            f"UA {row['understanding_accuracy_before']} → {row['understanding_accuracy_after']} | "
            f"avg {row['avg_before']} → {row['avg_after']} | "
            f"verdict {row['verdict_before']} → {row['verdict_after']}",
            flush=True,
        )

    csv_handle.close()
    print(f"\n本块 {n_written} 篇已写入: {OUT_CSV}", flush=True)
    if n_written:
        summarize(OUT_CSV)


def summarize(csv_path: Path) -> None:
    """对已累积的对比 CSV 输出汇总（读完所有块后调用）。"""
    import csv as _csv

    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        rows = list(_csv.DictReader(f))
    n = len(rows)
    if not n:
        print("CSV 为空，无汇总")
        return

    def _mean(vals):
        vals = [v for v in vals if v is not None and v != ""]
        vals = [float(v) for v in vals]
        return sum(vals) / len(vals) if vals else float("nan")

    def _num(row, key):
        v = row.get(key)
        if v in (None, ""):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    print("\n===== 汇总（before: preview=4000 → after: preview≤16000）=====")
    print(f"报告数: {n}")

    print("\n维度均值变化（after - before）:")
    for d in DIMENSIONS:
        before = _mean([_num(r, f"{d}_before") for r in rows])
        after = _mean([_num(r, f"{d}_after") for r in rows])
        changed = sum(1 for r in rows if _num(r, f"{d}_before") != _num(r, f"{d}_after"))
        up = sum(1 for r in rows if (_num(r, f"{d}_after") or 0) > (_num(r, f"{d}_before") or 0))
        down = sum(1 for r in rows if (_num(r, f"{d}_after") or 0) < (_num(r, f"{d}_before") or 0))
        print(
            f"  {d:26s} {before:.3f} → {after:.3f} (Δ {after - before:+.3f}) | "
            f"变动 {changed}/{n}（↑{up} ↓{down}）"
        )

    avg_before = _mean([_num(r, "avg_before") for r in rows])
    avg_after = _mean([_num(r, "avg_after") for r in rows])
    print(f"  {'average':26s} {avg_before:.3f} → {avg_after:.3f} (Δ {avg_after - avg_before:+.3f})")

    vc = sum(1 for r in rows if r.get("verdict_before") != r.get("verdict_after"))
    print(f"\nverdict 变化: {vc}/{n}")

    covs = [c for c in (_num(r, "coverage") for r in rows) if c is not None]
    if covs:
        print(f"coverage（确定性，不应变化）: 均值 {_mean(covs):.3f}，"
              f"前后共用同一份计算 → Δ=0（对照维）")

    p_avg_b = _mean([_num(r, "preview_before") for r in rows])
    p_avg_a = _mean([_num(r, "preview_after") for r in rows])
    print(f"\n原论文预览字数均值: {p_avg_b:.0f} → {p_avg_a:.0f} 字")


if __name__ == "__main__":
    main()
    # watchdog 被杀的 LLM 调用会留下孤儿线程，阻塞 Python 正常退出 →
    # 批跑脚本直接强制退出，避免每次干等 10 分钟（数据已逐行落盘）。
    os._exit(0)
