#!/usr/bin/env python3
"""端到端造假论文演示：生成一篇「带造假指纹」的合成 PDF → 走上传+审计流程。

用法（仓库根目录）:
    .venv/Scripts/python.exe scripts/generate_and_audit_fraud_paper.py [paper_id]

流程（模拟前端用户上传）：
1. 生成合成图像：一张带克隆条带的 western blot + 一张柱状图（复用两处）。
2. 用 PyMuPDF 直接拼一篇 PDF：正文 + 两张带网格的表格 + 三张图 + 参考文献。
3. 拷贝到 uploads/<paper_id>.pdf、建 Paper 记录、extract_figures_for_paper 抽图入库。
4. AuditService.run_paper_audit 全量审计，打印 Findings 与「哪些指纹被抓到」。

刻意植入的造假指纹（全部离线可检测）：
- P0-1  正文 accuracy=92.1% vs Table 1 accuracy=84.3（数字不一致）
- P0-2  Precision=90.0 / Recall=80.0 / F1=91.5（F1 应为 84.7，数学不自洽）
- P0-3  ablation 表 w/o attention 行不降反升 + 「each component contributes」
- P0-5  全文缺 seed/std/hardware/lr/batch/epochs（可复现信息缺失）
- P0-8  结果表无 ± 且全文无不确定度描述（p 值/t-test 不豁免——p 值只说明
        差异是否显著，不提供均值离散程度，故 P0-8 与 P1-3 同时命中）
- P0-9  Figure 1 与 Figure 2 用同一张柱状图（图内复用）
- P0-14 Figure 3 条带克隆（同图两条带像素级相同）
- P1-0  参考文献含占位符 DOI (10.5555/1234567.8901234) 与假 arXiv 号 (2513.12345)
- P1-2  mean score of 4.56 with n = 30（GRIM 违例，经典示例）
- P1-3  6 个精确 p 值挤在 0.04~0.05 且 0.045 重复 3 次（p-hacking 指纹）
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

import numpy as np  # noqa: E402

PAPER_ID = "fraud_demo_001"
TITLE = "DeepGuard: A Robust Attention Network for Medical Image Segmentation"
OUT_DIR = Path(__file__).resolve().parent / "_fraud_demo_assets"


# ─────────────────────────────────────────────────────────────────────────────
# 1. 合成图像
# ─────────────────────────────────────────────────────────────────────────────
def make_blot(path: Path) -> None:
    """白底 + 4 条横向暗带，其中第 1、3 条像素级相同（克隆）。"""
    import cv2

    h, w = 320, 400
    rng = np.random.RandomState(0)
    img = np.full((h, w), 255, dtype=np.int16)
    img = img + rng.randint(-8, 9, (h, w), dtype=np.int16)
    img = np.clip(img, 0, 255).astype(np.uint8)

    def band(seed: int, x0: int, y0: int, bw: int = 140, bh: int = 16) -> None:
        r = np.random.RandomState(seed)
        col = r.randint(20, 90, bw)
        for i in range(bh):
            row = np.clip(col + r.randint(-6, 7, bw), 0, 120)
            img[y0 + i, x0 : x0 + bw] = row

    band(1, 40, 60)   # 独特条带 A
    band(2, 40, 130)  # 独特条带 B
    band(1, 40, 200)  # 克隆：与条带 A 完全相同
    band(3, 40, 270)  # 独特条带 C
    cv2.imwrite(str(path), img)


def make_bar_chart(path: Path) -> None:
    """带网格/标签/坐标轴的柱状图（足够的 SIFT 特征，供复用检测）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(4.2, 3.0), dpi=120)
    methods = ["Baseline", "ResNet-50", "Ours"]
    vals = [80.0, 82.1, 84.3]
    bars = ax.bar(
        methods,
        vals,
        color=["#4C72B0", "#55A868", "#C44E52"],
        edgecolor="black",
        linewidth=0.8,
    )
    ax.set_ylim(0, 100)
    ax.set_ylabel("Accuracy (%)")
    ax.set_title("Accuracy comparison on the test set")
    ax.grid(True, axis="y", linestyle="--", alpha=0.5)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v + 1.5, f"{v:.1f}", ha="center", fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# ─────────────────────────────────────────────────────────────────────────────
