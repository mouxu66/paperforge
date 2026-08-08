"""千问校准层 (ADR-014 P7) 对比：校准关闭 vs 开启，偏离 5-AI 中位数基线的变化。

无需 LLM 服务：直接把 R5/R6/R7 三条确定性封顶规则套到
`deliverables/exp_b_qwen_full.csv` 的千问原始 4 维分上得到校准后分数，
再用 `deliverables/wb_43_baseline.csv` 里另外 4 个 AI 的分数重算 5-AI 中位数，
计算偏离变化。

关键事实：基线报告里的 +0.094 就是用 exp_b_qwen_full.csv（千问原始分）算出来的，
所以这里套 P7 的输入与原始偏离完全同源，比较公平。
"""
from __future__ import annotations

import csv
import glob
import os
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import depth_eval_reflection 之前打开校准开关，使常量按 ON 读取
os.environ["PAPERFORGE_QWEN_CALIBRATION"] = "1"

from mock_api.reflection_docx_parser import parse_docx_from_bytes  # noqa: E402
import mock_api.depth_eval_reflection as DER  # noqa: E402

UPLOADS = ROOT / "mock_api" / "uploads"
EXP_B = ROOT / "deliverables" / "exp_b_qwen_full.csv"
BASE = ROOT / "deliverables" / "wb_43_baseline.csv"
OUT = ROOT / "deliverables" / "qwen_p7_calibration_compare.csv"

DIM_KEYS = ("understanding_accuracy", "analysis_depth", "innovative_insights", "evidence_support")


def find_docx(sid: str) -> str | None:
    hits = sorted(glob.glob(str(UPLOADS / f"reflection_*{sid}*.docx")))
    return hits[0] if hits else None


