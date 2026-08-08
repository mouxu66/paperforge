# -*- coding: utf-8 -*-
"""分析各 AI 对同一篇感悟报告的分歧（篇级 + 维度级）。"""
import csv, statistics, json
from pathlib import Path

DEL = Path(r'C:/Users/<user>\WorkBuddy\2026-06-13-21-30-08\paperforge\deliverables')

# ---------- 1. 加载各源文件的 total ----------
def load_total_csv(path, sid_col='sid', total_col='total'):
    out = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            try:
                v = float(r[total_col])
            except (ValueError, KeyError):
                continue
            out[r[sid_col].strip()] = v
    return out

bufy_v2 = load_total_csv(DEL/'bufy_scores_43.csv', sid_col='sid', total_col='bufy_average')
bufy_v1 = load_total_csv(DEL/'buffy_full_41.csv')
cg      = load_total_csv(DEL/'chatgpt_gpt-5.6-luna_independent_41.csv')
qwen    = load_total_csv(DEL/'exp_b_qwen_full.csv', total_col='avg')
human   = load_total_csv(DEL/'human_benchmark_full.csv')
deepseek= load_total_csv(DEL/'deepseek_my_16samples.csv', total_col='total')

# ---------- 2. 篇级分歧：每篇收集所有可用 AI 的 total ----------
sources = {
    'Bufy_v2': bufy_v2, 'Bufy_v1': bufy_v1, 'ChatGPT': cg,
    'Qwen': qwen, 'Human': human, 'DeepSeek': deepseek,
}

all_sids = set()
for s in sources.values():
    all_sids |= set(s.keys())

rows = []
for sid in sorted(all_sids):
    vals = {name: src.get(sid) for name, src in sources.items() if sid in src}
    if len(vals) < 2:
        continue
    vlist = list(vals.values())
    rng = max(vlist) - min(vlist)
    sd = statistics.pstdev(vlist) if len(vlist) > 1 else 0.0
    mean = statistics.mean(vlist)
    # 相对极差（极差 / 均值，避免高分篇天然数值大）
    rel_rng = rng / mean if mean > 0 else 0.0
    rows.append({
        'sid': sid,
        'n_ai': len(vals),
        'min': min(vlist), 'max': max(vlist),
        'range': rng, 'rel_range': rel_rng, 'std': sd,
        'vals': vals,
    })

# 按绝对极差排序 Top 12
top_range = sorted(rows, key=lambda r: r['range'], reverse=True)[:12]
# 按相对极差排序 Top 12（控制篇幅影响）
top_rel = sorted(rows, key=lambda r: r['rel_range'], reverse=True)[:12]

# ---------- 3. 维度级分歧：u/a/i/e 跨 mimo / qwen / bufy_v2 ----------
def load_4dim(path, mapping):
    out = {}
    with open(path, newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            sid = r['sid'].strip()
            d = {}
            for dim, col in mapping.items():
                try:
                    d[dim] = float(r[col])
                except (ValueError, KeyError):
                    d[dim] = None
            out[sid] = d
    return out

mimo = load_4dim(DEL/'mimo_4dim_truncated.csv',
                 {'u':'mimo_u','a':'mimo_a','i':'mimo_i','e':'mimo_e'})
qwen4 = load_4dim(DEL/'exp_b_qwen_full.csv',
                  {'u':'u','a':'a','i':'i','e':'e'})
bufy4 = load_4dim(DEL/'bufy_scores_43.csv',
                  {'u':'ua','a':'ad','i':'ii','e':'es'})

# 对每篇、每维，收集 3 个 AI 的分，算极差
dim_rows = []
for sid in sorted(set(mimo) & set(qwen4) & set(bufy4)):
    per_dim = {}
    for dim in ['u','a','i','e']:
        vs = [mimo[sid][dim], qwen4[sid][dim], bufy4[sid][dim]]
        vs = [x for x in vs if x is not None]
        if len(vs) >= 2:
            per_dim[dim] = (min(vs), max(vs), max(vs)-min(vs))
    if per_dim:
        # 这篇在 4 维上的最大极差
        max_dr = max(d[2] for d in per_dim.values())
        dim_rows.append({'sid': sid, 'per_dim': per_dim, 'max_dim_range': max_dr})

# 维度名 -> 全局平均极差
dim_avg = {'u':[], 'a':[], 'i':[], 'e':[]}
for dr in dim_rows:
    for dim, (mn, mx, rg) in dr['per_dim'].items():
        dim_avg[dim].append(rg)
dim_mean = {d: (statistics.mean(v) if v else 0.0) for d, v in dim_avg.items()}

# ---------- 4. 输出 ----------
print("="*70)
print("一、篇级分歧（同一篇，多个 AI 总分差多少）")
print(f"参与统计的篇数（≥2 个 AI 有分）：{len(rows)}")
print(f"平均绝对极差：{statistics.mean([r['range'] for r in rows]):.3f}")
print(f"平均相对极差：{statistics.mean([r['rel_range'] for r in rows]):.1%}")
print()
print("-- 绝对极差最大的 12 篇 --")
print(f"{'sid':<16}{'n':>3}{'min':>7}{'max':>7}{'range':>8}{'std':>7}  vals")
for r in top_range:
    vs_str = " ".join(f"{k}={v:.2f}" for k,v in r['vals'].items())
    print(f"{r['sid']:<16}{r['n_ai']:>3}{r['min']:>7.3f}{r['max']:>7.3f}{r['range']:>8.3f}{r['std']:>7.3f}  {vs_str}")

print()
print("="*70)
print("二、维度级分歧（u=理解/a=深度/i=洞见/e=证据，跨 MiMo/Qwen/Bufy_v2）")
print(f"可比篇数：{len(dim_rows)}")
print("各维度平均极差：")
for d, m in dim_mean.items():
    print(f"  {d}: {m:.3f}")
worst_dim = max(dim_mean, key=dim_mean.get)
print(f"→ 分歧最大的维度是「{worst_dim}」")
print()
print("-- 维度极差最大的 8 篇 --")
for dr in sorted(dim_rows, key=lambda x: x['max_dim_range'], reverse=True)[:8]:
    parts = " ".join(f"{d}:{mn:.2f}-{mx:.2f}" for d,(mn,mx,rg) in dr['per_dim'].items())
    print(f"{dr['sid']:<16} maxΔ={dr['max_dim_range']:.2f}  {parts}")

# 存 JSON 供后续报告使用
out = {
    'n_rows': len(rows),
    'mean_abs_range': statistics.mean([r['range'] for r in rows]),
    'mean_rel_range': statistics.mean([r['rel_range'] for r in rows]),
    'top_range': [{k:v for k,v in r.items() if k!='vals'} | {'vals':{kk:round(vv,3) for kk,vv in r['vals'].items()}} for r in top_range],
    'top_rel': [{k:v for k,v in r.items() if k!='vals'} | {'vals':{kk:round(vv,3) for kk,vv in r['vals'].items()}} for r in top_rel],
    'dim_n': len(dim_rows),
    'dim_mean': dim_mean,
    'worst_dim': worst_dim,
}
(DEL/'_ai_disagreement.json').write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
print("\n已写出 deliverables/_ai_disagreement.json")
