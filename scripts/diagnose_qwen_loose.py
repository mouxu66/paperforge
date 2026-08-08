"""精确诊断千问偏松规律：哪些维度在虚高、什么类型的报告被高估。"""
import csv, io, sys
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ROOT = Path(__file__).resolve().parent.parent

# 加载
bl = {}
with open(ROOT / 'deliverables/wb_43_baseline.csv', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        bl[r['sid']] = r

bufy = {}
with open(ROOT / 'deliverables/bufy_scores_43.csv', encoding='utf-8-sig') as f:
    for r in csv.DictReader(f):
        bufy[r['sid']] = r

def F(v, d=None):
    try: return float(v)
    except: return d

# 千问偏松 >0.10 的篇
loose = []
tight = []
for sid, r in bl.items():
    qw = F(r.get('qwen_avg'))
    med = F(r.get('median_total'))
    if qw is None or med is None: continue
    dev = qw - med
    bu = bufy.get(sid) or {}
    if dev > 0.10:
        loose.append((sid, dev, F(r.get('median_ua')), F(r.get('median_ad')), F(r.get('median_ii')), F(r.get('median_es')),
                       F(bu.get('ua')), F(bu.get('ad')), F(bu.get('ii')), F(bu.get('es')),
                       bu.get('section_keys_count',''), bu.get('chars','')))
    elif dev < -0.05:
        tight.append((sid, dev, '', '', '', '', '', '', '', '', '', ''))

print("="*80)
print("千问偏松(dev>0.10):", len(loose), "篇")
print("千问偏严(dev<-0.05):", len(tight), "篇")
print()

# 特征分析（仅偏松）
for label, lst in [("偏松", loose)]:
    if not lst: continue
    print(f"--- 千问{label}篇特征 ---")
    sec4 = sum(1 for x in lst if x[10] == '4')
    sec3 = sum(1 for x in lst if x[10] == '3')
    sec2 = sum(1 for x in lst if x[10] == '2')
    short = sum(1 for x in lst if int(x[11] or 0) < 2000)
    no_ref = sum(1 for x in lst if x[10] in ('2','3'))
    bufy_ii_lt_50 = sum(1 for x in lst if x[8] and x[8] < 0.50)
    med_ii_lt_50 = sum(1 for x in lst if x[4] and x[4] < 0.50)
    print(f"  4段={sec4}  3段={sec3}  2段={sec2}  缺reflection段={no_ref}  短篇(<2000)={short}  Bufy_ii<0.5={bufy_ii_lt_50}  median_ii<0.5={med_ii_lt_50}")
    print()

# Top 10 千问最松篇的细节
print("--- 千问最松 Top 10 ---")
sorted_loose = sorted(loose, key=lambda x: -x[1])
for sid, dev, m_ua, m_ad, m_ii, m_es, b_ua, b_ad, b_ii, b_es, secs, chars in sorted_loose[:10]:
    bu = bufy.get(sid) or {}
    note = (bu.get('bufy_note','') or '')[:100]
    flags = bu.get('bufy_flags','')
    print(f"  {sid} | qwen_dev={dev:+.3f} | median(ua={m_ua:.2f}, ad={m_ad:.2f}, ii={m_ii:.2f}, es={m_es:.2f}) | Bufy(ua={b_ua:.2f}, ad={b_ad:.2f}, ii={b_ii:.2f}, es={b_es:.2f}) | 段={secs} 字={chars} | flags={flags}")
    if note: print(f"    note: {note}")

# 千问一致的篇(dev<0.05)的特征对比
agree = [(sid, F(r['qwen_avg'])-F(r['median_total'])) for sid,r in bl.items() if F(r.get('qwen_avg')) and F(r.get('median_total')) and abs(F(r['qwen_avg'])-F(r['median_total'])) < 0.05]
print(f"\n千问一致(dev<0.05): {len(agree)} 篇")
agree_sids = set(s for s,_ in agree)
agree_bufy = [bufy[s] for s in agree_sids if s in bufy]
agree_sec4 = sum(1 for b in agree_bufy if b.get('section_keys_count','')=='4')
agree_ii_lt_50 = sum(1 for b in agree_bufy if F(b.get('ii')) and F(b['ii'])<0.50)
print(f"  其中4段={agree_sec4}/{len(agree_sids)}, Bufy_ii<0.5={agree_ii_lt_50}")
