"""批量补录旧报告引用的原论文（手动/调试用 CLI）。

⚠️ 日常使用场景不需要本脚本：在 PaperForge 系统「提交感悟报告」弹窗里上传
docx，后端会自动调用 mock_api/report_paper_resolver 识别并下载原论文。
本脚本仅用于「历史报告批量补录」或调试。

两种用法：
    A. 指定文件（提交几篇处理几篇）：
       python -m scripts.auto_ingest_reports --files "D:/报告/学生01.docx" "D:/报告/王芳.docx"
    B. 扫描文件夹只处理新增（按 文件名+大小 去重）：
       python -m scripts.auto_ingest_reports --reports-dir "C:/Users/<user>/Desktop/Word文档"

核心逻辑全部委托 mock_api.report_paper_resolver.resolve_and_ingest。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_REPORTS_DIR = r"C:/Users/<user>/Desktop/Word文档"
PROCESSED_LOG = ROOT / "deliverables" / ".processed_reports.json"


def _file_key(fp: Path) -> str:
    return f"{fp.name}:{fp.stat().st_size}"


def _load_processed() -> dict:
    if PROCESSED_LOG.exists():
        try:
            return json.loads(PROCESSED_LOG.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_processed(d: dict) -> None:
    PROCESSED_LOG.parent.mkdir(parents=True, exist_ok=True)
    PROCESSED_LOG.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def run(files=None, reports_dir=None, dry_run=False) -> dict:
    from mock_api.database import SessionLocal, init_db
    from mock_api.reflection_docx_parser import parse_docx_from_bytes
    from mock_api.report_paper_resolver import resolve_and_ingest

    init_db()

    if files:
        targets = [Path(f) for f in files if Path(f).exists()]
        missing = [f for f in files if not Path(f).exists()]
        if missing:
            print(f"[warn] 忽略不存在的文件: {missing}")
    else:
        rp = Path(reports_dir)
        if not rp.exists():
            return {"error": f"reports dir not found: {reports_dir}"}
        all_docs = sorted(rp.glob("*-*.docx"))
        processed = _load_processed()
        targets = [fp for fp in all_docs if _file_key(fp) not in processed]
        print(f"扫描到 {len(all_docs)} 份，其中新增待处理 {len(targets)} 份（其余已处理跳过）")

    records: list[dict] = []
    processed_log = _load_processed()
    db = SessionLocal()
    try:
        for fp in targets:
            try:
                doc = parse_docx_from_bytes(fp.read_bytes(), filename=str(fp))
            except Exception as e:
                records.append({"file": fp.name, "status": "parse_error", "detail": str(e)})
                continue

            title = doc.paper_title
            if not title:
                records.append({"file": fp.name, "student": doc.student_id,
                                "status": "no_title", "detail": "报告头部无论文题目"})
                processed_log[_file_key(fp)] = time.strftime("%Y-%m-%d %H:%M:%S")
                continue

            if dry_run:
                records.append({"file": fp.name, "student": doc.student_id,
                                "title": title, "status": "would_resolve"})
                continue

            res = resolve_and_ingest(title, doc.paper_author, db=db)
            rec = {"file": fp.name, "student": doc.student_id, "title": title}
            rec.update(res)
            records.append(rec)
            # 扫描模式下去重记录（仅成功/明确结论才记，限流留待下次重试）
            if not files and res.get("status") != "rate_limited":
                processed_log[_file_key(fp)] = time.strftime("%Y-%m-%d %H:%M:%S")
    finally:
        db.close()
        if not files:
            _save_processed(processed_log)

    summary = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "files" if files else "scan",
        "dry_run": dry_run,
        "total_targets": len(targets),
        "records": records,
        "counts": _count_by_status(records),
    }
    out = ROOT / "deliverables" / f"auto_ingest_{time.strftime('%Y%m%d')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"摘要已写入 {out}")
    return summary


def _count_by_status(records: list[dict]) -> dict:
    c: dict[str, int] = {}
    for r in records:
        c[r.get("status", "?")] = c.get(r.get("status", "?"), 0) + 1
    return c


def main() -> None:
    ap = argparse.ArgumentParser(description="历史报告原论文批量补录（调试/补数据用）")
    ap.add_argument("--files", nargs="*", help="指定报告文件（提交几篇处理几篇）")
    ap.add_argument("--reports-dir", default=DEFAULT_REPORTS_DIR)
    ap.add_argument("--dry-run", action="store_true", help="只报告会做什么，不下载不入库")
    args = ap.parse_args()
    s = run(files=args.files, reports_dir=args.reports_dir, dry_run=args.dry_run)
    print("统计:", json.dumps(s.get("counts", {}), ensure_ascii=False))


if __name__ == "__main__":
    main()
