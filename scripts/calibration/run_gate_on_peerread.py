#!/usr/bin/env python3
"""门禁正式跑：从 peerread_sample.json 挑 10 篇 arxiv 来源论文，用项目 API 拉原始 PDF，跑 figure_recall_gate。

- 只选 stem 形如 arxiv ID（全数字含点，如 1702.02171）的条目，跳过 ICLR 等非 arxiv 源。
- PDF 拉取复用项目 backfill_full_text.download_pdf（自带 validate_pdf_url 白名单+私网校验，
  且走普通 DNS 路径，避开 pdf_proxy_service 的 DNS 钉死坑）。
- 原始 PDF 下到 scripts/gate_pdfs/<stem>.pdf，再调 figure_recall_gate 的逻辑出 JSON 报告。
- 不自动判过/不过；输出供人工核验 recall>=90%。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backfill_full_text import download_pdf  # noqa: E402  # 项目批量拉 PDF 的安全路径
import figure_recall_gate as gate  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "deliverables" / "peerread_sample.json"
OUT_DIR = REPO / "scripts" / "gate_pdfs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT = REPO / "deliverables" / "figure_recall_gate_peerread10.json"


def _is_arxiv_stem(stem: str) -> bool:
    # arxiv ID 形如 1702.02171（恰好 1 个点号，去点后全数字）；
    # ICLR 等是纯数字无点（如 371），跳过。
    if stem.count(".") != 1:
        return False
    return stem.replace(".", "").isdigit()


def main() -> int:
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    arxiv_entries = [e for e in sample if _is_arxiv_stem(e.get("stem", ""))]
    # 多取候选（前 12），容忍个别下载失败，取前 10 篇成功下载的
    candidates = arxiv_entries[:12]
    if len(candidates) < 10:
        print(f"arxiv 来源不足 10 篇（仅 {len(candidates)}），停止", file=sys.stderr)
        return 1

    pdf_paths: list[Path] = []
    for e in candidates:
        if len(pdf_paths) >= 10:
            break
        stem = e["stem"]
        dest = OUT_DIR / f"{stem}.pdf"
        if dest.exists() and dest.read_bytes().startswith(b"%PDF"):
            pdf_paths.append(dest)
            continue
        # 用项目 API 拉原始 PDF（带 SSRF 校验）
        data = download_pdf(f"https://arxiv.org/pdf/{stem}.pdf")
        if data and data.startswith(b"%PDF"):
            dest.write_bytes(data)
            pdf_paths.append(dest)
        else:
            print(f"[WARN] {stem} 拉取失败或非法 PDF，跳过", file=sys.stderr)

    if len(pdf_paths) < 10:
        print(f"仅成功取得 {len(pdf_paths)}/10 篇 PDF，仍继续跑已取得的", file=sys.stderr)

    # 复用 figure_recall_gate 的逐篇评估
    results = [gate.evaluate_pdf(p, f"prgate_{i:03d}") for i, p in enumerate(pdf_paths, 1)]
    for r in results:
        if r["extracted_total"] < 1:
            r["low_figure_warn"] = True

    report = {
        "sample_size": len(results),
        "source": "PeerRead sample (arxiv stems, seed=42) — 用项目 API 拉原始 PDF",
        "discipline": "M0 接入前需 10 篇、图表召回率 >=90%；本脚本只出代理指标+产物，最终 pass/fail 须人工核验",
        "per_paper": results,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\n报告已写: {REPORT}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
