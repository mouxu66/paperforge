"""冒烟测试：新 prompt + temp 0.2 对 4 篇是否产生区分度。"""
import csv, sqlite3, time, os, sys
sys.path.insert(0, '.')
os.environ.setdefault('PAPERFORGE_EVAL_SEED', '42')
os.environ.setdefault('PAPERFORGE_LLM_CACHE_TTL', '0')

from mock_api.depth_eval_reflection import ReflectionReviewer

DB = "mock_api/paperforge_mock.db"
targets = ['2101', '2109', '2114', '2138']

# 读金标
gold = {}
with open('deliverables/human_benchmark_full.csv', encoding='utf-8-sig') as f:
    for row in csv.DictReader(f):
        gold[row['sid']] = row

conn = sqlite3.connect(DB)
# 不用自定义 llm_func，让 ReflectionReviewer 走 compute mode 的温度
reviewer = ReflectionReviewer()
results = []

for sid in targets:
    full_sid = f"20230300{sid}"
    cur = conn.cursor()
    cur.execute(
        "SELECT p.id, p.full_text, s.full_text FROM papers p JOIN papers s ON s.id = p.source_paper_id WHERE p.id = ?",
        (f"reflection_{full_sid}",),
    )
    row = cur.fetchone()
    if not row:
        print(f"  {sid}: NOT FOUND")
        continue
    rid, rep, paper = row

    t0 = time.time()
    result = reviewer.review(paper_id=rid, title=sid, full_text=rep, paper_text="")
    elapsed = time.time() - t0

    sc = result.scores
    avg = sum(sc.values()) / len(sc) if sc else 0
    human_row = gold.get(full_sid, {})
    human_total = float(human_row.get('total', 0))

    print(f"  {sid}: UA={sc.get('understanding_accuracy',0):.2f} AD={sc.get('analysis_depth',0):.2f} "
          f"II={sc.get('innovative_insights',0):.2f} ES={sc.get('evidence_support',0):.2f} "
          f"→ avg={avg:.3f} (human={human_total:.3f}) [{elapsed:.0f}s]")
    results.append({'sid': sid, 'avg': avg, 'human': human_total, 'scores': sc, 'elapsed': elapsed})

conn.close()

# 汇总
print(f"\n--- 区分度 ---")
avgs = [r['avg'] for r in results]
print(f"  范围: {min(avgs):.3f} ~ {max(avgs):.3f} (跨度={max(avgs)-min(avgs):.3f})")
print(f"  之前 Ornstein-V2 跨度: 0.15（几乎全是 0.775/0.800/0.825 三个值）")
print(f"\n--- vs 人工 ---")
for r in results:
    delta = r['avg'] - r['human']
    print(f"  {r['sid']}: Ornstein-V2={r['avg']:.3f} Human={r['human']:.3f} Δ={delta:+.3f}")
