"""Ornith(新本地) vs Ornstein(旧千问) vs 多AI共识 —— 41 篇感悟报告三方对比。

输入：
  - Ornith 全量结果：deliverables/ornith_reports_41_results.json（回填脚本产出，41 条）
    或回退解析 deliverables/ornith_reports_41.log 的 JSON 行
  - 共识 + Ornstein 旧分：deliverables/ornstein_vs_human_full.csv
      （human_total / human_UA,AD,II,ES = 多AI共识；orn_avg / orn_UA,AD,II,ES = Ornstein 旧分）
  - 备选 6 维共识：deliverables/buffy_full_41.csv（UA,AD,II,ES,fidelity,coverage,total）

输出：
  - deliverables/ornith_vs_consensus_41.csv    逐篇三方分数
  - deliverables/ornith_vs_consensus_41.md     对比分析（r / MAE / 维度 / 结论）
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[2]
DEL = ROOT / "deliverables"
ORNITH_JSON = DEL / "ornith_reports_41_results.json"
ORNITH_LOG = DEL / "ornith_reports_41.log"
ORNSTEIN_CSV = DEL / "ornstein_vs_human_full.csv"
BUFFY_CSV = DEL / "buffy_full_41.csv"
OUT_CSV = DEL / "ornith_vs_consensus_41.csv"
OUT_MD = DEL / "ornith_vs_consensus_41.md"

DIMS_4 = ["UA", "AD", "II", "ES"]  # 共识/Ornstein 仅有这 4 维
DIM_MAP = {  # ornith json key -> 短名
    "understanding_accuracy": "UA",
    "analysis_depth": "AD",
    "innovative_insights": "II",
    "evidence_support": "ES",
    "fidelity": "FID",
    "coverage": "COV",
}


def load_ornith() -> dict[str, dict]:
    data: dict[str, dict] = {}
    src = None
    if ORNITH_JSON.exists():
        src = json.loads(ORNITH_JSON.read_text(encoding="utf-8"))
    else:
        lines = ORNITH_LOG.read_text(encoding="utf-8").splitlines()
        arr = []
        for ln in lines:
            ln = ln.strip()
            if ln.startswith("{"):
                try:
                    arr.append(json.loads(ln))
                except json.JSONDecodeError:
                    pass
        src = arr
    for o in src:
        rid = o.get("report")
        if rid:
            # 归一化：共识/Ornstein CSV 的 sid 是纯数字，Ornith 的 report 带 reflection_ 前缀
            data[rid.replace("reflection_", "")] = o
    return data


def load_ornstein_consensus() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in csv.DictReader(open(ORNSTEIN_CSV, encoding="utf-8-sig")):
        sid = r["sid"]
        out[sid] = {
            "human_total": float(r["human_total"]),
            "human": {d: float(r[f"human_{d}"]) for d in DIMS_4},
            "orn_avg": float(r["orn_avg"]),
            "orn": {d: float(r[f"orn_{d}"]) for d in DIMS_4},
            "orn_seconds": float(r.get("seconds", 0) or 0),
        }
    return out


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = mean(xs), mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def mae(xs: list[float], ys: list[float]) -> float:
    return mean(abs(x - y) for x, y in zip(xs, ys))


def main() -> int:
    ornith = load_ornith()
    base = load_ornstein_consensus()
    print(f"[compare] ornith={len(ornith)} consensus/ornstein={len(base)}", flush=True)

    common = [sid for sid in base if sid in ornith]
    print(f"[compare] common reports={len(common)}", flush=True)
    if len(common) < 30:
        print("[compare] WARN: too few common reports", flush=True)

    rows = []
    for sid in common:
        o = ornith[sid]
        b = base[sid]
        ornith_scores = {DIM_MAP[k]: (o["scores"].get(k) or 0.0) for k in DIM_MAP}
        ornith_avg = o.get("average") or 0.0
        rows.append({
            "sid": sid,
            "consensus_total": b["human_total"],
            "ornstein_avg": b["orn_avg"],
            "ornith_avg": round(ornith_avg, 4),
            "ornith_seconds": o.get("seconds"),
            "ornstein_seconds": b["orn_seconds"],
            "cons_UA": b["human"]["UA"], "cons_AD": b["human"]["AD"],
            "cons_II": b["human"]["II"], "cons_ES": b["human"]["ES"],
            "ornst_UA": b["orn"]["UA"], "ornst_AD": b["orn"]["AD"],
            "ornst_II": b["orn"]["II"], "ornst_ES": b["orn"]["ES"],
            "ornith_UA": round(ornith_scores["UA"], 4), "ornith_AD": round(ornith_scores["AD"], 4),
            "ornith_II": round(ornith_scores["II"], 4), "ornith_ES": round(ornith_scores["ES"], 4),
            "ornith_FID": round(ornith_scores["FID"], 4), "ornith_COV": round(ornith_scores["COV"], 4),
            "verdict": o.get("verdict"),
            "copy_ratio": o.get("copy_ratio"),
            "llm_failed": o.get("llm_failed"),
        })

    # 写入逐篇 CSV
    fields = list(rows[0].keys())
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # 统计
    cons_t = [r["consensus_total"] for r in rows]
    ornst_t = [r["ornstein_avg"] for r in rows]
    ornith_t = [r["ornith_avg"] for r in rows]

    def block(name, model_vals, cons_vals):
        r = pearson(model_vals, cons_vals)
        m = mae(model_vals, cons_vals)
        bias = mean(v - c for v, c in zip(model_vals, cons_vals))
        return r, m, bias

    lines = []
    lines.append("# Ornith(新本地) vs Ornstein(旧千问) vs 多AI共识 —— 41 篇感悟报告对比\n")
    lines.append(f"样本量 n = {len(rows)}（与 Ornstein 旧跑完全相同的 41 篇 + 同一共识标签）\n")
    lines.append("配置：Ornith 本地 Ornith-1.5-9B-Q4_K_M @ llama-server:8080，evidence=1, thinking=0, II_SAMPLES=1（单调用）。\n")

    lines.append("## 1. 总分组（加权平均分 vs 共识 total）\n")
    lines.append("| 模型 | 与共识 Pearson r | MAE | 平均偏差(模型−共识) | 模型均分 | 共识均分 |")
    lines.append("|---|---|---|---|---|---|")
    for label, vals in [("Ornstein(旧千问)", ornst_t), ("Ornith(新本地)", ornith_t)]:
        r, m, bias = block(label, vals, cons_t)
        lines.append(f"| {label} | {r:.3f} | {m:.4f} | {bias:+.4f} | {mean(vals):.4f} | {mean(cons_t):.4f} |")
    lines.append("")
    lines.append("> 偏差为正=模型比共识更宽松(给分偏高)；为负=更严。r 越高越贴合共识排序。\n")

    lines.append("## 2. 四维逐项（UA/AD/II/ES，共识与 Ornstein 均覆盖的维度）\n")
    lines.append("| 维度 | Ornstein r | Ornstein MAE | Ornith r | Ornith MAE |")
    lines.append("|---|---|---|---|---|")
    for d in DIMS_4:
        cons_d = [r[f"cons_{d}"] for r in rows]
        ornst_d = [r[f"ornst_{d}"] for r in rows]
        ornith_d = [r[f"ornith_{d}"] for r in rows]
        r1 = pearson(ornst_d, cons_d)
        r2 = pearson(ornith_d, cons_d)
        lines.append(f"| {d} | {r1:.3f} | {mae(ornst_d, cons_d):.4f} | {r2:.3f} | {mae(ornith_d, cons_d):.4f} |")
    lines.append("")

    # 全 6 维 Ornith vs buffy（若可用）
    if BUFFY_CSV.exists():
        buffy = {r["sid"]: r for r in csv.DictReader(open(BUFFY_CSV, encoding="utf-8-sig"))}
        common6 = [sid for sid in common if sid in buffy]
        if common6:
            lines.append("## 3. Ornith 全 6 维 vs buffy 共识（含 fidelity/coverage）\n")
            lines.append("| 维度 | Ornith r | Ornith MAE |")
            lines.append("|---|---|---|")
            for d in ["UA", "AD", "II", "ES", "FID", "COV"]:
                col = {"UA": "understanding_accuracy", "AD": "analysis_depth", "II": "innovative_insights",
                       "ES": "evidence_support", "FID": "fidelity", "COV": "coverage"}[d]
                cons_b = [float(buffy[sid][col]) for sid in common6]
                orn_b = [ornith[sid]["scores"].get(col) or 0.0 for sid in common6]
                r = pearson(orn_b, cons_b)
                lines.append(f"| {d} | {r:.3f} | {mae(orn_b, cons_b):.4f} |")
            # 总分组 Ornith vs buffy total
            bt = [float(buffy[sid]["total"]) for sid in common6]
            ot = [ornith[sid]["average"] or 0.0 for sid in common6]
            rb = pearson(ot, bt)
            lines.append(f"\n**Ornith avg vs buffy total**：r={rb:.3f}, MAE={mae(ot, bt):.4f}（n={len(common6)}）\n")

    # 速度
    lines.append("## 4. 单篇耗时\n")
    ornith_sec = [r["ornith_seconds"] for r in rows if r["ornith_seconds"]]
    ornst_sec = [r["ornstein_seconds"] for r in rows if r["ornstein_seconds"]]
    if ornith_sec:
        lines.append(f"- Ornith(新)：均 {mean(ornith_sec):.1f}s/篇，中位 {sorted(ornith_sec)[len(ornith_sec)//2]:.1f}s\n")
    if ornst_sec:
        lines.append(f"- Ornstein(旧，II_SAMPLES=3)：均 {mean(ornst_sec):.1f}s/篇\n")

    # 结论
    r_ornst, _, _ = block("Ornstein", ornst_t, cons_t)
    r_ornith, _, bias_ornith = block("Ornith", ornith_t, cons_t)
    lines.append("## 5. 结论\n")
    better = "Ornith(新本地)" if (r_ornith or 0) > (r_ornst or 0) else "Ornstein(旧千问)"
    lines.append(f"- 与多AI共识的排序相关性：**Ornstein r={r_ornst:.3f}**，**Ornith r={r_ornith:.3f}**"
                 f"→ 本批上 **{better}** 更贴合共识排序。")
    lines.append(f"- Ornith 平均偏差 {bias_ornith:+.4f}（{'偏高' if bias_ornith>0 else '偏低'}于共识），"
                 f"说明新模型在校准后整体给分{'更松' if bias_ornith>0 else '更紧'}。")
    lines.append(f"- 速度上 Ornith 单篇 {mean(ornith_sec):.0f}s 显著快于 Ornstein 旧跑的 {mean(ornst_sec):.0f}s"
                 f"（且 Ornith 已关思考+II 单采样，Ornstein 旧跑仍是 3 采样）。")
    lines.append("\n> 注：共识为 41 篇「多AI共同评审整合」标签（非人工金标）；r 衡量排序一致性，MAE 衡量绝对误差。")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"[compare] wrote {OUT_CSV.name} ({len(rows)} rows) + {OUT_MD.name}", flush=True)
    print(f"[compare] Ornstein r={r_ornst:.3f} Ornith r={r_ornith:.3f} Ornith MAE={mae(ornith_t,cons_t):.4f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
