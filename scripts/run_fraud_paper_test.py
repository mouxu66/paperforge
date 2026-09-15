#!/usr/bin/env python3
"""端到端造假论文检测：把本地 PDF 入库 → 抽图 → 跑 AuditService → 打印 Findings。

用法（仓库根目录）:
    .venv/Scripts/python.exe scripts/run_fraud_paper_test.py <pdf_path> <paper_id> [title]

- 把 PDF 拷贝到 uploads/<paper_id>.pdf
- 建 Paper 记录（full_text 从 PDF 抽）
- extract_figures_for_paper 抽图并持久化 PaperFigure
- AuditService.run_paper_audit 跑全量审计
- 打印所有 Finding（type/severity/claim/evidence）
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows 控制台 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from mock_api.database import SessionLocal, init_db  # noqa: E402
from mock_api.models import Paper, PaperFigure  # noqa: E402
from mock_api.pdf_parser import extract_figures_for_paper, _get_uploads_dir  # noqa: E402
from mock_api.experiment_audit.service import AuditService  # noqa: E402
from mock_api.crud.figures import delete_figures_by_paper, upsert_figure  # noqa: E402


def extract_full_text(pdf_bytes: bytes) -> str:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n".join(doc[i].get_text() for i in range(doc.page_count))
    finally:
        doc.close()


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: run_fraud_paper_test.py <pdf_path> <paper_id> [title]")
        return 1
    pdf_path = Path(sys.argv[1])
    paper_id = sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else paper_id

    if not pdf_path.exists():
        print(f"PDF 不存在: {pdf_path}")
        return 1

    init_db()
    uploads = _get_uploads_dir()
    dest = uploads / f"{paper_id}.pdf"
    shutil.copyfile(pdf_path, dest)
    pdf_bytes = dest.read_bytes()
    full_text = extract_full_text(pdf_bytes)
    print(f"[1] PDF 已拷贝: {dest}（{len(pdf_bytes)} bytes，全文 {len(full_text)} 字符）")

    db = SessionLocal()
    try:
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if paper is None:
            paper = Paper(
                id=paper_id,
                title=title,
                authors=[],
                abstract="",
                category="fraud-test",
                tags=["retracted", "fraud-test"],
                year=2014,
                journal="PLoS ONE",
                pdf_url="",
                source="manual",
                full_text=full_text,
                doi="10.1371/journal.pone.0113398",
            )
            db.add(paper)
        else:
            paper.full_text = full_text
        db.commit()
        print(f"[2] Paper 记录就绪: {paper_id}")

        # 抽图并持久化
        delete_figures_by_paper(db, paper_id)
        figs = extract_figures_for_paper(pdf_bytes, paper_id, skip_ocr=True)
        print(f"[3] 抽取 figure: {len(figs)} 张")
        for f in figs:
            upsert_figure(
                db,
                paper_id=paper_id,
                page=f.get("page", 0),
                figure_index=f.get("figure_index", 0),
                figure_path=f.get("figure_path", ""),
                ocr_text=f.get("ocr_text", ""),
                caption_text=f.get("caption_text"),
                source=f.get("source", "bitmap"),
                figure_number=f.get("figure_number"),
            )
        db.commit()
        total = db.query(PaperFigure).filter(PaperFigure.paper_id == paper_id).count()
        print(f"[4] 已入库 figure: {total} 张")

        # 跑审计
        print("[5] 开始 AuditService.run_paper_audit ...")
        audit = AuditService().run_paper_audit(db, paper_id)
        print(f"[6] 审计完成 status={audit.status} findings={len(audit.findings or [])}")

        print("\n" + "=" * 70)
        print("FINDINGS")
        print("=" * 70)
        if not audit.findings:
            print("（无 Finding）")
        for f in audit.findings or []:
            print(f"\n[{f.get('finding_id')}] {f.get('type')} (severity={f.get('severity')})")
            print(f"  title: {f.get('title')}")
            if f.get("claim"):
                print(f"  claim: {f.get('claim')[:300]}")
            if f.get("computed"):
                print(f"  computed: {f.get('computed')[:300]}")
            if f.get("method"):
                print(f"  method: {f.get('method')}")

        print("\n" + "=" * 70)
        print("CHECKS_RUN")
        print("=" * 70)
        for c in audit.checks_run or []:
            extra = f" ({c.get('reason')})" if c.get("reason") else (
                f" ({c.get('findings')} findings)" if "findings" in c else ""
            )
            print(f"  {c.get('check')}: {c.get('status')}{extra}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
