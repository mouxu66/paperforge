# -*- coding: utf-8 -*-
"""基于更新后的 baseline 输出 Top/Bottom 10 表（markdown）。"""
import csv
from pathlib import Path

DEL = Path(r'C:/Users/<user>\WorkBuddy\2026-06-13-21-30-08\paperforge\deliverables')
with open(DEL/'wb_43_baseline.csv', newline='', encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))

def f2(x):
    try: return f'{float(x):.4f}'
    except (ValueError, TypeError): return ''

rows_sorted = sorted(rows, key=lambda r: float(r['median_total']) if r['median_total'] else -1)

print("=== TOP 10 ===")
print("| # | sid | median | bufy_v2 | bufy_v1 | cg | qwen | human |")
print("|---|---|---|---|---|---|---|---|")
for i, r in enumerate(rows_sorted[::-1][:10], 1):
    print(f"| {i} | {r['sid']} | {f2(r['median_total'])} | {f2(r['bufy_v2_total'])} | {f2(r['bufy_v1_total'])} | {f2(r['cg_total'])} | {f2(r['qwen_avg'])} | {f2(r['human_total'])} |")

print("\n=== BOTTOM 10 ===")
print("| # | sid | median | bufy_v2 | bufy_v1 | cg | qwen | human |")
print("|---|---|---|---|---|---|---|---|")
for i, r in enumerate(rows_sorted[:10], 1):
    print(f"| {i} | {r['sid']} | {f2(r['median_total'])} | {f2(r['bufy_v2_total'])} | {f2(r['bufy_v1_total'])} | {f2(r['cg_total'])} | {f2(r['qwen_avg'])} | {f2(r['human_total'])} |")
