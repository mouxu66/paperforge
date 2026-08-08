"""合并 5 个 AI 评分 (ChatGPT + Qwen + MiMo + Bufy v1 + Bufy v2=本次)
   + human_benchmark_full 做参考。
   输出:
   - 交叉矩阵 wb_43_compare.csv (43 篇 × 7 个评分方)
   - 中位数基线 wb_43_baseline.csv
   - 每 AI 偏离基线分析 wb_43_deviation.md
"""
from __future__ import annotations
import csv
import json
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

OUT_COMPARE = ROOT / 'deliverables' / 'wb_43_compare.csv'
OUT_BASELINE = ROOT / 'deliverables' / 'wb_43_baseline.csv'
OUT_DEVIATION = ROOT / 'deliverables' / 'wb_43_deviation.md'

# 1) 加载已抽好的 Bufy v2 (本次我自己)
bufy_v2 = {}
with open(ROOT / 'deliverables/busy_43_reports.json' if False else ROOT / 'deliverables/bufy_43_reports.json', encoding='utf-8') as f:
    pass
# 保持简洁，重写
def load_csv(path, key='sid'):
    with open(path, encoding='utf-8-sig') as f:
        return {r[key]: r for r in csv.DictReader(f)}

# bufy v2
bufy_v2_raw = load_csv(ROOT / 'deliverables/bufy_scores_43.csv')

# human (可能有 user 内置标底，可作参照)
human = load_csv(ROOT / 'deliverables/human_benchmark_full.csv')

# chatgpt
chatgpt = load_csv(ROOT / 'deliverables/chatgpt_gpt-5.6-luna_independent_41.csv')

# mimo 4dim
mimo = load_csv(ROOT / 'deliverables/mimo_4dim_truncated.csv')

# qwen 4dim (exp_b_qwen_full)
qwen = load_csv(ROOT / 'deliverables/exp_b_qwen_full.csv')

# bufy v1 (上轮系统跑)
bufy_v1 = load_csv(ROOT / 'deliverables/buffy_full_41.csv')

# deepseek 仅 16 篇
deepseek = load_csv(ROOT / 'deliverables/deepseek_my_16samples.csv')


def to_float(v, default=None):
    try:
        return float(v)
    except Exception:
        return default


def get(d, key, default=None):
    return d.get(key) if d else default


# 6 维 (ua/ad/ii/es/fid/cov) + total + verdict。
# 但很多 AI 只给 4 维(u/a/i/e)，需要把 fid/cov 部分基于 t/a/i/e 启发式或留空。
def row_for_sid(sid):
    bh = bufy_v2_raw.get(sid) or {}            # bufy v2 (本次我)
    h_v1 = bufy_v1.get(sid) or {}               # bufy v1 (上轮系统)
    hm = human.get(sid) or {}                   # human
    cg = chatgpt.get(sid) or {}                # chatgpt
    mi = mimo.get(sid) or {}                   # mimo 4dim
    qw = qwen.get(sid) or {}                    # qwen 4dim
    ds = deepseek.get(sid) or {}                # deepseek (仅 total)

    # 还原字段
    row = {
        'sid': sid,
        'bufy_v2_ua': to_float(bh.get('ua')),
        'bufy_v2_ad': to_float(bh.get('ad')),
        'bufy_v2_ii': to_float(bh.get('ii')),
        'bufy_v2_es': to_float(bh.get('es')),
        'bufy_v2_fid': to_float(bh.get('fid')),
        'bufy_v2_cov': to_float(bh.get('cov')),
        'bufy_v2_total': to_float(bh.get('bufy_average')),
        'bufy_v2_verdict': bh.get('bufy_verdict'),

        'bufy_v1_ua': to_float(h_v1.get('understanding_accuracy')),
        'bufy_v1_ad': to_float(h_v1.get('analysis_depth')),
        'bufy_v1_ii': to_float(h_v1.get('innovative_insights')),
        'bufy_v1_es': to_float(h_v1.get('evidence_support')),
        'bufy_v1_fid': to_float(h_v1.get('fidelity')),
        'bufy_v1_cov': to_float(h_v1.get('coverage')),
        'bufy_v1_total': to_float(h_v1.get('total')),

        'human_ua': to_float(hm.get('understanding_accuracy')),
        'human_ad': to_float(hm.get('analysis_depth')),
        'human_ii': to_float(hm.get('innovative_insights')),
        'human_es': to_float(hm.get('evidence_support')),
        'human_fid': to_float(hm.get('fidelity')),
        'human_cov': to_float(hm.get('coverage')),
        'human_total': to_float(hm.get('total')),

        'cg_ua': to_float(cg.get('understanding_accuracy')),
        'cg_ad': to_float(cg.get('analysis_depth')),
        'cg_ii': to_float(cg.get('innovative_insights')),
        'cg_es': to_float(cg.get('evidence_support')),
        'cg_fid': to_float(cg.get('fidelity')),
        'cg_cov': to_float(cg.get('coverage')),
        'cg_total': to_float(cg.get('total')),
        'cg_notes': cg.get('notes', ''),

        'mimo_u': to_float(mi.get('mimo_u')),
        'mimo_a': to_float(mi.get('mimo_a')),
        'mimo_i': to_float(mi.get('mimo_i')),
        'mimo_e': to_float(mi.get('mimo_e')),

        'qwen_u': to_float(qw.get('u')),
        'qwen_a': to_float(qw.get('a')),
        'qwen_i': to_float(qw.get('i')),
        'qwen_e': to_float(qw.get('e')),
        'qwen_avg': to_float(qw.get('avg')),
        'qwen_verdict': qw.get('verdict'),

        'deepseek_total': to_float(ds.get('total')),
    }
    return row


