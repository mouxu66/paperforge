# -*- coding: utf-8 -*-
"""读已更新的 wb_43_baseline.csv，输出各 AI 偏离画像（用于修订报告表）。"""
import csv, statistics
from pathlib import Path

DEL = Path(r'C:\Users\<user>\WorkBuddy\2026-06-13-21-30-08\paperforge\deliverables')
with open(DEL/'wb_43_baseline.csv', newline='', encoding='utf-8-sig') as f:
    rows = [r for r in csv.DictReader(f) if r['sid'].strip() and r['sid'].strip() != 'unknown']

def profile(dev_col):
    devs = []
    for r in rows:
        v = r.get(dev_col, '')
        if v not in ('', None):
            try: devs.append(float(v))
            except ValueError: pass
    if not devs:
        return None
    return {
        'mean': statistics.mean(devs),
        'max': max(devs), 'min': min(devs),
        'loose': sum(1 for d in devs if d > 0.0001),
        'strict': sum(1 for d in devs if d < -0.0001),
        'n': len(devs),
    }

for k, col in [('Bufy v2','dev_bufy_v2_vs_med'),('Bufy v1','dev_bufy_v1_vs_med'),
               ('ChatGPT','dev_cg_vs_med'),('Qwen','dev_qwen_vs_med'),
               ('Human','dev_human_vs_med')]:
    p = profile(col)
    if p:
        print(f"{k:<10} mean={p['mean']:+.4f} max={p['max']:+.4f} min={p['min']:+.4f} loose={p['loose']} strict={p['strict']} n={p['n']}")
