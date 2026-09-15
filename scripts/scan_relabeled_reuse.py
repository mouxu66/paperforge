#!/usr/bin/env python3
"""「改标图片复用」证据链扫描（离线，NCC 像素复用 + VLM 目标蛋白标签比对）。

扫描方式与 scan_cross_paper_bands.py 相同：默认 figures 根目录下每个含图片的
子目录 = 一篇论文；若 --figures-dir 指向单篇论文目录，自动上溯到父级根目录做
跨论文扫描。跑 detect_relabeled_image_reuse（像素复用 NCC ≥ 阈值 且 两图目标
蛋白/分子集合无交集 → RELABELED_IMAGE_REUSE），候选落 CSV
（列：figure_a,figure_b,title,claim）。

检测器的 VLM 判定预算有限（--max-pairs 对最高 NCC 的图对）。全库扫描时
NCC=1.0 的「同图重复对」（同一份图的多个副本）会占满预算、把低 NCC 的真
改标对挤出——因此**单篇论文目录模式**下先做两步裁剪：
1. 全库跑条带复用，找出与该论文有像素复用候选的伙伴论文；
2. 只把 {该论文 ∪ 伙伴论文} 交给改标判定，保证预算花在真实候选上。

全库根目录模式不做裁剪（默认 max_pairs=20 时若结果为空，可调大
--max-pairs 让更低 NCC 的候选进入判定）。

注意：语义层依赖视觉模型端点（settings.vision_http_url，Qwen3-VL），端点
不可用时检测器 fail-open 返回空——此时 CSV 只有表头，属预期降级。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/scan_relabeled_reuse.py
    .venv/Scripts/python.exe scripts/scan_relabeled_reuse.py \\
        --figures-dir uploads/figures/fraud_berberine \\
        --ncc-threshold 0.92 --max-pairs 20 --out deliverables/relabeled_reuse.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 控制台 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from mock_api.experiment_audit.figure_reuse import (  # noqa: E402
    DEFAULT_BAND_NCC_THRESHOLD,
    detect_cross_paper_band_reuse,
    detect_relabeled_image_reuse,
)

_IMG_SUFFIXES = (".png", ".jpg", ".jpeg")

_CSV_FIELDS = ("figure_a", "figure_b", "title", "claim")


def _dedupe_identical_files(
    figures_by_paper: dict[str, list[str]],
) -> dict[str, list[str]]:
    """按内容 hash 去重跨论文字节完全相同的图文件副本，保留首次出现。

    与 scan_cross_paper_bands._dedupe_identical_files 同语义：字节完全相同的整图
    副本（如 diag_berberine/fraud_berberine 两份副本）会产生 NCC=1.0 自匹配，把
    改标判定预算（top-N NCC 图对）占满，须先剔除。读失败的文件不拦截。
    """
    seen: set[str] = set()
    out: dict[str, list[str]] = {}
    removed = 0
    for paper, paths in figures_by_paper.items():
        kept: list[str] = []
        for p in paths:
            try:
                digest = hashlib.sha256(Path(p).read_bytes()).hexdigest()
            except OSError:
                kept.append(p)
                continue
            if digest in seen:
                removed += 1
                continue
            seen.add(digest)
            kept.append(p)
        if kept:
            out[paper] = kept
    if removed:
        print(f"去重：跨论文字节相同的图文件副本 {removed} 个（NCC=1.0 自匹配来源）")
    return out


def _image_files(d: Path) -> list[str]:
    """目录内直接含有的图片文件（不递归子目录）。"""
    return [
        str(p)
        for p in sorted(d.iterdir())
        if p.is_file() and p.suffix.lower() in _IMG_SUFFIXES
    ]


def build_figures_by_paper(figures_dir: str | Path) -> tuple[dict[str, list[str]], Path, str | None]:
    """扫描 figures 目录，返回 (figures_by_paper, 实际根目录, 叶子论文名或 None)。

    与 scan_cross_paper_bands.build_figures_by_paper 逻辑一致：若目录内直接含
    图片，视为单篇论文目录并上溯到父级根目录做跨论文扫描；否则每个含图片的
    子目录 = 一篇论文。
    """
    base = Path(figures_dir).resolve()
    direct = _image_files(base)
    if direct:
        root = base.parent
        papers: dict[str, list[str]] = {base.name: direct}
        for d in sorted(root.iterdir()):
            if d == base or not d.is_dir():
                continue
            imgs = _image_files(d)
            if imgs:
                papers[d.name] = imgs
        return papers, root, base.name

    papers = {}
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        imgs = _image_files(d)
        if imgs:
            papers[d.name] = imgs
    return papers, base, None


def _focus_on_partners(
    figures_by_paper: dict[str, list[str]],
    leaf: str,
    ncc_threshold: float,
) -> tuple[dict[str, list[str]], list[str]]:
    """裁剪到 {叶子论文} ∪ {与叶子论文有条带复用候选的伙伴论文}。

    返回 (裁剪后的 dict, 伙伴论文名列表)。伙伴列表为空时返回原 dict 的单论文
    子集（无像素复用证据，改标判定无从谈起）。
    """
    band_cands = detect_cross_paper_band_reuse(
        figures_by_paper, ncc_threshold=ncc_threshold
    )
    partners = sorted(
        {
            c["paper_b"] if c["paper_a"] == leaf else c["paper_a"]
            for c in band_cands
            if leaf in (c["paper_a"], c["paper_b"])
        }
    )
    focused = {p: figures_by_paper[p] for p in [leaf, *partners] if p in figures_by_paper}
    return focused, partners


def _figure_pair(finding: dict) -> tuple[str, str]:
    """从 finding 的 evidence_sources 提取 (figure_a, figure_b) 文件名。

    RELABELED_IMAGE_REUSE 的 evidence_sources 是两条镜像的 figure 记录
    （figure_id / other_figure_id），取第一条即可。
    """
    for src in finding.get("evidence_sources") or []:
        if src.get("type") == "figure":
            return str(src.get("figure_id", "")), str(src.get("other_figure_id", ""))
    return "", ""


def main() -> int:
    ap = argparse.ArgumentParser(description="改标图片复用证据链扫描（NCC 像素 + VLM 标签比对）")
    ap.add_argument("--figures-dir", default=str(ROOT / "uploads" / "figures"))
    ap.add_argument("--ncc-threshold", type=float, default=DEFAULT_BAND_NCC_THRESHOLD)
    ap.add_argument("--max-pairs", type=int, default=20)
    ap.add_argument("--out", default=str(ROOT / "deliverables" / "relabeled_reuse.csv"))
    args = ap.parse_args()

    figures_dir = Path(args.figures_dir)
    if not figures_dir.is_dir():
        print(f"目录不存在: {figures_dir}")
        return 1

    figures_by_paper, root, leaf = build_figures_by_paper(figures_dir)
    if not figures_by_paper:
        print(f"未在 {figures_dir} 找到任何论文图目录")
        return 1

    figures_by_paper = _dedupe_identical_files(figures_by_paper)

    if leaf is not None:
        print(
            f"注意: {figures_dir} 是单篇论文目录，已上溯到根目录 {root} "
            f"做跨论文扫描（共 {len(figures_by_paper)} 篇论文）"
        )
        n_figs = sum(len(v) for v in figures_by_paper.values())
        print(f"扫描 {len(figures_by_paper)} 篇论文 / {n_figs} 张图，先找 {leaf} 的条带复用伙伴 …")
        focused, partners = _focus_on_partners(figures_by_paper, leaf, args.ncc_threshold)
        if not partners:
            print(f"{leaf} 未发现任何跨论文条带复用候选（NCC ≥ {args.ncc_threshold}），改标判定无像素证据可依")
            focused = {leaf: figures_by_paper[leaf]}
        else:
            print(
                f"伙伴论文 {len(partners)} 篇: {', '.join(partners)}；"
                f"改标判定限定在 {len(focused)} 篇论文内"
            )
        figures_by_paper = focused
    else:
        n_figs = sum(len(v) for v in figures_by_paper.values())
        print(f"扫描 {len(figures_by_paper)} 篇论文 / {n_figs} 张图 …")

    print("改标复用检测需调视觉模型（Qwen3-VL）提取目标蛋白，请稍候 …")
    findings = detect_relabeled_image_reuse(
        figures_by_paper,
        ncc_threshold=args.ncc_threshold,
        max_pairs=args.max_pairs,
    )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_CSV_FIELDS)
        w.writeheader()
        for f in findings:
            fig_a, fig_b = _figure_pair(f)
            w.writerow(
                {
                    "figure_a": fig_a,
                    "figure_b": fig_b,
                    "title": f.get("title", ""),
                    "claim": f.get("claim", ""),
                }
            )

    print(f"改标图片复用候选总数: {len(findings)}（NCC 阈值 {args.ncc_threshold}，max_pairs {args.max_pairs}）")
    print(f"已落盘: {out}")
    for f in findings[:10]:
        fig_a, fig_b = _figure_pair(f)
        print(f"  {fig_a} vs {fig_b}: {f.get('title', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
