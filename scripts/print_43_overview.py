"""43 篇总览 + 抽样"""
import json
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

data = json.load(open('deliverables/bufy_43_reports.json', encoding='utf-8'))
print(f"总篇数: {len(data)}")
# 论文所属域概览（顶层标题）
print("\n=== 43 篇 报告标题 总览 ===")
for i, d in enumerate(data):
    sid = d.get('student_id') or '-'
    name = d.get('name') or '-'
    title = d.get('paper_title') or '(空)'
    secs = d.get('section_keys') or []
    chars = d.get('raw_chars') or 0
    print(f"[{i+1:2}] {sid:<14} {name[:14]:<14} {len(secs)}段 {chars:>5}字 | {title[:90]}")
print(f"\n=== 全 4 段: {sum(1 for d in data if len(d.get('section_keys', []))==4)} 篇")
print(f"=== 3 段: {sum(1 for d in data if len(d.get('section_keys', []))==3)} 篇")
print(f"=== 2 段: {sum(1 for d in data if len(d.get('section_keys', []))==2)} 篇")
