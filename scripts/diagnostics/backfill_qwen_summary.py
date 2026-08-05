"""回填历史 qwen_summary NULL：模拟新代码兜底逻辑。

对每条 qwen_summary 为空但 ocr_text 非空的记录，把 ocr_text 写入 qwen_summary。
这是幂等的：再次运行不会改变已经有 qwen_summary 的记录。
"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
db = ROOT / "mock_api" / "paperforge_mock.db"
conn = sqlite3.connect(str(db))
cur = conn.cursor()

cur.execute("""
    UPDATE paper_figures
    SET qwen_summary = ocr_text
    WHERE (qwen_summary IS NULL OR qwen_summary = '')
      AND ocr_text IS NOT NULL
      AND ocr_text != ''
""")
filled = cur.rowcount
conn.commit()

cur.execute("SELECT COUNT(*) FROM paper_figures")
total = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM paper_figures WHERE qwen_summary IS NULL OR qwen_summary = ''")
empty = cur.fetchone()[0]

print(f"=== 回填完成 ===")
print(f"已填充: {filled} 条")
print(f"图总数: {total}")
print(f"qwen_summary 空: {empty} ({100*empty//max(total,1)}%)")
print(f"qwen_summary 有: {total - empty} ({100*(total-empty)//max(total,1)}%)")

conn.close()
