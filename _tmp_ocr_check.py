import sqlite3
c = sqlite3.connect("mock_api/paperforge_mock.db")
rows = c.execute("PRAGMA table_info(paper_figures)").fetchall()
print("cols:", [r[1] for r in rows])
tot = c.execute("SELECT COUNT(*) FROM paper_figures").fetchone()[0]
oc = c.execute("SELECT COUNT(*) FROM paper_figures WHERE ocr_text IS NOT NULL AND LENGTH(TRIM(ocr_text))>0").fetchone()[0]
cap = c.execute("SELECT COUNT(*) FROM paper_figures WHERE caption_text IS NOT NULL AND LENGTH(TRIM(caption_text))>0").fetchone()[0]
print(f"total_figs={tot} with_ocr={oc} with_caption={cap}")
# 抽样看 OCR 内容长度
sample = c.execute("SELECT paper_id, LENGTH(ocr_text) FROM paper_figures WHERE ocr_text IS NOT NULL ORDER BY LENGTH(ocr_text) DESC LIMIT 3").fetchall()
print("top ocr lengths:", sample)
