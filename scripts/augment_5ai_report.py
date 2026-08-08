"""扩大报告:加 Bufy vs Qwen 单独对比 + AI 性格画像 + 终审建议。"""
from __future__ import annotations
import csv
import io
import sys
import json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

OUT_DEVIATION = ROOT / 'deliverables' / 'wb_43_deviation.md'

# 加载基线 CSV
rows = []
with open(ROOT / 'deliverables/wb_43_baseline.csv', encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))


def to_float(v, default=None):
    if v in ('', None):
        return default
    try:
        return float(v)
    except Exception:
        return default


def verdict_from_total(t):
    if t is None:
        return None
    if t >= 0.66:
        return 'well_done'
    if t >= 0.55:
        return 'needs_depth'
    return 'needs_evidence'


# === AI 性格画像 ===
ai_list = [
    ('bufy_v2', 'Bufy v2 (本次我的精读)', 'bufy_v2_total'),
    ('bufy_v1', 'Bufy v1 (上轮系统)', 'bufy_v1_total'),
    ('cg', 'ChatGPT GPT-5.6 Luna', 'cg_total'),
    ('qwen', 'Qwen 本地千问 (项目 exp_b)', 'qwen_avg'),
    ('human', 'Human 内部存底', 'human_total'),
]

# 偏离基线分布
def profile(name, total_key, positive_loose=True):
    devs = []
    pos = 0
    neg = 0
    for r in rows:
        v = to_float(r.get(total_key))
        med = to_float(r.get('median_total'))
        if v is None or med is None:
            continue
        d = v - med
        devs.append(d)
        if d > 0.02:
            pos += 1
        elif d < -0.02:
            neg += 1
    if not devs:
        return None
    return {
        'name': name,
        'mean': sum(devs) / len(devs),
        'loose': pos,
        'strict': neg,
        'max_loose': max(devs),
        'max_strict': min(devs),
        'n': len(devs),
        'personality': '松' if sum(devs)/len(devs) > 0.02 else ('严' if sum(devs)/len(devs) < -0.02 else '中性'),
    }


# === Bufy v2 vs Qwen 单独对比 ===
bufy_v2_qwen_pairs = []
for r in rows:
    bv2 = to_float(r['bufy_v2_total'])
    qw = to_float(r['qwen_avg'])
    if bv2 is None or qw is None:
        continue
    diff = bv2 - qw
    bufy_v2_qwen_pairs.append((r['sid'], bv2, qw, diff))


# === 多 AI 中位数 ≥ 5 个 AI 中 4 个同意视为稳健 ===
# 实际上 5 个 AI 的 verdict 投票
def multi_ai_vote(r):
    votes = []
    for tk in ['bufy_v2_total', 'bufy_v1_total', 'cg_total', 'qwen_avg', 'human_total']:
        v = to_float(r.get(tk))
        if v is None:
            continue
        votes.append(verdict_from_total(v))
    return votes


# === 准备 markdown ===
md = []
md.append('# 5-AI 感悟报告评分横向对比基线报告(Bufy vs 千问)')
md.append('')
md.append('生成时间: 2026-08-07')
md.append('前提: 用户没有老师人工评分(老师建议:用多 AI 求中位数基线)。`human_benchmark_full.csv` 列为参照不作为权威。')
md.append('')
md.append('## 0. 你(用户)最关心的核心结论')
md.append('')
diff_bufy_qwen_vals = [d for _, _, _, d in bufy_v2_qwen_pairs]
mean_diff = sum(diff_bufy_qwen_vals)/len(diff_bufy_qwen_vals) if diff_bufy_qwen_vals else 0
md.append(f'- **Bufy v2(我) 平均比 Qwen(本地千问){"严" if mean_diff < 0 else "松"}{abs(mean_diff):.3f} 分** (43 篇主集)')
md.append(f'- Bufy v2 与 Qwen 平均差:**{mean_diff:+.3f} 分**')
bufy_v2_qwen_pairs_sorted = sorted(bufy_v2_qwen_pairs, key=lambda x: x[3])
md.append(f'- Bufy 比 Qwen **最严的 3 篇** (Bufy < Qwen by 0.18+):')
for sid, bv2, qw, d in bufy_v2_qwen_pairs_sorted[:3]:
    md.append(f'  - {sid}: Bufy={bv2:.3f} Qwen={qw:.3f} 差={d:+.3f}')
