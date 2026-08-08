"""我亲自读 43 篇。

每篇打印: 标题、4 段定位、段头/段尾关键句,让我快速判定 6 维。
支持 params: --start 0 --end 10。
"""
import json, sys, io, argparse
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

ap = argparse.ArgumentParser()
ap.add_argument("--start", type=int, default=0)
ap.add_argument("--end", type=int, default=43)
args = ap.parse_args()

data = json.load(open('deliverables/bufy_43_reports.json', encoding='utf-8'))

for i in range(args.start, args.end):
    if i >= len(data):
        break
    d = data[i]
    print('\n' + '=' * 80)
    print(f"\n[{i+1}/{len(data)}] FILE: {d.get('file')}")
    print(f"标题: {(d.get('paper_title') or '(空)')[:160]}")
    print(f"作者/源: {d.get('paper_author') or '(空)'} / {d.get('paper_source') or '(空)'}")
    print(f"段数: {d.get('section_keys')} | 总字 {d.get('raw_chars')}")
    secs = d.get('section_text', {})
    for k, v in secs.items():
        head = v[:380] if v else '(empty)'
        tail = v[-380:] if len(v) > 760 else ''
        print(f"\n--- [{k}] 字 {len(v)} ---")
        print(head)
        if tail:
            print("  // 尾 //")
            print(tail)
