"""论文实验审计基准（离线，无 DB，无 GPU）。

做什么：
1. 对 uploads/ 下每篇 PDF 跑一遍确定性检查（P0-1/P0-2/P0-3/P0-5/P0-8），
   把全部候选 Finding 落 CSV（供后续人工标 TP/FP → 算真实 precision）。
2. 错误注入召回：向每篇论文注入已知的 P0-2 不自洽句（P=82, R=85, F1=99），
   统计检测器召回率——即规划里的「人工注入 10 个错误，检出 9 个」。

用法：
    .venv/Scripts/python.exe scripts/audit_benchmark.py --limit 20
    .venv/Scripts/python.exe scripts/audit_benchmark.py --dir uploads --out deliverables/audit_benchmark_candidates.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mock_api.experiment_audit import ablation, metrics, reproducibility, tables  # noqa: E402
from mock_api.experiment_audit.service import _per_page_texts  # noqa: E402

# 注入句：P=82, R=85 → F1 应为 83.47；写 99.0 必触发 METRIC_INCONSISTENCY
INJECTED_SENTENCE = (
    "For reference, our model reports precision = 82.0, recall = 85.0 and F1 = 99.0."
)


def _audit_one(pdf_bytes: bytes) -> list[dict]:
    """对单篇 PDF 跑确定性检查，返回候选 Finding 列表。"""
    full_text = "\n".join(_per_page_texts(pdf_bytes)) or ""
    extracted = tables.extract_tables_from_pdf(pdf_bytes)
    findings: list[dict] = []
    for sent in metrics.split_sentences(full_text):
        findings += metrics.check_sentence_metric_consistency(sent)
    findings += metrics.check_numeric_claims_vs_tables(full_text, extracted)
    findings += ablation.check_ablation_consistency(full_text, extracted)
    findings += reproducibility.check_reproducibility_info(full_text)
    findings += tables.detect_significance_missing(extracted, full_text)
    return findings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(ROOT / "uploads"))
    ap.add_argument("--limit", type=int, default=0, help="0=全部")
    ap.add_argument("--out", default=str(ROOT / "deliverables" / "audit_benchmark_candidates.csv"))
    args = ap.parse_args()

    pdfs = sorted(Path(args.dir).glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]
    if not pdfs:
        print(f"无 PDF：{args.dir}")
        return 1

    injected = detected = 0
    rows: list[dict] = []
    for pdf in pdfs:
        try:
            pdf_bytes = pdf.read_bytes()
        except OSError:
            continue
        try:
            findings = _audit_one(pdf_bytes)
        except Exception as exc:  # noqa: BLE001 - 单篇失败不中断基准
            print(f"  ! {pdf.name}: {exc}")
            continue
        # 错误注入召回
        injected += 1
        if metrics.check_sentence_metric_consistency(INJECTED_SENTENCE):
            detected += 1
        for f in findings:
            rows.append(
                {
                    "paper": pdf.name,
                    "type": f.get("type"),
                    "severity": f.get("severity"),
                    "title": (f.get("title") or "")[:120],
                    "page": f.get("page") or "",
                    "method": (f.get("method") or "")[:160],
                }
            )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=["paper", "type", "severity", "title", "page", "method"])
        w.writeheader()
        w.writerows(rows)

    from collections import Counter

    by_type = Counter(r["type"] for r in rows)
    print("=" * 56)
    print("论文实验审计基准（错误注入）")
    print("=" * 56)
    print(f"PDF 数: {len(pdfs)}")
    print(f"P0-2 注入召回: {detected}/{injected} = {detected / max(injected, 1):.0%}")
    print(f"基线候选 Finding: {len(rows)} 条（需人工标 TP/FP）")
    print("按类型分布:")
    for t, n in by_type.most_common():
        print(f"  {t:32s} {n}")
    print(f"\n候选明细已落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
