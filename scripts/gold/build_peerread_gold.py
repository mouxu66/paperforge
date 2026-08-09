"""从真实 PeerRead 重评分数据构建论文侧金标（ADR-014 · P3，诚实化）。

数据源：deliverables/peerread_rescore_n200_base.jsonl（N=198，ACL/arXiv 2007-2017）
  - **offset=0 真基线**：由 scripts/calibration/peerread_rescore.py run --baseline 产出
    （偏移表清零 {default:0, peerread:0} + 封顶关闭），每行 calibrated = base + Q5c-delta，
    残差恒为 0（2026-08-09 验证）→ 可安全叠加任意偏移做阈值扫描。
  - 含**真实人类标注** human_accepted（PeerRead 官方 accept/reject）
  - ⚠️ 不要用 peerread_rescore_n200_fix.jsonl：其分已内嵌 +0.34 偏移实验，非基线。
输出：deliverables/gold/peerread_verdict_gold.json（金标回归/偏移校准使用）

关键诚实声明：
  - 这是「verdict 校准金标」：人类只给了二进制 accept/reject，
    因此 4 档 verdict 校准只对比 accept/reject 边界，不冒充多维人工分。
  - 数据全部来自真实 PeerRead 数据集，非合成占位（对比 depth_gold.json 是
    PLACEHOLDER_SYNTHETIC，本文件是生产级真实金标）。
  - 2026-08-09 重扫结论（deliverables/gold/peerread_offset_scan_t06.json）：
    生产阈值 accept≥0.6 下 N=198 最优偏移为 -0.02（κ=0.414 / 一致率 70.7%），
    采纳稳健圆整值 0.0（κ=0.364 / 68.2%）。旧 DEFAULT_OFFSET_TABLE["peerread"]=0.18
    是 0.8 阈值时代的扫描产物，在 0.6 阈值下过度接受（56.6%），已移除。

用法：
    python scripts/gold/build_peerread_gold.py
    python scripts/gold/build_peerread_gold.py --source deliverables/peerread_rescore_n200_base.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SOURCE_CANDIDATES = [
    ROOT / "deliverables" / "peerread_rescore_n200_base.jsonl",
    ROOT / "deliverables" / "peerread_rescore_n200_fix.jsonl",
    ROOT / "deliverables" / "peerread_rescore_v2.jsonl",
]
OUT_PATH = ROOT / "deliverables" / "gold" / "peerread_verdict_gold.json"

VALID = {"accept", "minor_revision", "major_revision", "reject"}


def _load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _pick_source() -> Path | None:
    """选数据源。金标是回归基准，源必须可复现：默认只认主源
    peerread_rescore_n200_base.jsonl（offset=0 真基线）；其余候选仅作
    显式 --source 使用。主源缺失时明确报错而非静默换源（否则会悄悄
    重建出不同的金标）。"""
    primary = DEFAULT_SOURCE_CANDIDATES[0]
    if primary.exists():
        return primary
    print(
        f"⚠ 主数据源不存在: {primary}\n"
        f"   请先恢复该文件，或显式用 --source 指定其他 jsonl（将记录进 generated_from）。",
        file=sys.stderr,
    )
    return None


def build_gold(source: Path | None) -> dict:
    src = Path(source) if source else _pick_source()
    if src is None or not src.exists():
        sys.exit(f"找不到 PeerRead 重评分数据源: {src or '主源缺失（见上方提示）'}")
    rows = _load_rows(src)
    samples: list[dict] = []
    skipped = 0
    for r in rows:
        stem = str(r.get("stem", "") or "")
        human = r.get("human_accepted")
        calib = r.get("depth_calibrated_score")
        verdict = str(r.get("depth_verdict") or "").strip().lower().replace(" ", "_")
        # 只保留有真实标签 + 有实测分的行
        if human is None or calib is None:
            skipped += 1
            continue
        if verdict not in VALID:
            verdict = "reject" if human is False else "accept"
        samples.append(
            {
                "paper_id": stem,
                "title": r.get("title", ""),
                "venue": r.get("venue", ""),
                "human_accepted": bool(human),
                # 人类标签 → 4 档 verdict（accept/reject 为真实标签；另两档不存在）
                "verdict": "accept" if human else "reject",
                "scores": {"calibrated": round(float(calib), 4)},
                "depth_verdict": verdict,
                "depth_base_score": r.get("depth_base_score"),
                "depth_delta": r.get("depth_delta"),
            }
        )

    if len(samples) < 20:
        sys.exit(f"有效样本过少（{len(samples)}），拒绝生成金标以避免过拟合。")

    gold = {
        "schema_version": 1,
        "source": "peerread (real human labels, ACL/arXiv 2007-2017)",
        "note": (
            "真实人工标注金标（PeerRead accept/reject，N={n}）。"
            "用于 verdict 阈值/偏移校准的回归基准：scores.calibrated 是 DEPTH 在"
            "offset=0 基线（偏移表清零 + 封顶关闭）下对全文的实测分（calibrated = base + "
            "Q5c-delta，残差恒 0），可安全叠加任意偏移做阈值扫描。"
            "verdict 是【人类】accept/reject（映射为 4 档中的 accept/reject）。"
            "注意这是二进制裁决金标，不做多维人工分校准。"
            "由 scripts/gold/build_peerread_gold.py 从 peerread_rescore_n200_base.jsonl 生成。"
        ).format(n=len(samples)),
        "generated_from": str(src.name),
        "samples": samples,
    }
    return gold


def main() -> None:
    ap = argparse.ArgumentParser(description="构建真实 PeerRead 论文侧金标")
    ap.add_argument("--source", default=None, help="peerread rescore jsonl 路径")
    ap.add_argument("--out", default=str(OUT_PATH), help="输出 gold json 路径")
    args = ap.parse_args()

    gold = build_gold(args.source)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(gold, f, ensure_ascii=False, indent=2)

    samples = gold["samples"]
    # 复现偏移扫描结论：在真实金标上扫描最优偏移（同时看一致率与 Cohen κ）
    def _predict(offset: float, s: dict) -> bool:
        pred = min(1.0, max(0.0, float(s["scores"]["calibrated"]) + offset))
        return pred >= 0.6  # 生产 verdict_accept_threshold

    def _binary_kappa(offset: float, rows: list[dict]) -> float:
        pred = [1 if _predict(offset, s) else 0 for s in rows]
        truth = [1 if s["human_accepted"] else 0 for s in rows]
        n = len(rows)
        po = sum(1 for x, y in zip(pred, truth) if x == y) / n
        pa = (sum(pred) / n) * (sum(truth) / n) + (1 - sum(pred) / n) * (1 - sum(truth) / n)
        return (po - pa) / (1 - pa) if pa < 1 else (1.0 if po == 1.0 else 0.0)

    best_acc = (0.0, 0, 0.0)
    best_kap = (0.0, 0.0)
    for off in [round(x * 0.01, 2) for x in range(-30, 51)]:
        agree = sum(1 for s in samples if _predict(off, s) == bool(s["human_accepted"]))
        acc = agree / len(samples)
        if acc > best_acc[2]:
            best_acc = (off, agree, acc)
        k = _binary_kappa(off, samples)
        if k > best_kap[1]:
            best_kap = (off, k)
    # 采纳口径：round 稳健值（0.0），数据最优值记录进审计（recommended）
    agree0 = sum(1 for s in samples if _predict(0.0, s) == bool(s["human_accepted"]))
    kap0 = _binary_kappa(0.0, samples)

    print(f"金标已写入: {out}")
    print(f"样本数: {len(samples)}（人类 accept={sum(1 for s in samples if s['human_accepted'])}, "
          f"reject={sum(1 for s in samples if not s['human_accepted'])}）")
    print(f"DEPTH verdict 分布: {dict(Counter(s['depth_verdict'] for s in samples))}")
    print(f"采纳偏移(0.0)一致率: {agree0}/{len(samples)} = {agree0 / len(samples):.3f}  κ={kap0:.3f}")
    print(f"数据最优偏移: {best_acc[0]:+.2f} → 一致率 {best_acc[1]}/{len(samples)} = {best_acc[2]:.3f}"
          f"（κ 最优 {best_kap[0]:+.2f} → κ={best_kap[1]:.3f}）")


if __name__ == "__main__":
    main()