# 2. 拼 PDF（PyMuPDF 直接绘制：正文文本 + 带网格表格 + 位图）
# ─────────────────────────────────────────────────────────────────────────────
def _add_text(page, text, x, y, w, h, size=11, bold=False):
    font = "hebo" if bold else "helv"
    return page.insert_textbox(
        __import__("fitz").Rect(x, y, x + w, y + h),
        text,
        fontname=font,
        fontsize=size,
        color=(0, 0, 0),
    )


def _draw_table(page, rect, header, rows, fontsize=10):
    import fitz

    x0, y0, x1, y1 = rect
    n_rows = len(rows) + 1
    n_cols = len(header)
    row_h = (y1 - y0) / n_rows
    col_w = (x1 - x0) / n_cols
    # 重要：find_tables 的 lines 策略只认「填充矩形」形式的表格线，不认
    # draw_line 的描边路径（type='s'）。且单元格文本必须用 insert_text（而非
    # insert_textbox）写入——lines 策略靠「网格线 + 格内文本」确认表格，
    # insert_textbox 的文本框对象不会被关联到网格，导致表格被漏检。
    t = 1.2
    for i in range(n_rows + 1):
        y = y0 + i * row_h
        page.draw_rect(fitz.Rect(x0, y, x1, y + t), color=(0, 0, 0), fill=(0, 0, 0), width=0)
    for j in range(n_cols + 1):
        x = x0 + j * col_w
        page.draw_rect(fitz.Rect(x, y0, x + t, y1), color=(0, 0, 0), fill=(0, 0, 0), width=0)
    for j, cell in enumerate(header):
        page.insert_text(
            (x0 + j * col_w + 3, y0 + row_h - 4), cell, fontname="hebo", fontsize=fontsize
        )
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            page.insert_text(
                (x0 + j * col_w + 3, y0 + (i + 2) * row_h - 4),
                cell,
                fontname="helv",
                fontsize=fontsize,
            )


