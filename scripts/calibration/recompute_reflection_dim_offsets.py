"""用 41 篇人工金标重算感悟报告 4 维 LLM 分的 per-dim 校准偏移。

与 recompute_gold_offset.py 同构，但作用在 reflection 侧：对每个 LLM 维度拟合
系统分相对人工分的平均偏差 offset[dim] = mean(系统 - 人工)，落盘
calib_papers/runs/reflection_dim_offsets.json，供 reflection_calibration 在推理时
逐维减去（新分 = clamp(旧分 - offset)）。

数据来源（本仓既有，非合成）：
  - deliverables/human_benchmark_full.csv          : 41 篇人工金标（6 维 + total）
  - deliverables/ornstein_vs_human_full.csv        : 同 41 篇的 Ornstein 9B 系统分
    （human_UA/AD/II/ES + orn_UA/AD/II/ES，n=41 逐维配对）

不重跑 LLM——只基于既有系统分与人工金标做均值回归。

用法：
  python scripts/calibration/recompute_reflection_dim_offsets.py \
      [--input deliverables/ornstein_vs_human_full.csv] \
      [--output calib_papers/runs/reflection_dim_offsets.json]
"""
import argparse
import csv
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_INPUT = os.path.join(ROOT, "deliverables", "ornstein_vs_human_full.csv")
DEFAULT_OUTPUT = os.path.join(ROOT, "calib_papers", "runs", "reflection_dim_offsets.json")

# dim -> (human 列名, 系统列名)。默认匹配 ornstein_vs_human_full.csv 的列命名。
_DIM_COLS = {
    "understanding_accuracy": ("human_UA", "orn_UA"),
    "analysis_depth": ("human_AD", "orn_AD"),
    "innovative_insights": ("human_II", "orn_II"),
    "evidence_support": ("human_ES", "orn_ES"),
}


def _mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def _mae(a, b):
    return _mean([abs(x - y) for x, y in zip(a, b)])


def main() -> None:
    ap = argparse.ArgumentParser(description="重算感悟报告 4 维 per-dim 校准偏移")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUTPUT)
    args = ap.parse_args()

    with open(args.input, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"输入为空或列名不匹配: {args.input}")

    offsets: dict[str, float] = {}
    print(f"=== reflection 4 维 per-dim 偏移回归（n={len(rows)}，source={args.input}）===")
    for dim, (hcol, scol) in _DIM_COLS.items():
        if hcol not in rows[0] or scol not in rows[0]:
            raise SystemExit(
                f"缺少列 {hcol}/{scol}；输入需含 41 篇逐维配对列（如 ornstein_vs_human_full.csv）"
            )
        human = [float(r[hcol]) for r in rows]
        system = [float(r[scol]) for r in rows]
        bias = [s - h for s, h in zip(system, human)]
        off = round(_mean(bias), 4)
        offsets[dim] = off
        before = _mae(system, human)
        after = _mae([s - off for s in system], human)
        print(
            f"  {dim:24s}: bias={off:+.4f}  MAE {before:.4f} -> {after:.4f}"
        )

    payload = {
        "offsets": offsets,
        "n_samples": len(rows),
        "source": os.path.relpath(args.input, ROOT).replace("\\", "/"),
        "generated_by": "scripts/calibration/recompute_reflection_dim_offsets.py",
        "method": "offset = mean(system_score - human_score) per dimension",
        "note": (
            "正值表示系统系统性偏高、推理时需减去。II 是主要修正量；UA/AD/ES 的偏移在噪声内。"
            "系统分口径=标题+摘要注入 + R1.5 分级帽 + II 维中位数采样（PAPERFORGE_REFLECTION_II_SAMPLES=3）；"
            "模型/提示词/采样口径稳定后应重跑 scripts/orn_review.py 全量 41 篇并用本脚本复核。"
        ),
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n已落盘: {args.output}")
    print("生效条件: PAPERFORGE_REFLECTION_DIM_CALIBRATION 未设或 =1（默认开启）。")


if __name__ == "__main__":
    main()