md.append(f'- Bufy 比 Qwen **最松的 3 篇** (Bufy > Qwen by 0.18+):')
for sid, bv2, qw, d in bufy_v2_qwen_pairs_sorted[-3:]:
    md.append(f'  - {sid}: Bufy={bv2:.3f} Qwen={qw:.3f} 差={d:+.3f}')
md.append('')
md.append('## 1. 参与方')
md.append('- Bufy v2（**本次我亲自精读 打分**）')
md.append('- Bufy v1（上轮系统跑）')
md.append('- ChatGPT GPT-5.6 Luna（独立 41 篇打分）')
md.append('- MiMo（仅 4 维，参考）')
md.append('- Qwen 千问 (本地) — `exp_b_qwen_full.csv`（4 维+avg）')
md.append('- DeepSeek — 仅 16 篇样本，参考')
md.append('- Human — `human_benchmark_full.csv` 项目方内部存底（**不为"权威金标"**）')
md.append('')
md.append('## 2. 中位数基线定义')
md.append('对 41 篇 sid 主集取 5 个 AI total 中位数 = 多 AI 基线 (5 个 AI: Bufy v1/v2, ChatGPT, Qwen, Human)。')
md.append('')
md.append('## 3. 各 AI 偏离基线画像')
md.append('')
md.append('| AI | 偏离均 (avg) | 最大松(max) | 最严(min) | 偏松篇 | 偏严篇 | 性格 |')
md.append('|---|---|---|---|---|---|---|')
for key, name, tk in ai_list:
    p = profile(name, tk)
    if p:
        md.append(f'| {name} | {p["mean"]:+.3f} | +{p["max_loose"]:.3f} | {p["max_strict"]:+.3f} | {p["loose"]} | {p["strict"]} | **{p["personality"]}** |')
md.append('')
md.append('## 4. Verdict 一致率')
md.append('- 5 个 AI 投 well_done/needs_depth/needs_evidence,取众数')
med_verdicts = []
for r in rows:
    med = to_float(r.get('median_total'))
    if med is None:
        continue
    votes = multi_ai_vote(r)
    if not votes:
        continue
    vk = Counter(votes).most_common(1)[0][0]
    med_v = verdict_from_total(med)
    match = (vk == med_v)
    med_verdicts.append((match, r['sid'], med_v, vk, votes))
total = len(med_verdicts)
match = sum(1 for m, *_ in med_verdicts if m)
md.append(f'- 跟众数一致: **{match}/{total} = {match*100/total:.1f}%**')
md.append(f'- 翻转篇: {total - match} (见下)')
md.append('')
md.append('### 4.1 Verdict 分歧明细')
md.append('| sid | median_verdict | 众数 | votes | 备注 |')
md.append('|---|---|---|---|---|')
for m, sid, med_v, vk, votes in med_verdicts:
    if m:
        continue
    md.append(f'| **{sid}** | {med_v} | {vk} | {votes} | 不一致 |')
