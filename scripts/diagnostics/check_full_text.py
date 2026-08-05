#!/usr/bin/env python3
"""诊断脚本：检查 papers 表中 full_text 字段的实际状态。

用法：
    python scripts/check_full_text.py

输出：
    - 总论文数
    - 有 full_text（≥200 字符）的论文列表
    - 无 full_text 的论文列表
    - DEPTH 评估会使用哪个数据源
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mock_api.database import SessionLocal
from mock_api.models import Paper as PaperORM

db = SessionLocal()

try:
    papers = db.query(PaperORM).all()

    print(f"=== 数据库 full_text 状态诊断 ===\n")
    print(f"总论文数: {len(papers)}")

    has_full = []
    no_full = []
    for p in papers:
        ft_len = len(p.full_text) if p.full_text else 0
        if ft_len >= 200:
            has_full.append((p, ft_len))
        else:
            no_full.append((p, ft_len))

    print(f"有 full_text（≥200 字符）: {len(has_full)} 篇")
    print(f"无 full_text（或 < 200 字符）: {len(no_full)} 篇")
    print()

    if has_full:
        print("--- 有 full_text 的论文（DEPTH 评估将使用全文）---")
        for p, ft_len in has_full:
            print(f"  [{p.id}] ft_len={ft_len:>6}  chunks={p.chunk_count:>4}  {p.title[:60]}")
        print()

    if no_full:
        print("--- 无 full_text 的论文（DEPTH 评估将降级为 abstract）---")
        for p, ft_len in no_full:
            ab_len = len(p.abstract) if p.abstract else 0
            src = "abstract" if ab_len > 0 else "title（仅标题兜底！）"
            print(f"  [{p.id}] ft_len={ft_len:>6}  chunks={p.chunk_count:>4}  ab_len={ab_len:>4}  → 使用: {src}  {p.title[:50]}")
        print()

    # 关键诊断：chunk_count > 0 但 full_text 为空
    inconsistent = [(p, len(p.full_text) if p.full_text else 0)
                    for p in papers
                    if p.chunk_count and p.chunk_count > 0 and (not p.full_text or len(p.full_text) < 200)]
    if inconsistent:
        print("!!! 警告：以下论文 chunk_count > 0 但 full_text 为空回填会跳过它们 !!!")
        for p, ft_len in inconsistent:
            print(f"  [{p.id}] chunks={p.chunk_count}  ft_len={ft_len}  {p.title[:60]}")
        print("  解决：手动运行 python scripts/backfill_chunks.py --paper-id <ID> 强制回填")
    else:
        print("✅ 无 chunk_count/full_text 不一致的论文")

finally:
    db.close()