def f2(x: str) -> float | None:
    """空串/非法 → None。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def apply_p7(u: float, a: float, i: float, e: float, raw_text: str) -> tuple[dict, list[str]]:
    """原样复刻 depth_eval_reflection.py 的 P7 块（R5/R6/R7），仅此一块。"""
    out = {
        "understanding_accuracy": u,
        "analysis_depth": a,
        "innovative_insights": i,
        "evidence_support": e,
    }
    overrides: list[str] = []
    if not raw_text:
        return out, overrides

    section_count = DER._count_sections(raw_text)
    ref_len = DER._reflection_section_length(raw_text)
    total_chars = len(raw_text)
    rule_1_fired = False  # exp_b 是原始分，未套 R1，这里恒 False
    calib_any = False

    # R5
    if not rule_1_fired:
        if section_count <= 1 and total_chars > 100:
            for k in ("understanding_accuracy", "analysis_depth"):
                if out[k] > DER._QWEN_CALIBRATION_2SEC_CAP:
                    out[k] = DER._QWEN_CALIBRATION_2SEC_CAP
                    calib_any = True
            overrides.append(
                f"R5(P7): 无段落标记, ua/ad 封顶 {DER._QWEN_CALIBRATION_2SEC_CAP}"
            )
        elif section_count <= 2:
            for k in list(out.keys()):
                if out[k] > DER._QWEN_CALIBRATION_2SEC_CAP:
                    out[k] = DER._QWEN_CALIBRATION_2SEC_CAP
                    calib_any = True
            if calib_any:
                overrides.append(f"R5(P7): 仅 {section_count} 段, 全维封顶 {DER._QWEN_CALIBRATION_2SEC_CAP}")
        elif section_count <= 3:
            iv = out["innovative_insights"]
            if iv > DER._QWEN_CALIBRATION_3SEC_CAP:
                out["innovative_insights"] = DER._QWEN_CALIBRATION_3SEC_CAP
                calib_any = True
                overrides.append(f"R5(P7): 仅 {section_count} 段, ii 封顶 {DER._QWEN_CALIBRATION_3SEC_CAP}")

    # R6
    if not rule_1_fired and ref_len >= 0 and ref_len < DER._QWEN_CALIBRATION_REFL_MIN_CHARS and section_count >= 4:
        iv = out["innovative_insights"]
        if iv > DER._QWEN_CALIBRATION_REFL_II_CAP:
            out["innovative_insights"] = DER._QWEN_CALIBRATION_REFL_II_CAP
            calib_any = True
            overrides.append(f"R6(P7): reflection 仅 {ref_len} 字, ii 封顶 {DER._QWEN_CALIBRATION_REFL_II_CAP}")

    # R7
    if not rule_1_fired and total_chars < DER._QWEN_CALIBRATION_SHORT_CHARS:
        avg = sum(out.values()) / 4
        if avg > DER._QWEN_CALIBRATION_SHORT_CAP:
            for k in list(out.keys()):
                if out[k] > DER._QWEN_CALIBRATION_SHORT_CAP:
                    out[k] = DER._QWEN_CALIBRATION_SHORT_CAP
                    calib_any = True
            if calib_any:
                overrides.append(f"R7(P7): 仅 {total_chars} 字, 全维封顶 {DER._QWEN_CALIBRATION_SHORT_CAP}")

    return out, overrides


def main():
    # 1) 读 exp_b 原始千问分
    qwen_raw: dict[str, dict] = {}
    with open(EXP_B, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sid = r["sid"]
            qwen_raw[sid] = {
                "u": float(r["u"]), "a": float(r["a"]),
                "i": float(r["i"]), "e": float(r["e"]),
                "avg": float(r["avg"]),
            }

    # 2) 读基线（5-AI 中位数 + 各 AI total）
    base: dict[str, dict] = {}
    orig_dev_sum = 0.0
    orig_dev_n = 0
    with open(BASE, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sid = r["sid"]
            qa = f2(r.get("qwen_avg", ""))
            dv = f2(r.get("dev_qwen_vs_med", ""))
            base[sid] = {
                "median": f2(r.get("median_total", "")) or 0.0,
                "bufy_v1": r.get("bufy_v1_total", ""),
                "bufy_v2": r.get("bufy_v2_total", ""),
                "cg": r.get("cg_total", ""),
                "human": r.get("human_total", ""),
                "qwen_avg": qa,
                "dev_qwen": dv,
            }
            if dv is not None:
                orig_dev_sum += dv
                orig_dev_n += 1

    orig_mean_dev = orig_dev_sum / orig_dev_n if orig_dev_n else float("nan")

    # 3) 套 P7，重算偏离
    rows = []
    new_dev_sum = 0.0
    new_dev_fixed_sum = 0.0
    new_dev_n = 0
    new_qwen_sum = 0.0
    fire_counts = {"R5": 0, "R6": 0, "R7": 0}
    capped_sids = []

    for sid, q in qwen_raw.items():
        docx = find_docx(sid)
        raw_text = ""
        if docx:
            try:
                with open(docx, "rb") as fh:
                    doc = parse_docx_from_bytes(fh.read(), filename=docx)
                raw_text = doc.raw_text or "\n".join((doc.sections or {}).values())
            except Exception as ex:  # noqa: BLE001
                raw_text = ""
                print(f"[warn] {sid} docx 解析失败: {ex}", flush=True)

        capped, ovs = apply_p7(q["u"], q["a"], q["i"], q["e"], raw_text)
        new_avg = round(sum(capped.values()) / 4, 4)

        # 规则命中计数
        for tag in ("R5", "R6", "R7"):
            if any(o.startswith(tag) for o in ovs):
                fire_counts[tag] += 1
        if ovs:
            capped_sids.append((sid, ovs, q["avg"], new_avg))

        # 重算 5-AI 中位数（含新千问）
        b = base.get(sid)
        others = []
        if b:
            for key in ("bufy_v1", "bufy_v2", "cg", "human"):
                v = b.get(key, "")
                if v not in ("", None):
                    try:
                        others.append(float(v))
                    except ValueError:
                        pass
        pool = others + [new_avg]
        new_median = statistics.median(pool) if pool else new_avg

        # 偏离：相对"重算基线" 与 相对"固定原基线"
        if b:
            new_dev = new_avg - new_median
            new_dev_fixed = new_avg - b["median"]
            new_dev_sum += new_dev
            new_dev_fixed_sum += new_dev_fixed
            new_dev_n += 1
        else:
            new_dev = float("nan")
            new_dev_fixed = float("nan")

        new_qwen_sum += new_avg
        rows.append({
            "sid": sid,
            "qwen_raw_avg": f"{q['avg']:.4f}",
            "qwen_p7_avg": f"{new_avg:.4f}",
            "delta": f"{new_avg - q['avg']:+.4f}",
            "orig_median": f"{b['median']:.4f}" if b else "",
            "new_median": f"{new_median:.4f}",
            "dev_vs_new_med": f"{new_dev:+.4f}" if b else "",
            "dev_vs_orig_med": f"{new_dev_fixed:+.4f}" if b else "",
            "rules": "; ".join(o.split(":")[0] for o in ovs),
        })

    mean_new_qwen = new_qwen_sum / len(qwen_raw) if qwen_raw else float("nan")
    mean_orig_qwen = sum(q["avg"] for q in qwen_raw.values()) / len(qwen_raw)
    new_mean_dev = new_dev_sum / new_dev_n if new_dev_n else float("nan")
    new_mean_dev_fixed = new_dev_fixed_sum / new_dev_n if new_dev_n else float("nan")

    # 4) 落盘
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # 5) 汇总
    print("=" * 60)
    print("千问校准层 (P7) 对比结果")
    print("=" * 60)
    print(f"样本数: {len(qwen_raw)} 篇 (exp_b 原始千问分)")
    print(f"原始千问均值:        {mean_orig_qwen:.4f}")
    print(f"校准后千问均值:      {mean_new_qwen:.4f}  (Δ={mean_new_qwen-mean_orig_qwen:+.4f})")
    print("-" * 60)
    print(f"原始偏离基线 (复核):  {orig_mean_dev:+.4f}   (预期 +0.094)")
    print(f"校准后偏离 (重算基线): {new_mean_dev:+.4f}   (降 {orig_mean_dev-new_mean_dev:+.4f})")
    print(f"校准后偏离 (固定基线): {new_mean_dev_fixed:+.4f}   (降 {orig_mean_dev-new_mean_dev_fixed:+.4f})")
    print("-" * 60)
    print("规则命中篇数:")
    for tag, c in fire_counts.items():
        print(f"  {tag}: {c} 篇")
    print(f"被校准命中的报告共 {len(capped_sids)} 篇")
    print("-" * 60)
    print("被校准的报告明细 (sid | 规则 | 原始→校准):")
    for sid, ovs, raw_a, new_a in capped_sids:
        print(f"  {sid}  {'/'.join(o.split(':')[0] for o in ovs)}  {raw_a:.3f}→{new_a:.3f}")
    print("=" * 60)
    print(f"明细已写入: {OUT}")


if __name__ == "__main__":
    main()