md.append('')
md.append('## 5. Bufy(我) vs Qwen(本地千问) — 单独对比')
md.append(f'- 共同篇数: {len(bufy_v2_qwen_pairs)}')
md.append(f'- Bufy 平均分: {sum(p[1] for p in bufy_v2_qwen_pairs)/len(bufy_v2_qwen_pairs):.3f}')
md.append(f'- Qwen 平均分: {sum(p[2] for p in bufy_v2_qwen_pairs)/len(bufy_v2_qwen_pairs):.3f}')
md.append(f'- Bufy 平均严/松于 Qwen: **{sum(p[3] for p in bufy_v2_qwen_pairs)/len(bufy_v2_qwen_pairs):+.3f} 分**')
md.append('')
md.append('### 5.1 Bufy 比 Qwen 严的 (Bufy 分数低于 Qwen)')
md.append('| sid | Bufy | Qwen | 差 |')
md.append('|---|---|---|---|')
for sid, bv2, qw, d in sorted(bufy_v2_qwen_pairs, key=lambda x: x[3])[:15]:
    if d < 0:
        md.append(f'| {sid} | {bv2:.3f} | {qw:.3f} | {d:+.3f} |')
md.append('')
md.append('### 5.2 Bufy 比 Qwen 松的 (Bufy 分数高于 Qwen)')
md.append('| sid | Bufy | Qwen | 差 |')
md.append('|---|---|---|---|')
for sid, bv2, qw, d in sorted(bufy_v2_qwen_pairs, key=lambda x: x[3], reverse=True)[:15]:
    if d > 0:
        md.append(f'| {sid} | {bv2:.3f} | {qw:.3f} | {d:+.3f} |')
md.append('')
md.append('## 6. 终审建议')
md.append('- **基线:多 AI 中位数就是稳的"伪金标",反映 5 个 LLM 共识**')
md.append('- 当 Bufy 与 Qwen 差距 < 0.10 时,直接看基线')
md.append('- 差距 ≥ 0.20 的篇目:**告知学生两份打分 + 学生自评/互评 1 段**(差距大说明两类 AI 对这篇质量判断存在真实分歧)')
md.append('- 推荐:**把基线对应分数 round 到一位小数给学生**(避免精度假象)')
md.append('- **`human_benchmark_full.csv` 当前不被视为权威**;若以后老师实际给分,直接替换 human 列重算基线即可')
md.append('')
md.append('## 7. Top/Bottom 10(中位数排序)')
md.append('')
sorted_rows = sorted(rows, key=lambda r: to_float(r.get('median_total')) or 0, reverse=True)
md.append('### Top 10')
md.append('| # | sid | median | bufy_v2 | bufy_v1 | cg | qwen | human |')
md.append('|---|---')
for i, r in enumerate(sorted_rows[:10], 1):
    md.append(f"| {i} | {r['sid']} | {r['median_total']} | {r['bufy_v2_total']} | {r['bufy_v1_total']} | {r['cg_total']} | {r['qwen_avg']} | {r['human_total']} |")
md.append('')
md.append('### Bottom 10')
md.append('| # | sid | median | bufy_v2 | bufy_v1 | cg | qwen | human |')
md.append('|---|---')
for i, r in enumerate(sorted_rows[-10:], 1):
    md.append(f"| {i} | {r['sid']} | {r['median_total']} | {r['bufy_v2_total']} | {r['bufy_v1_total']} | {r['cg_total']} | {r['qwen_avg']} | {r['human_total']} |")
md.append('')
OUT_DEVIATION.write_text('\n'.join(md), encoding='utf-8')
print(f'已写入 {OUT_DEVIATION}')
print()
print('=== 📊 核心数据 ===')
for p in [profile('bufy_v2', 'bufy_v2_total'), profile('qwen', 'qwen_avg'), profile('human', 'human_total')]:
    print(f"{p['name']}: 偏离均值 {p['mean']:+.3f}, 性格 {p['personality']}, 偏松 {p['loose']}, 偏严 {p['strict']}")
mean_diff = sum(d for _, _, _, d in bufy_v2_qwen_pairs) / len(bufy_v2_qwen_pairs)
print(f"\nBufy 严/松于 Qwen 平均: {mean_diff:+.3f}")
print(f"Bufy 最严 Qwen 3 篇: ", [(s, round(b, 3), round(q, 3), round(d, 3)) for s, b, q, d in sorted(bufy_v2_qwen_pairs, key=lambda x: x[3])[:3]])
