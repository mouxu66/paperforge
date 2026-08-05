"""回填清理 + 质量门控回填。

撤销之前无脑回填的 qwen_summary（用 ocr_text 覆盖的），
重新用 _is_ocr_text_noise 质量门控过滤后再回填。
"""
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from mock_api.workers.figures import _is_ocr_text_noise

db = ROOT / "mock_api" / "paperforge_mock.db"
conn = sqlite3.connect(str(db))
cur = conn.cursor()

# 查当前 qwen_summary 状态
cur.execute("SELECT COUNT(*) FROM paper_figures")
total = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM paper_figures WHERE qwen_summary IS NULL OR qwen_summary = ''")
empty_before = cur.fetchone()[0]
print(f"=== 回填前状态 ===")
print(f"图总数: {total}, qwen_summary 空: {empty_before} ({100*empty_before//max(total,1)}%)")

# 把所有 qwen_summary == ocr_text 的记录清空（撤销之前无脑回填）
# 然后用质量门控重新回填
cur.execute("""
    UPDATE paper_figures
    SET qwen_summary = NULL
    WHERE qwen_summary = ocr_text
      AND ocr_text IS NOT NULL
      AND ocr_text != ''
""")
cleared = cur.rowcount
print(f"\n撤销无脑回填: {cleared} 条（qwen_summary 重置为 NULL）")

# 用质量门控重新回填
cur.execute("SELECT paper_id, figure_index, ocr_text FROM paper_figures WHERE (qwen_summary IS NULL OR qwen_summary = '') AND ocr_text IS NOT NULL AND ocr_text != ''")
rows = cur.fetchall()
refilled = 0
filtered = 0
for paper_id, fig_idx, ocr_text in rows:
    if _is_ocr_text_noise(ocr_text):
        filtered += 1
        continue
    cur.execute(
        "UPDATE paper_figures SET qwen_summary = ? WHERE paper_id = ? AND figure_index = ?",
        (ocr_text, paper_id, fig_idx)
    )
    refilled += 1

conn.commit()

# 最终状态
cur.execute("SELECT COUNT(*) FROM paper_figures WHERE qwen_summary IS NULL OR qwen_summary = ''")
empty_after = cur.fetchone()[0]
print(f"\n=== 回填后状态 ===")
print(f"撤销: {cleared} 条")
print(f"噪声过滤: {filtered} 条（不回填，保持 NULL）")
print(f"质量门控回填: {refilled} 条")
print(f"图总数: {total}, qwen_summary 空: {empty_after} ({100*empty_after//max(total,1)}%)")
print(f"\n修复前缺失率: {100*empty_before//max(total,1)}%")
print(f"修复后缺失率: {100*empty_after//max(total,1)}%")
print(f"过滤掉的噪声: {filtered} 条")

conn.close()
