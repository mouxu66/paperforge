"""感悟报告批量评测驱动（基准/修复对比用）。

从桌面「Word文档」目录读取每篇感悟报告 docx，逐篇跑完整分析
（解析 → 绑定原论文 → fidelity/coverage → 4 维 → 6 维融合），
输出 CSV。同一脚本可用于「修复前基线」与「修复后」两次运行，再 --compare 对比。

用法：
    python scripts/reflection_bench.py --out deliverables/reflection_baseline.csv
    python scripts/reflection_bench.py --out deliverables/reflection_fixed.csv
    python scripts/reflection_bench.py --compare a.csv b.csv

选项：
    --source-dir  报告目录（默认 ~/Desktop/Word文档）
    --out         CSV 输出路径
    --compare     对比两份 CSV（此时忽略 --out）
    --no-llm      跳过 LLM 4 维（fidelity/coverage 仍算，4 维用结构启发式）——开发自检用
    --only <sid>  只跑指定学号（用逗号分隔）
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PAPERFORGE_LOCAL_ML", "1")  # 优先本地 ONNX 多语言模型
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_STUDENT_RE = re.compile(r"(\d{12})")  # 文件名需含 12 位学号
_SKIP_NAMES = {"实验报告.docx", "实验报告.doc"}
_DB_PATH = ROOT / "mock_api" / "paperforge_mock.db"


def load_bindings() -> dict[str, str]:
    """从 depth_reviews_v4 读回历史绑定（source_arxiv_id / bound_paper_id）。

    报告题目从 docx 头部提取不可靠，历史批阅已保存正确绑定（学生→arXiv ID），
    这里直接复用，避免 match_paper_by_title 误绑/漏绑。
    """
    import sqlite3

    bind = {}
    if not _DB_PATH.exists():
        return bind
    conn = sqlite3.connect(str(_DB_PATH))
    try:
        for pid, rr in conn.execute(
            "select paper_id, reflection_result from depth_reviews_v4 where paper_id like 'reflection_%'"
        ):
            sid = pid.replace("reflection_", "", 1)
            if not rr:
                continue
            import json as _json

            try:
                d = _json.loads(rr)
            except Exception:  # noqa: BLE001
                continue
            src = d.get("source_arxiv_id") or d.get("bound_paper_id")
            if src:
                bind[sid] = src
    finally:
        conn.close()
    return bind

FIELDS = [
    "sid", "name", "file", "paper_title", "bound_paper_id", "bound_status",
    "report_chars", "paper_chars",
    "understanding_accuracy", "analysis_depth", "innovative_insights",
    "evidence_support", "fidelity", "coverage", "average", "verdict",
    "copy_ratio", "stray_claims", "truncated", "fidelity_status",
    "coverage_status", "effective_evidence",
    # —— 2026-08-05 新增：LLM 侧可观测性 ——
    # 没有这几列，R1 触发的 0.3 分与「学生报告真没证据」在 CSV 里长得一模一样，
    # 41 篇批次里 19 篇污染就是因此才拖到事后才被发现。
    "hardcoded_overrides", "llm_calls", "llm_empty", "elapsed_s",
    # ev_from_paper > 0 = 模型把注入的原论文当成报告去引证（系统侧问题），
    # ev_not_found > 0 = 引文改写/拼接/编造。二者的处置完全不同。
    "ev_from_paper", "ev_not_found",
    "error",
]

# LLM 直接产出的 4 维 + 由其推导的总分/结论。
# LLM 故障（超时空返回 / JSON 解析失败）时这些值是系统故障的产物，
# 不代表报告质量，必须留空而不是写 0.3——否则会被当成真实低分统计进去。
_LLM_DERIVED_FIELDS = (
    "understanding_accuracy", "analysis_depth", "innovative_insights",
    "evidence_support", "average", "verdict",
)


def _round(v, n=3):
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return round(float(v), n)
    return v


def row_from_result(sid: str, name: str, fname: str, res: dict) -> dict:
    scores = res.get("scores") or {}
    rej = res.get("evidence_rejections") or {}
    row = {
        "sid": sid,
        "name": name,
        "file": fname,
        "paper_title": res.get("paper_title", ""),
        "bound_paper_id": res.get("bound_paper_id", ""),
        "bound_status": res.get("bound_status", ""),
        "report_chars": res.get("report_chars", ""),
        "paper_chars": res.get("paper_chars", ""),
        "understanding_accuracy": _round(scores.get("understanding_accuracy")),
        "analysis_depth": _round(scores.get("analysis_depth")),
        "innovative_insights": _round(scores.get("innovative_insights")),
        "evidence_support": _round(scores.get("evidence_support")),
        "fidelity": _round(scores.get("fidelity")),
        "coverage": _round(scores.get("coverage")),
        "average": _round(res.get("average")),
        "verdict": res.get("verdict", ""),
        "copy_ratio": _round(res.get("copy_ratio")),
        "stray_claims": res.get("stray_claims_count", len(res.get("stray_claims") or [])),
        "truncated": bool(res.get("truncated")),
        "fidelity_status": res.get("fidelity_status", ""),
        "coverage_status": res.get("coverage_status", ""),
        "effective_evidence": res.get("effective_evidence_count", ""),
        "hardcoded_overrides": ";".join(res.get("hardcoded_overrides") or []),
        "llm_calls": res.get("llm_calls", ""),
        "llm_empty": res.get("llm_empty", ""),
        "elapsed_s": _round(res.get("elapsed_s"), 1),
        "ev_from_paper": rej.get("from_paper", ""),
        "ev_not_found": rej.get("not_found", ""),
        "error": "",
    }
    # LLM 故障 → 作废 LLM 派生列，只保留 fidelity/coverage 等确定性指标供排查。
    if res.get("llm_failed"):
        for k in _LLM_DERIVED_FIELDS:
            row[k] = ""
        row["error"] = "llm_failed"
    return row


def analyze_docx(db, path: Path, only_sids: set[str] | None, bindings: dict[str, str]) -> dict | None:
    """解析单篇 docx 并跑完整分析。返回 row dict，出错返回含 error 的 row。"""
    fname = path.name
    m = _STUDENT_RE.search(fname)
    if not m or fname in _SKIP_NAMES:
        return None
    sid = m.group(1)
    if only_sids and sid not in only_sids:
        return None
    from mock_api.reflection_pipeline import analyze_reflection_file

    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False, dir=str(ROOT / "mock_api" / "uploads")) as tf:
        tf.write(path.read_bytes())
        tmp = tf.name
    try:
        source_paper = bindings.get(sid)
        t0 = time.time()
        res = analyze_reflection_file(tmp, db, source_paper)
        # 单篇耗时：识别「LLM 超时级联」的关键信号——
        # 正常 50~90s，一旦某篇冲到 watchdog 上限且后续连续贴顶，就是级联而非报告问题。
        res["elapsed_s"] = time.time() - t0
        row = row_from_result(sid, res.get("student_name") or "", fname, res)
        if source_paper and not row["bound_paper_id"]:
            row["bound_paper_id"] = source_paper
            row["bound_status"] = "db_binding"
        return row
    except Exception as e:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        return {
            "sid": sid, "name": "", "file": fname, "error": str(e)[:200],
            **{k: "" for k in FIELDS if k not in ("sid", "name", "file", "error")},
        }
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def analyze_db_fallback(db, sid: str, fname: str) -> dict | None:
    """对无 docx（仅 .doc/PDF）但数据库已有解析文本的学号做兜底分析。"""
    from mock_api import crud
    from mock_api.reflection_docx_parser import extract_title_from_text

    try:
        from mock_api.models import Paper as PaperORM

        p = db.query(PaperORM).filter(PaperORM.id == f"reflection_{sid}").first()
        if p is None or not (p.full_text or "").strip():
            return None
        full = p.full_text
        title = extract_title_from_text(full) or (p.title or "")
        # 手动组合分析：绑定 + fidelity/coverage + 4 维
        from mock_api.reflection_binding import match_paper_by_title
        from mock_api.reflection_fidelity import compute_coverage, compute_fidelity

        bound = match_paper_by_title(title, db)
        paper_full = ""
        if bound:
            pp = db.query(PaperORM).filter(PaperORM.id == bound).first()
            paper_full = (pp.full_text or "") if pp else ""
        fid = compute_fidelity({"q": full}, paper_full)
        cov = compute_coverage({"q": full}, paper_full)
        scores = {
            "understanding_accuracy": 0.0, "analysis_depth": 0.0,
            "innovative_insights": 0.0, "evidence_support": 0.0,
            "fidelity": fid.fidelity or 0.0, "coverage": cov.coverage or 0.0,
        }
        from mock_api.depth_eval_reflection import ReflectionReviewer

        rid = f"report_{sid}"
        four = ReflectionReviewer().review(rid, title, full, paper_text=paper_full)
        if four and four.scores:
            for k in ("understanding_accuracy", "analysis_depth", "innovative_insights", "evidence_support"):
                scores[k] = four.scores.get(k, 0.0)
        from mock_api.reflection_pipeline import W

        w_sum = sum(W.get(k, 0.0) for k in scores) or 1.0
        avg = sum(scores.get(k, 0.0) * W.get(k, 0.0) for k in scores) / w_sum
        res = {
            "paper_title": title, "bound_paper_id": bound,
            "scores": scores, "average": round(avg, 4),
            "verdict": (four.verdict if four else "needs_evidence"),
            "report_chars": len(full), "paper_chars": len(paper_full),
            "fidelity_status": fid.status, "coverage_status": cov.status,
            "copy_ratio": getattr(fid, "copy_ratio", None),
            "stray_claims": len(fid.stray_claims),
            "effective_evidence_count": getattr(four, "effective_evidence_count", 0),
            "truncated": getattr(four, "truncated", False),
            "hardcoded_overrides": list(getattr(four, "hardcoded_overrides", None) or []),
            "llm_calls": int(getattr(four, "llm_calls", 0) or 0),
            "llm_empty": int(getattr(four, "llm_empty", 0) or 0),
            "llm_failed": bool(
                getattr(four, "parse_failed", False) or getattr(four, "llm_empty", 0)
            ),
        }
        return row_from_result(sid, "", fname, res)
    except Exception as e:  # noqa: BLE001
        return {"sid": sid, "name": "", "file": fname, "error": str(e)[:200],
                **{k: "" for k in FIELDS if k not in ("sid", "name", "file", "error")}}


def run(source_dir: Path, out: Path, only_sids: set[str] | None) -> list[dict]:
    from mock_api.database import SessionLocal

    db = SessionLocal()
    bindings = load_bindings()
    print(f"复用 DB 绑定映射: {len(bindings)} 篇", flush=True)
    rows: list[dict] = []
    files = sorted(source_dir.glob("*.docx"))
    print(f"docx 文件数: {len(files)}", flush=True)
    t0 = time.time()
    for i, f in enumerate(files, 1):
        t1 = time.time()
        row = analyze_docx(db, f, only_sids, bindings)
        if row is None:
            continue
        dt = time.time() - t1
        if row.get("error"):
            print(
                f"  [{i}] {f.name}: ERROR {row['error'][:80]} "
                f"({round(dt, 1)}s, 累计 {round(time.time()-t0, 1)}s)",
                flush=True,
            )
        else:
            # ev/ovr 是判断分数可信度的两个关键信号：
            # ev<2 会被 R1 硬压到 0.3，ovr 列出实际触发的硬规则。
            ovr = row.get("hardcoded_overrides") or "-"
            print(
                f"  [{i}] {row['sid']} {row['name'] or ''} "
                f"fid={row['fidelity']} cov={row['coverage']} "
                f"avg={row['average']} verdict={row['verdict']} "
                f"ev={row['effective_evidence']} ovr={ovr} "
                f"({round(dt, 1)}s, 累计 {round(time.time()-t0, 1)}s)",
                flush=True,
            )
        rows.append(row)
    # PDF-only 兜底（无 docx 且数据库已有解析文本的）：目前仅 999900000031（黄苑婷，PDF）
    # 注意：不要与 docx 主循环重复（如 148 有 .docx 就不要再走这里）
    db_full_rows = []
    for sid in ("999900000031",):
        if only_sids and sid not in only_sids:
            continue
        r = analyze_db_fallback(db, sid, f"(db-fallback {sid})")
        if r:
            db_full_rows.append(r)
            print(f"  [db-fallback] {sid}: avg={r['average']} verdict={r['verdict']}", flush=True)
    rows.extend(db_full_rows)
    db.close()

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    ok = sum(1 for r in rows if not r.get("error"))
    llm_failed = sum(1 for r in rows if r.get("error") == "llm_failed")
    capped = sum(1 for r in rows if "R1" in (r.get("hardcoded_overrides") or ""))
    print(f"\n完成: {len(rows)} 行（成功 {ok}，失败 {len(rows) - ok}）→ {out}", flush=True)
    if llm_failed:
        print(f"⚠ LLM 故障 {llm_failed} 篇：这些行的 4 维/总分/verdict 已留空，需重跑", flush=True)
    if capped:
        print(f"⚠ R1 证据不足封顶 {capped} 篇：确认是报告真没证据，还是取证被原论文文本污染", flush=True)
    return rows


def compare(a: Path, b: Path) -> None:
    def load(p: Path):
        with open(p, encoding="utf-8-sig") as fh:
            return {r["sid"]: r for r in csv.DictReader(fh)}

    A, B = load(a), load(b)
    print(f"\n=== 对比 {a.name} (修复前) vs {b.name} (修复后) ===")
    print(f"{'学号':<14}{'修复前avg':>10}{'修复后avg':>10}{'Δavg':>8}{'fid→':>8}{'cov→':>8}{'copy':>7}  {'verdict 前→后'}")
    for sid in sorted(set(A) & set(B)):
        ra, rb = A[sid], B[sid]
        try:
            da = float(rb["average"]) - float(ra["average"])
        except (TypeError, ValueError):
            da = float("nan")
        print(
            f"{sid:<14}{ra['average']:>10}{rb['average']:>10}{da:>+8.3f}"
            f"{ra['fidelity']+'→'+rb['fidelity']:>8}"
            f"{ra['coverage']+'→'+rb['coverage']:>8}"
            f"{rb['copy_ratio']:>7}  {ra['verdict']} → {rb['verdict']}"
        )
    # 统计
    n = len(set(A) & set(B))
    down = sum(1 for s in set(A) & set(B)
               if (A[s].get("average") or "") and (B[s].get("average") or "")
               and float(B[s]["average"]) < float(A[s]["average"]))
    up = sum(1 for s in set(A) & set(B)
             if (A[s].get("average") or "") and (B[s].get("average") or "")
             and float(B[s]["average"]) > float(A[s]["average"]))
    print(f"\n共同 {n} 篇：分数下降 {down}，上升 {up}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", type=Path, default=Path.home() / "Desktop" / "Word文档")
    ap.add_argument("--out", type=Path, default=ROOT / "deliverables" / "reflection_scores.csv")
    ap.add_argument("--compare", nargs=2, type=Path, metavar=("A.csv", "B.csv"))
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--only", type=str, default="")
    args = ap.parse_args()

    if args.compare:
        compare(*args.compare)
        return 0
    only = set(args.only.split(",")) if args.only else None
    if args.no_llm:
        os.environ["PAPERFORGE_BENCH_NO_LLM"] = "1"
    run(args.source_dir.expanduser(), args.out, only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
