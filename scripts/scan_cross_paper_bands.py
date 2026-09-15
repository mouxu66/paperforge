#!/usr/bin/env python3
"""跨论文 western-blot 条带（band）级复用扫描（离线，NCC 像素比对）。

扫描 figures 根目录下所有论文的 figure，跑 detect_cross_paper_band_reuse
（NCC ≥ 阈值），候选对按 ncc 降序落 CSV 供人工复核。

--figures-dir 语义：
- 默认 uploads/figures/（根目录：每个含图片的子目录 = 一篇论文）；
- 若指向某篇论文的目录（目录内直接含图片），自动上溯到父级根目录做跨论文
  扫描——单篇论文无法产生「跨论文」候选，上溯后该论文可与库内其余论文两两
  比对（例如指向 uploads/figures/fraud_berberine 也能命中与 kjpp 的复用）。

用法（仓库根目录）：
    .venv/Scripts/python.exe scripts/scan_cross_paper_bands.py
    .venv/Scripts/python.exe scripts/scan_cross_paper_bands.py \\
        --figures-dir uploads/figures --ncc-threshold 0.92 \\
        --out deliverables/cross_paper_bands.csv
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
)

_IMG_SUFFIXES = (".png", ".jpg", ".jpeg")

_BAND_FIELDS = ("paper_a", "fig_a", "band_a", "paper_b", "fig_b", "band_b", "ncc")


def _image_files(d: Path) -> list[str]:
    """目录内直接含有的图片文件（不递归子目录）。"""
    return [
        str(p)
        for p in sorted(d.iterdir())
        if p.is_file() and p.suffix.lower() in _IMG_SUFFIXES
    ]


def build_figures_by_paper(figures_dir: str | Path) -> tuple[dict[str, list[str]], Path, bool]:
    """扫描 figures 目录，返回 (figures_by_paper, 实际根目录, 是否上溯)。

    - 目录内直接含图片 → 视为单篇论文目录：上溯到父级根目录，把该论文与兄弟
      论文一并纳入（单篇论文无法产生跨论文候选，上溯才能让指向任意一篇论文
      的调用仍能发现它与库内其他论文的复用）。
    - 否则视为根目录：每个含图片的子目录 = 一篇论文。
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
        return papers, root, True

    papers = {}
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        imgs = _image_files(d)
        if imgs:
            papers[d.name] = imgs
    return papers, base, False


def _format_band(band: tuple[int, int, int, int]) -> str:
    """条带 bbox (x, y, w, h) → 'x,y,w,h' 字符串（csv 模块会加引号）。"""
    return ",".join(str(int(v)) for v in band)


def _dedupe_identical_files(
    figures_by_paper: dict[str, list[str]],
) -> dict[str, list[str]]:
    """按内容 hash 去重跨论文字节完全相同的图文件副本，保留首次出现。

    同一份图文件被复制到多个论文目录（如 diag_berberine/fraud_berberine 是同一篇
    论文的两份副本）会产生 NCC=1.0 的自匹配噪音；band 级扫描只关心「不同合成图里
    复用的同一条带」，字节完全相同的整图副本不是本扫描目标（整图复用由
    detect_cross_paper_reuse / detect_figure_reuse 覆盖）。读失败的文件不拦截。
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


def main() -> int:
    ap = argparse.ArgumentParser(description="跨论文 western-blot 条带级复用扫描（NCC 像素比对）")
    ap.add_argument("--figures-dir", default=str(ROOT / "uploads" / "figures"))
    ap.add_argument("--ncc-threshold", type=float, default=DEFAULT_BAND_NCC_THRESHOLD)
    ap.add_argument("--out", default=str(ROOT / "deliverables" / "cross_paper_bands.csv"))
    args = ap.parse_args()

    figures_dir = Path(args.figures_dir)
    if not figures_dir.is_dir():
        print(f"目录不存在: {figures_dir}")
        return 1

    figures_by_paper, root, lifted = build_figures_by_paper(figures_dir)
    if not figures_by_paper:
        print(f"未在 {figures_dir} 找到任何论文图目录")
        return 1
    if lifted:
        print(
            f"注意: {figures_dir} 是单篇论文目录，已上溯到根目录 {root} "
            f"做跨论文扫描（共 {len(figures_by_paper)} 篇论文）"
        )

    figures_by_paper = _dedupe_identical_files(figures_by_paper)

    n_figs = sum(len(v) for v in figures_by_paper.values())
    print(f"扫描 {len(figures_by_paper)} 篇论文 / {n_figs} 张图 …")
    cands = detect_cross_paper_band_reuse(
        figures_by_paper, ncc_threshold=args.ncc_threshold
    )
    cands = sorted(cands, key=lambda c: c["ncc"], reverse=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=_BAND_FIELDS)
        w.writeheader()
        for c in cands:
            w.writerow(
                {
                    "paper_a": c["paper_a"],
                    "fig_a": c["fig_a"],
                    "band_a": _format_band(c["band_a"]),
                    "paper_b": c["paper_b"],
                    "fig_b": c["fig_b"],
                    "band_b": _format_band(c["band_b"]),
                    "ncc": c["ncc"],
                }
            )

    print(f"跨论文条带复用候选总数: {len(cands)}（NCC 阈值 {args.ncc_threshold}）")
    print(f"已落盘: {out}")
    for c in cands[:10]:
        print(
            f"  {c['paper_a']}/{Path(c['fig_a']).name}{_format_band(c['band_a'])} vs "
            f"{c['paper_b']}/{Path(c['fig_b']).name}{_format_band(c['band_b'])} "
            f"(NCC={c['ncc']})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
