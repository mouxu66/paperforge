"""思考模式稳定性分析：同一报告多次评审的分数方差对比。

用法（在仓库根目录）：
    .venv/Scripts/python.exe scripts/calibration/analyze_thinking_stability.py \
        deliverables/thinking_ab_1657.log deliverables/thinking_stability_*.log

读取每行 JSON（run_evidence_once.py 输出），按 (report, thinking) 分组，
输出：n / average 均值±std / 极差 / 各维 std / verdict 分布与一致率。
判据：「把握更稳」= 思考开的 std 与 verdict 熵显著低于思考关。
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

DIMS = ("understanding_accuracy", "analysis_depth", "innovative_insights", "evidence_support")


def load_runs(paths: list[str]) -> list[dict]:
    runs = []
    for p in paths:
        for line in Path(p).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("average") is None:
                continue  # llm_failed 轮次不进方差统计（另行计数）
            runs.append(d)
    return runs


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: analyze_thinking_stability.py <log1> [log2 ...]")
        return 1
    runs = load_runs(sys.argv[1:])
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    failed: dict[tuple[str, str], int] = defaultdict(int)
    total: dict[tuple[str, str], int] = defaultdict(int)
    for d in runs:
        key = (d["report"], d["thinking"])
        total[key] += 1
        if d.get("average") is None or d.get("llm_failed"):
            failed[key] += 1
        else:
            groups[key].append(d)

    reports = sorted({r for r, _ in groups})
    print(f"{'报告':<10} {'think':<6} {'n':<3} {'fail':<4} "
          f"{'avg mean':<9} {'avg std':<8} {'range':<13} "
          f"{'各维std(UA/AD/II/ES)':<24} {'verdict 分布':<22} {'一致率'}")
    for rep in reports:
        for th in ("0", "1"):
            key = (rep, th)
            g = groups.get(key, [])
            if not g:
                print(f"{rep[-4:]:<10} {th:<6} {total[key]:<3} {failed[key]:<4} (无有效轮次)")
                continue
            avgs = [d["average"] for d in g]
            dim_stds = []
            for dim in DIMS:
                vals = [d["scores"].get(dim) for d in g if d["scores"].get(dim) is not None]
                dim_stds.append(statistics.pstdev(vals) if len(vals) > 1 else 0.0)
            verd = [d["verdict"] for d in g]
            vcount: dict[str, int] = defaultdict(int)
            for v in verd:
                vcount[v] += 1
            top_v, top_n = max(vcount.items(), key=lambda kv: kv[1])
            consist = top_n / len(verd)
            rng = f"{min(avgs):.3f}-{max(avgs):.3f}"
            vdist = ",".join(f"{k}:{v}" for k, v in sorted(vcount.items()))
            print(
                f"{rep[-4:]:<10} {th:<6} {len(g):<3} {failed[key]:<4} "
                f"{statistics.mean(avgs):<9.4f} {statistics.pstdev(avgs):<8.4f} {rng:<13} "
                f"{'/'.join(f'{s:.3f}' for s in dim_stds):<24} {vdist:<22} {consist:.0%}"
            )
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
