"""检查当前 PaperFigure 表的 qwen_summary 状态作为修复前 baseline。"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
db = ROOT / "mock_api" / "paperforge_mock.db"
conn = sqlite3.connect(str(db))
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM paper_figures")
total = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM paper_figures WHERE qwen_summary IS NULL OR qwen_summary = ''")
empty = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM paper_figures WHERE ocr_text IS NOT NULL AND ocr_text != ''")
has_ocr = cur.fetchone()[0]
cur.execute("SELECT COUNT(DISTINCT paper_id) FROM paper_figures")
papers = cur.fetchone()[0]

print(f"=== PaperFigure 表 baseline 状态 ===")
print(f"论文数: {papers}")
print(f"图总数: {total}")
print(f"有 ocr_text: {has_ocr} ({100*has_ocr//max(total,1)}%)")
print(f"qwen_summary 空: {empty} ({100*empty//max(total,1)}%)")
print(f"qwen_summary 有: {total - empty} ({100*(total-empty)//max(total,1)}%)")

# 预期修复后缺失率
fixable = 0
cur.execute("SELECT paper_id, figure_index FROM paper_figures WHERE qwen_summary IS NULL OR qwen_summary = ''")
for pid, fidx in cur.fetchall():
    cur2 = conn.cursor()
    cur2.execute("SELECT ocr_text FROM paper_figures WHERE paper_id=? AND figure_index=?", (pid, fidx))
    row = cur2.fetchone()
    if row and row[0] and row[0].strip():
        fixable += 1
print(f"\n修复后预期：")
print(f"  qwen_summary 空且 ocr_text 有: {fixable} 张可被兜底")
print(f"  修复后剩余空: {empty - fixable} 张 (true null，无任何文本)")
print(f"  修复后缺失率: {100*(empty-fixable)//max(total,1)}% (从 {100*empty//max(total,1)}% 降)")

conn.close()