def compute_6dim(row):
    """用 (u/a/i/e) + (fid/cov=已知 AI 给出) 算 6 维 total (按权重)。
    项目权重: ua 0.05 + ad 0.15 + ii 0.35 + es 0.05 + fid 0.05 + cov 0.35 = 1.0
    """
    W = {'ua': 0.05, 'ad': 0.15, 'ii': 0.35, 'es': 0.05, 'fid': 0.05, 'cov': 0.35}
    return W


def main():
    # 主集 = bufy_v2 出现的全部 sid (43 篇) — 包含 unknown 和 999900000020
    all_sids = sorted(bufy_v2_raw.keys(), key=lambda s: (s == 'unknown', s))

    # 合并矩阵
    rows = [row_for_sid(s) for s in all_sids]

    # 计算 4 维 (u/a/i/e) 的「多 AI 中位数」基线 (仅 5 个 AI 都有的篇)
    W6 = {'ua': 0.05, 'ad': 0.15, 'ii': 0.35, 'es': 0.05, 'fid': 0.05, 'cov': 0.35}
    keys_4d_ua = ['bufy_v2_ua', 'bufy_v1_ua', 'cg_ua', 'mimo_u', 'qwen_u']
    keys_4d_ad = ['bufy_v2_ad', 'bufy_v1_ad', 'cg_ad', 'mimo_a', 'qwen_a']
    keys_4d_ii = ['bufy_v2_ii', 'bufy_v1_ii', 'cg_ii', 'mimo_i', 'qwen_i']
    keys_4d_es = ['bufy_v2_es', 'bufy_v1_es', 'cg_es', 'mimo_e', 'qwen_e']
    keys_6d_tot = ['bufy_v2_total', 'bufy_v1_total', 'cg_total', 'qwen_avg', 'human_total']
    # (deepseek 只有 total,放在展示层不用)

    def median(vals):
        s = sorted(v for v in vals if v is not None)
        if not s:
            return None
        n = len(s)
        return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2

    # 写到 compare
    base_fields = list(rows[0].keys())
    with open(OUT_COMPARE, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=base_fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print(f'compare matrix: {OUT_COMPARE} ({len(rows)} rows)')

    # 计算基线 + 偏离
    baseline_rows = []
    for r in rows:
        ua_m = median([r.get(k) for k in keys_4d_ua])
        ad_m = median([r.get(k) for k in keys_4d_ad])
        ii_m = median([r.get(k) for k in keys_4d_ii])
        es_m = median([r.get(k) for k in keys_4d_es])
        # 对有 6 维的 (人 + bufy_v1 + bufy_v2 + cg)，还需要 fid/cov 中位
        # 此基线仅做 4 维加权平均 (权 重等于 4 维硬集 u=0.05+a=0.15+i=0.35+e=0.05=0.60 比例)
        # 然后 total_baseline_4d = 0.6 * u_adjusted? 不过更简单 - 用项目给的 total (bufy_v2/v1/cg/qwen_avg/human_total 之中位数)
        total_m = median([r.get(k) for k in keys_6d_tot])

        bv2 = r['bufy_v2_total']
        bv1 = r['bufy_v1_total']
        hm = r['human_total']
        cg = r['cg_total']
        qa = r['qwen_avg']

        bl_row = {
            'sid': r['sid'],
            'median_ua': round(ua_m, 3) if ua_m is not None else '',
            'median_ad': round(ad_m, 3) if ad_m is not None else '',
            'median_ii': round(ii_m, 3) if ii_m is not None else '',
            'median_es': round(es_m, 3) if es_m is not None else '',
            'median_total': round(total_m, 4) if total_m is not None else '',
            'bufy_v2_total': round(bv2, 4) if bv2 is not None else '',
            'bufy_v1_total': round(bv1, 4) if bv1 is not None else '',
            'cg_total': round(cg, 4) if cg is not None else '',
            'qwen_avg': round(qa, 4) if qa is not None else '',
            'human_total': round(hm, 4) if hm is not None else '',
            'deepseek_total': round(r['deepseek_total'], 4) if r['deepseek_total'] is not None else '',
        }
        # 单维偏离
        def dev(a, b):
            if a is None or b is None:
                return ''
            return round(a - b, 3)
        bl_row.update({
            'dev_bufy_v2_vs_med': dev(bv2, total_m),
            'dev_bufy_v1_vs_med': dev(bv1, total_m),
            'dev_cg_vs_med': dev(cg, total_m),
            'dev_qwen_vs_med': dev(qa, total_m),
            'dev_human_vs_med': dev(hm, total_m),
        })

        baseline_rows.append(bl_row)

    base_fields = list(baseline_rows[0].keys())
    with open(OUT_BASELINE, 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=base_fields)
        w.writeheader()
        for r in baseline_rows:
            w.writerow(r)
    print(f'baseline: {OUT_BASELINE}')

    # Markdown 偏离分析
    valid = [r for r in baseline_rows if r['median_total'] != '']
    devs = {
        'bufy_v2': [abs(r['dev_bufy_v2_vs_med']) for r in valid if r['dev_bufy_v2_vs_med'] != ''],
        'bufy_v1': [abs(r['dev_bufy_v1_vs_med']) for r in valid if r['dev_bufy_v1_vs_med'] != ''],
        'cg': [abs(r['dev_cg_vs_med']) for r in valid if r['dev_cg_vs_med'] != ''],
        'qwen': [abs(r['dev_qwen_vs_med']) for r in valid if r['dev_qwen_vs_med'] != ''],
        'human': [abs(r['dev_human_vs_med']) for r in valid if r['dev_human_vs_med'] != ''],
    }

    def stat(name, vals):
        if not vals:
            return f'  {name}: (无数据)'
        avg = sum(vals) / len(vals)
        var = sum((v - avg) ** 2 for v in vals) / len(vals)
        return (f'  {name}: n={len(vals)} '
                f'|avg偏离={avg:.3f}|max偏离={max(vals):.3f}|[min={min(vals):.3f},max={max(vals):.3f}]')

    # verdict 一致率:用 median_total 转 verdict (.65=well_done, .55~.65=needs_depth, < .55=needs_evidence)
    def verdict_from_total(t):
        if t is None:
            return None
        if t >= 0.66:
            return 'well_done'
        if t >= 0.55:
            if t < 0.60:
                return 'needs_evidence'
            return 'needs_depth'
        return 'needs_evidence';

    cnt_verdict_match = 0
    total_verdict = 0
    verdict_diff = []
    for r in valid:
        med_v = verdict_from_total(r['median_total'])
        votes = {}
        for k, total_key in [('bufy_v2', 'bufy_v2_total'),
                              ('bufy_v1', 'bufy_v1_total'),
                              ('cg', 'cg_total'),
                              ('qwen', 'qwen_avg'),
                              ('human', 'human_total')]:
            v = r.get(total_key)
            if v == '' or v is None:
                continue
            tk = v
            if not isinstance(tk, (int, float)):
                try:
                    tk = float(tk)
                except Exception:
                    continue
            vs = verdict_from_total(tk)
            if vs is None:
                continue
            votes[k] = vs
        if med_v is None:
            continue
        # 简单：每个 AI 是否与中位数一致
        all_v = list(votes.values())
        # 取众数
        from collections import Counter
        vk = Counter(all_v).most_common(1)[0][0]
        if vk == med_v:
            cnt_verdict_match += 1
        else:
            verdict_diff.append((r['sid'], med_v, votes))
        total_verdict += 1

    # 写入 Markdown
    md = ["# 五 AI 横向对比基线报告",
          "",
          "## 1. 参与方",
          "- Bufy v2（**本次我亲自精读 打分**）",
          "- Bufy v1（上轮系统 Buffy 跑）",
          "- ChatGPT GPT-5.6 Luna（独立 41 篇打分）",
          "- MiMo（4 维）",
          "- Qwen 千问 (本地) — exp_b_qwen_full（4 维+avg）",
          "- DeepSeek — 仅 16 篇样本",
          "- Human (`human_benchmark_full.csv`) — 项目方内部存底人工评分，**用户声明当前老师未给人工评分**，故下方仅作\"参照金标\"而非\"权威\"",
          "",
          "## 2. 中位数基线 (41 篇主集 sid)",
          "对 sid ∈ {999900000001…999900000043} 取 5 个 AI total 中位数 = 多 AI 基线。",
          "",
          "## 3. 各 AI 偏离基线 (绝对值)",
          ""]
    for k in ['bufy_v2', 'bufy_v1', 'cg', 'qwen', 'human']:
        md.append(stat(k, devs[k]))
    md.append('')
    md.append(f"## 4. Verdict 跟众数一致性 (基于 total → verdict)")
    if total_verdict:
        rate = cnt_verdict_match * 100 / total_verdict
        md.append(f"- 一致率：**{cnt_verdict_match}/{total_verdict} = {rate:.1f}%**")
        md.append(f"- 翻转/不一致篇数:**{len(verdict_diff)}** （含 sid, median_verdict, 投票分布）")
        md.append('')
    md.append('## 5. Top/Bottom 10 篇（中位数）')
    sorted_b = sorted(valid, key=lambda r: r['median_total'], reverse=True)
    md.append('')
    md.append("### Top 10")
    md.append('| # | sid | median_total | bufy_v2 | bufy_v1 | cg | qwen | human |')
    md.append('|---|---|---|---|---|---|---|---|')
    for i, r in enumerate(sorted_b[:10], 1):
        md.append('| {i} | {sid} | {med} | {b2} | {b1} | {cg} | {qw} | {hu} |'.format(
            i=i, sid=r['sid'],
            med=r['median_total'],
            b2=r['bufy_v2_total'], b1=r['bufy_v1_total'], cg=r['cg_total'],
            qw=r['qwen_avg'], hu=r['human_total']))
    md.append('')
    md.append("### Bottom 10")
    md.append('| # | sid | median_total | bufy_v2 | bufy_v1 | cg | qwen | human |')
    md.append('|---|---')
    for i, r in enumerate(sorted_b[-10:], 1):
        md.append('| {i} | {sid} | {med} | {b2} | {b1} | {cg} | {qw} | {hu} |'.format(
            i=i, sid=r['sid'],
            med=r['median_total'],
            b2=r['bufy_v2_total'], b1=r['bufy_v1_total'], cg=r['cg_total'],
            qw=r['qwen_avg'], hu=r['human_total']))
    md.append('')
    md.append('## 6. Verdict 分歧明细')
    for sid, mv, votes in verdict_diff[:30]:
        md.append(f"- **{sid}** median={mv}, AI votes={votes}")
    OUT_DEVIATION.write_text('\n'.join(md), encoding='utf-8')
    print(f"deviation md: {OUT_DEVIATION}")
    # 也写到 stdout 摘要
    print()
    print("\n=== 💡 各 AI 偏离基线 (|avg|, n) ===")
    for k in ['bufy_v2', 'bufy_v1', 'cg', 'qwen', 'human']:
        print(stat(k, devs[k]))
    print(f"\n=== Verdict 跟众数一致性: {cnt_verdict_match}/{total_verdict} ===")


if __name__ == '__main__':
    main()
