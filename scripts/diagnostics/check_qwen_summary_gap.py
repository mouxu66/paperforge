"""检查 qwen_summary 缺失原因：vlm_decision 分布 / ocr_text 是否空 / source 分布。"""
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
db_path = ROOT / "mock_api" / "paperforge_mock.db"
conn = sqlite3.connect(str(db_path))
cur = conn.cursor()

# 字段对齐：vlm_decision / source / ocr_text / caption_text / qwen_summary
cur.execute("""
SELECT paper_id,
       COUNT(*) AS total,
       SUM(CASE WHEN qwen_summary IS NULL OR qwen_summary='' THEN 1 ELSE 0 END) AS no_qs,
       SUM(CASE WHEN ocr_text IS NULL OR ocr_text='' THEN 1 ELSE 0 END) AS no_ocr,
       SUM(CASE WHEN caption_text IS NULL OR caption_text='' THEN 1 ELSE 0 END) AS no_cap
FROM paper_figures
GROUP BY paper_id
ORDER BY no_qs DESC, total DESC
""")
print(f"{'paper_id':<28} {'total':>5} {'no_qs':>5} {'no_ocr':>6} {'no_cap':>6}")
for r in cur.fetchall():
    print(f"{r[0]:<28} {r[1]:>5} {r[2]:>5} {r[3]:>6} {r[4]:>6}")

# vlm_decision 分布
cur.execute("SELECT vlm_decision, COUNT(*) FROM paper_figures GROUP BY vlm_decision")
print("\nvlm_decision 分布:")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

# source 分布
cur.execute("SELECT source, COUNT(*) FROM paper_figures GROUP BY source")
print("\nsource 分布:")
for r in cur.fetchall():
    print(f"  {r[0]}: {r[1]}")

# 有 ocr_text 但缺 qwen_summary 的（应跑 VLM 但失败的子集）
cur.execute("""
SELECT COUNT(*) FROM paper_figures
WHERE (qwen_summary IS NULL OR qwen_summary='')
  AND ocr_text IS NOT NULL AND ocr_text != ''
""")
lost = cur.fetchone()[0]
cur.execute("""
SELECT COUNT(*) FROM paper_figures
WHERE (qwen_summary IS NULL OR qwen_summary='')
  AND ocr_text IS NOT NULL AND ocr_text != ''
  AND (vlm_decision IS NULL OR vlm_decision NOT IN ('rule_only','skip'))
""")
lost_route = cur.fetchone()[0]
print(f"\n有 ocr_text 但缺 qwen_summary: {lost}")
print(f"  其中 vlm_decision 不是 rule_only/skip（即本应跑 VLM 但失败）: {lost_route}")

conn.close()