def build_pdf(out_path: Path, blot: Path, bar: Path) -> None:
    import fitz

    doc = fitz.open()
    W, H = 595.0, 842.0
    MX = 50.0  # 左边距

    # ── 页 1：标题 / 摘要 / 引言 ──────────────────────────────────────────
    p1 = doc.new_page(width=W, height=H)
    _add_text(p1, TITLE, MX, 50, W - 2 * MX, 60, size=16, bold=True)
    _add_text(p1, "John Q. Fabricator, Alice Doe, and Bob S. Inventor", MX, 92, W - 2 * MX, 20, size=10)
    _add_text(p1, "Fabricated Institute of Computational Medicine (2024)", MX, 110, W - 2 * MX, 20, size=10)

    _add_text(p1, "Abstract", MX, 140, W - 2 * MX, 20, size=12, bold=True)
    _add_text(
        p1,
        "Accurate medical image segmentation is critical for clinical decision making. "
        "We propose DeepGuard, a robust attention network that achieves state-of-the-art "
        "results across three benchmark datasets. Extensive experiments demonstrate that "
        "DeepGuard outperforms strong baselines with a clear margin.",
        MX, 160, W - 2 * MX, 60, size=11,
    )

    _add_text(p1, "1. Introduction", MX, 230, W - 2 * MX, 20, size=12, bold=True)
    _add_text(
        p1,
        "Segmentation models are widely used in radiology. In this work we introduce a novel "
        "attention module and validate it on a large cohort of clinical images. Our key "
        "contribution is a gating mechanism that reweights feature maps.",
        MX, 250, W - 2 * MX, 50, size=11,
    )
    _add_text(
        p1,
        "We annotated every slice manually. Across the cohort, the mean score of 4.56 was "
        "recorded from n = 30 patients, reflecting moderate pathology.",
        MX, 305, W - 2 * MX, 50, size=11,
    )

    # ── 页 2：方法 / 结果 / p 值 ─────────────────────────────────────────
    p2 = doc.new_page(width=W, height=H)
    _add_text(p2, "2. Method", MX, 50, W - 2 * MX, 20, size=12, bold=True)
    _add_text(
        p2,
        "We train DeepGuard with an end-to-end pipeline. Our proposed model achieves a "
        "precision of 90.0, a recall of 80.0, and an F1 of 91.5 on the test set, which "
        "compares favorably with prior work.",
        MX, 70, W - 2 * MX, 50, size=11,
    )

    _add_text(p2, "3. Results", MX, 150, W - 2 * MX, 20, size=12, bold=True)
    _add_text(
        p2,
        "As shown in Table 1, our method achieves an accuracy of 92.1% on the test set, "
        "surpassing all compared baselines.",
        MX, 170, W - 2 * MX, 40, size=11,
    )
    _add_text(
        p2,
        "All improvements are significant. The gain over the baseline was confirmed "
        "(p = 0.045) in the lung cohort, again (p = 0.045) in the liver cohort, "
        "and once more (p = 0.045) in the pancreas cohort. Additional comparisons "
        "reached (p = 0.048), (p = 0.041), and (p = 0.047).",
        MX, 215, W - 2 * MX, 70, size=11,
    )

    _add_text(p2, "Table 1. Comparison with baseline methods on the test set.", MX, 300, W - 2 * MX, 20, size=10)
    _draw_table(
        p2,
        (MX, 320, MX + 360, 400),
        ["Method", "Accuracy", "F1"],
        [["Baseline CNN", "80.0", "78.2"], ["ResNet-50", "82.1", "80.5"], ["Ours", "84.3", "82.9"]],
    )

    # ── 页 3：ablation 表 ────────────────────────────────────────────────
    p3 = doc.new_page(width=W, height=H)
    _add_text(p3, "4. Ablation study", MX, 50, W - 2 * MX, 20, size=12, bold=True)
    _add_text(
        p3,
        "We verify that each component contributes to the final performance by removing "
        "modules one at a time. The results are summarized below.",
        MX, 70, W - 2 * MX, 40, size=11,
    )
    _add_text(p3, "Table 2. Ablation study on the test set.", MX, 130, W - 2 * MX, 20, size=10)
    _draw_table(
        p3,
        (MX, 150, MX + 300, 210),
        ["Model", "Accuracy"],
        [["Full model", "84.3"], ["w/o attention", "86.1"]],
    )

    # ── 页 4：图（复用 + 克隆条带）──────────────────────────────────────
    p4 = doc.new_page(width=W, height=H)
    p4.insert_image(fitz.Rect(MX, 50, MX + 260, 235), filename=str(bar))
    _add_text(p4, "Figure 1. Performance comparison across methods.", MX, 240, W - 2 * MX, 20, size=10)

    p4.insert_image(fitz.Rect(MX, 280, MX + 260, 465), filename=str(bar))
    _add_text(p4, "Figure 2. Performance on the held-out validation set.", MX, 470, W - 2 * MX, 20, size=10)

    p4.insert_image(fitz.Rect(MX, 510, MX + 300, 750), filename=str(blot))
    _add_text(p4, "Figure 3. Western blot of marker expression.", MX, 755, W - 2 * MX, 20, size=10)

    # ── 页 5：结论 / 参考文献 ────────────────────────────────────────────
    p5 = doc.new_page(width=W, height=H)
    _add_text(p5, "5. Conclusion", MX, 50, W - 2 * MX, 20, size=12, bold=True)
    _add_text(
        p5,
        "DeepGuard achieves strong results across all datasets. The proposed attention "
        "mechanism is simple, effective, and broadly applicable.",
        MX, 70, W - 2 * MX, 40, size=11,
    )
    _add_text(p5, "References", MX, 150, W - 2 * MX, 20, size=12, bold=True)
    _add_text(p5, "[1] J. Smith et al. A benchmark study. DOI: 10.5555/1234567.8901234", MX, 175, W - 2 * MX, 20, size=10)
    _add_text(p5, "[2] A. Doe et al. Attention mechanisms. arXiv:2513.12345", MX, 200, W - 2 * MX, 20, size=10)

    doc.save(str(out_path))
    doc.close()


# ─────────────────────────────────────────────────────────────────────────────
# 3. 上传 + 审计（模拟前端用户流程）
# ─────────────────────────────────────────────────────────────────────────────
def extract_full_text(pdf_bytes: bytes) -> str:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return "\n".join(doc[i].get_text() for i in range(doc.page_count))
    finally:
        doc.close()


