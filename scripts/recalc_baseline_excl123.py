# -*- coding: utf-8 -*-
"""剔除 123 号 Qwen=0.00 异常，重算 5-AI 总分中位数基线与各 AI 偏离。"""
import csv, statistics, shutil, json
from pathlib import Path

DEL = Path(r'C:\Users\<user>\WorkBuddy\2026-06-13-21-30-08\paperforge\deliverables')
SRC = {
    'bufy_v2': (DEL/'bufy_scores_43.csv', 'sid', 'bufy_average'),
    'bufy_v1': (DEL/'buffy_full_41.csv', 'sid', 'total'),
    'cg':      (DEL/'chatgpt_gpt-5.6-luna_independent_41.csv', 'sid', 'total'),
    'qwen':    (DEL/'exp_b_qwen_full.csv', 'sid', 'avg'),
    'human':   (DEL/'human_benchmark_full.csv', 'sid', 'total'),
}
DEEP = (DEL/'deepseek_my_16samples.csv', 'sid', 'total')

def load(path, sid_col, total_col):
    out = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            try:
                out[r[sid_col].strip()] = float(r[total_col])
            except (ValueError, KeyError):
                pass
    return out

totals = {name: load(*spec) for name, spec in SRC.items()}
deep = load(*DEEP)

BASE = DEL/'wb_43_baseline.csv'
# 备份原文件
shutil.copy(BASE, DEL/'wb_43_baseline_pre123.csv')

# 123 号 Bufy_v2 真实 4 维分（Qwen=0 是异常，剔除后 median_4dim 应回到 Bufy 原值）
FIX_123_4DIM = {'median_ua':0.8,'median_ad':0.65,'median_ii':0.55,'median_es':0.75}

# 读原 baseline 保留 median_4dim 原值
with open(BASE, newline='', encoding='utf-8-sig') as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    orig_rows = list(reader)

QWEN_ANOMALY = 0.01  # Qwen total <= 此值视为异常缺失

out_rows = []
per_ai_dev = {k: [] for k in ['bufy_v2','bufy_v1','cg','qwen','human']}
old_per_ai_dev = {k: [] for k in ['bufy_v2','bufy_v1','cg','qwen','human']}

for r in orig_rows:
    sid = r['sid'].strip()
    row = {k: r[k] for k in fieldnames}

    # 原 dev（剔除前）用于对比
    for k in per_ai_dev:
        v = r.get(f'dev_{k}_vs_med','')
        if v not in ('', None):
            try: old_per_ai_dev[k].append(float(v))
            except ValueError: pass

    if sid == '' or not totals['bufy_v2'].get(sid) and sid != 'unknown':
        # 仍可能只有部分 AI 有值
        pass

    # 收集 5-AI total，剔除 Qwen 异常
    col_of = {'bufy_v2':'bufy_v2_total','bufy_v1':'bufy_v1_total',
              'cg':'cg_total','qwen':'qwen_avg','human':'human_total'}
    vals = []
    present = {}
    for k in ['bufy_v2','bufy_v1','cg','qwen','human']:
        v = totals[k].get(sid)
        if v is None:
            continue
        if k == 'qwen' and v <= QWEN_ANOMALY:
            # 异常：不参与中位数、不算该篇 qwen dev
            row[col_of[k]] = f'{v:.4f}'
            continue
        present[k] = v
        vals.append(v)

    if vals:
        med = statistics.median(vals)
    else:
        med = None

    # 重算 median_total
    if med is not None:
        row['median_total'] = f'{med:.4f}'
        # dev 列
        for k in ['bufy_v2','bufy_v1','cg','qwen','human']:
            if k in present:
                dev = present[k] - med
                row[f'dev_{k}_vs_med'] = f'{dev:.4f}'
                per_ai_dev[k].append(dev)
            else:
                row[f'dev_{k}_vs_med'] = ''
    else:
        row['median_total'] = ''
        for k in ['bufy_v2','bufy_v1','cg','qwen','human']:
            row[f'dev_{k}_vs_med'] = ''

    # 123 号 median_4dim 修正
    if sid == '999900000020':
        for dim, val in FIX_123_4DIM.items():
            row[dim] = f'{val:.4f}'

    # deepseek_total 列保留原值
    dv = deep.get(sid)
    row['deepseek_total'] = '' if dv is None else f'{dv:.4f}'

    out_rows.append(row)

# 写回 baseline
with open(BASE, 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(out_rows)

# 偏离均值对比
def mean(xs): return sum(xs)/len(xs) if xs else 0.0
print("各 AI 偏离 5-AI 中位数基线（剔除 123 Qwen=0 前后对比）")
print(f"{'AI':<10}{'剔除前':>10}{'剔除后':>10}")
labels = {'bufy_v2':'Bufy v2','bufy_v1':'Bufy v1','cg':'ChatGPT','qwen':'Qwen','human':'Human'}
for k in ['bufy_v2','bufy_v1','cg','qwen','human']:
    print(f"{labels[k]:<10}{mean(old_per_ai_dev[k]):>10.4f}{mean(per_ai_dev[k]):>10.4f}")

summary = {
    'old': {k: round(mean(old_per_ai_dev[k]),4) for k in old_per_ai_dev},
    'new': {k: round(mean(per_ai_dev[k]),4) for k in per_ai_dev},
}
(DEL/'_recalc_baseline_summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print("\n已更新 wb_43_baseline.csv（原文件备份为 wb_43_baseline_pre123.csv）")
print("已写出 _recalc_baseline_summary.json")
