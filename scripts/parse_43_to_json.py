"""把 43 篇反射报告解析成结构化 JSON 供 Buffy 精读。

输出 deliverables/bufy_43_reports.json
输出 deliverables/bufy_43_summary.csv（每篇: 学号/姓名/标题/sid匹配/段字数）
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mock_api.reflection_docx_parser import parse_docx_from_bytes

UPLOADS = ROOT / "mock_api" / "uploads"
OUT_JSON = ROOT / "deliverables" / "bufy_43_reports.json"
OUT_SUMMARY = ROOT / "deliverables" / "bufy_43_summary.csv"


def main():
    files = sorted(UPLOADS.glob("reflection_*.docx"))
    print(f"found {len(files)} reflection_*.docx files")
    bufy_data = []
    summary_rows = []

    for fp in files:
        try:
            data = fp.read_bytes()
            d = parse_docx_from_bytes(data, filename=str(fp))
            entry = {
                "file": fp.name,
                "student_id": d.student_id,
                "name": d.name,
                "paper_title": d.paper_title,
                "paper_author": d.paper_author,
                "paper_source": d.paper_source,
                "section_keys": list(d.sections.keys()),
                "section_chars": {k: len(v) for k, v in d.sections.items()},
                "section_text": {k: v for k, v in d.sections.items()},
                "raw_text": d.raw_text,
                "raw_chars": len(d.raw_text),
            }
            bufy_data.append(entry)
            summary_rows.append({
                "file": fp.name,
                "sid": d.student_id or "",
                "name": d.name or "",
                "title": (d.paper_title or "")[:80],
                "q_chars": entry["section_chars"].get("q", 0),
                "tech_chars": entry["section_chars"].get("tech", 0),
                "exp_chars": entry["section_chars"].get("exp", 0),
                "ref_chars": entry["section_chars"].get("reflection", 0),
                "total_chars": entry["raw_chars"],
                "section_count": len(entry["section_keys"]),
            })
            print(
                f"  {fp.name[:60]:<60} sid={d.student_id or '-':<14} "
                f"sections={entry['section_keys']} chars={entry['raw_chars']}"
            )
        except Exception as e:
            print(f"  ERR {fp.name}: {e}")
            bufy_data.append({"file": fp.name, "error": str(e)})

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(bufy_data, f, ensure_ascii=False, indent=2)
    import csv

    with open(OUT_SUMMARY, "w", encoding="utf-8-sig", newline="") as f:
        if summary_rows:
            wr = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
            wr.writeheader()
            wr.writerows(summary_rows)
    print(f"\nJSON: {OUT_JSON}")
    print(f"Summary: {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