def run_audit(pdf_path: Path, paper_id: str, title: str) -> None:
    from mock_api.crud.figures import delete_figures_by_paper, upsert_figure
    from mock_api.database import SessionLocal, init_db
    from mock_api.experiment_audit.service import AuditService
    from mock_api.models import Paper, PaperFigure
    from mock_api.pdf_parser import _get_uploads_dir, extract_figures_for_paper

    init_db()
    uploads = _get_uploads_dir()
    dest = uploads / f"{paper_id}.pdf"
    shutil.copyfile(pdf_path, dest)
    pdf_bytes = dest.read_bytes()
    full_text = extract_full_text(pdf_bytes)
    print(f"[1] PDF 已上传到 {dest}（{len(pdf_bytes)} bytes，全文 {len(full_text)} 字符）")

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
                tags=["fraud-test"],
                year=2024,
                journal="Fabricated Journal",
                pdf_url="",
                source="manual",
                full_text=full_text,
            )
            db.add(paper)
        else:
            paper.full_text = full_text
        db.commit()

        delete_figures_by_paper(db, paper_id)
        figs = extract_figures_for_paper(
            pdf_bytes, paper_id, skip_ocr=True, vector_render_enabled=False
        )
        print(f"[2] 抽取 figure: {len(figs)} 张")
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
        print(f"[3] 已入库 figure: {total} 张")

        audit = AuditService().run_paper_audit(db, paper_id)
        print(f"[4] 审计完成 status={audit.status} findings={len(audit.findings or [])}")

        print("\n" + "=" * 72)
        print("FINDINGS（按类型分组）")
        print("=" * 72)
        by_type: dict[str, list[dict]] = {}
        for f in audit.findings or []:
            by_type.setdefault(f.get("type"), []).append(f)
        for ftype in sorted(by_type):
            for f in by_type[ftype]:
                print(f"\n[{ftype}] severity={f.get('severity')}")
                print(f"  title: {f.get('title')}")
                if f.get("computed"):
                    print(f"  computed: {f.get('computed')}")

        # 汇总：哪些植入指纹被抓到
        print("\n" + "=" * 72)
        print("植入指纹 → 检测结果")
        print("=" * 72)
        got = {f.get("type") for f in audit.findings or []}
        planted = {
            "NUMERIC_MISMATCH": "P0-1 正文 accuracy 92.1% vs 表 84.3",
            "METRIC_INCONSISTENCY": "P0-2 P=90/R=80/F1=91.5 不自洽",
            "ABLATION_UNSUPPORTED": "P0-3 w/o attention 行不降反升 + each component",
            "MISSING_REPRO_INFO": "P0-5 缺 seed/std/hardware/lr/batch/epochs",
            "UNCERTAINTY_MISSING": "P0-8 结果表无 ± 且无不确定度（p 值不豁免）",
            "FIGURE_REUSE_CANDIDATE": "P0-9 Fig1 与 Fig2 同一张柱状图",
            "IMAGE_TAMPERING_CANDIDATE": "P0-14 Fig3 条带克隆",
            "CITATION_INTEGRITY": "P1-0 占位符 DOI + 假 arXiv 号",
            "GRIM_INCONSISTENCY": "P1-2 mean 4.56 / n=30 违例",
            "PCURVE_ANOMALY": "P1-3 p 值聚集 0.04~0.05",
        }
        for ftype, desc in planted.items():
            mark = "✓ 抓到" if ftype in got else "✗ 未抓"
            print(f"  [{mark}] {desc}")

        print("\n" + "=" * 72)
        print("CHECKS_RUN（跳过/失败项）")
        print("=" * 72)
        for c in audit.checks_run or []:
            if c.get("status") != "ok":
                print(f"  {c.get('check')}: {c.get('status')} — {c.get('reason', '')}")
    finally:
        db.close()


def main() -> int:
    paper_id = sys.argv[1] if len(sys.argv) > 1 else PAPER_ID
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    blot = OUT_DIR / "blot.png"
    bar = OUT_DIR / "bar.png"
    pdf = OUT_DIR / "fraud_paper.pdf"
    make_blot(blot)
    make_bar_chart(bar)
    build_pdf(pdf, blot, bar)
    print(f"PDF 生成: {pdf}")
    run_audit(pdf, paper_id, TITLE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
