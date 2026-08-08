"""生成教师评分册 — 多AI中位数作推荐分 + Bufy vs Qwen 偏差 + 等级 + 评语。"""
from __future__ import annotations
import csv, io, sys
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent

# 读基线
baseline = {}
with open(ROOT / 'deliverables/wb_43_baseline.csv', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        baseline[r['sid']] = r

# 读 Bufy v2 (评语+姓名+paper)
bufy = {}
with open(ROOT / 'deliverables/bufy_scores_43.csv', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        bufy[r['sid']] = r

def to_f(v, default=None):
    try: return float(v)
    except: return default

def grade(median_total):
    """等级映射"""
    if median_total is None: return '?'
    mt = float(median_total)
    if mt >= 0.75: return 'A'
    if mt >= 0.65: return 'B'
    if mt >= 0.55: return 'C'
    return 'D'

def teacher_note(sid, median_total, bufy_v2_total, qwen_avg, bufy_note):
    """教师视角简短评语"""
    m = to_f(median_total)
    b2 = to_f(bufy_v2_total)
    qw = to_f(qwen_avg)
    parts = []
    if m and m >= 0.75:
        parts.append("优秀")
    elif m and m >= 0.65:
        parts.append("良好")
    elif m:
        parts.append("需改进")
    else:
        parts.append("待评")
    # Bufy vs Qwen 偏差
    if b2 and qw and abs(b2 - qw) > 0.15:
        parts.append(f"AI评分分歧大({b2:.2f}vs{qw:.2f})")
    # 从 Bufy note 里抓一句
    if bufy_note:
        note = bufy_note[:60].replace('\n',' ').replace(',',' ')
        parts.append(note)
    return '; '.join(parts[:2])

# 合并
OUT_CSV = ROOT / 'deliverables/teacher_scorebook.csv'
rows = []
for sid in sorted(bufy.keys(), key=lambda s: (s == 'unknown', s)):
    br = baseline.get(sid) or {}
    bu = bufy.get(sid) or {}
    m_total = to_f(br.get('median_total'))
    b2_total = to_f(br.get('bufy_v2_total'))
    qw_total = to_f(br.get('qwen_avg'))
    dev_bq = round(b2_total - qw_total, 3) if b2_total and qw_total else ''

    rows.append({
        '序号': str(len(rows)+1),
        '学号': sid,
        '姓名': bu.get('name', ''),
        '论文标题': (bu.get('paper','') or '')[:70],
        '段数': bu.get('section_keys_count', ''),
        '字数': bu.get('chars', ''),
        '推荐分(5-AI中位数)': f"{m_total:.3f}" if m_total else '',
        'Bufy分': f"{b2_total:.3f}" if b2_total else '',
        'Qwen分': f"{qw_total:.3f}" if qw_total else '',
        'BufyvsQwen': f"{dev_bq:+.3f}" if dev_bq != '' else '',
        '推荐等级': grade(m_total) if m_total else '',
        '教师评语': teacher_note(sid, m_total, b2_total, qw_total, bu.get('bufy_note','')),
    })

# 写 CSV
with open(OUT_CSV, 'w', encoding='utf-8-sig', newline='') as f:
    fields = list(rows[0].keys())
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
print(f'✅ 评分册 CSV: {OUT_CSV}')

# 打印终端表格(按推荐分排序)
print()
print('='*110)
print('                   📋 43 篇感悟报告 — 教师评分册 (推荐分=5-AI中位数)')
print('='*110)
sorted_rows = sorted(rows, key=lambda r: to_f(r.get('推荐分(5-AI中位数)','0'), 0), reverse=True)
print(f"{'#':<3} {'学号':<14} {'姓名':<12} {'论文':<30} {'段':<2} {'字':<5} {'推荐分':<7} {'等级':<3} {'Bufy':<7} {'Qwen':<7} {'BvsQ':<7} 评语")
print('-'*110)
for r in sorted_rows:
    print(f"{r['序号']:<3} {r['学号']:<14} {r['姓名'][:12]:<12} {r['论文标题'][:30]:<30} {r['段数']:<2} {r['字数']:<5} {r['推荐分(5-AI中位数)']:<7} {r['推荐等级']:<3} {r['Bufy分']:<7} {r['Qwen分']:<7} {r['BufyvsQwen']:<7} {r['教师评语'][:50]}")
print('-'*110)

# 统计
from collections import Counter
grades = Counter(r['推荐等级'] for r in rows if r['推荐等级'])
avg_score = sum(to_f(r.get('推荐分(5-AI中位数)','0')) for r in rows if to_f(r.get('推荐分(5-AI中位数)','0')) is not None) / max(len(rows),1)
print(f"\n📊 统计: 平均推荐分={avg_score:.3f} | 等级分布: {dict(grades)}")
print(f"    A(≥0.75)={grades.get('A',0)}篇 B(0.65~0.74)={grades.get('B',0)}篇 C(0.55~0.64)={grades.get('C',0)}篇 D(<0.55)={grades.get('D',0)}篇")
bufy_vs_qwen_vals = [to_f(r.get('BufyvsQwen','')) for r in rows if to_f(r.get('BufyvsQwen','')) is not None]
mean_bq = sum(bufy_vs_qwen_vals) / max(len(bufy_vs_qwen_vals), 1)
print(f"    Bufy 平均严于 Qwen: {mean_bq:+.3f}")
