"""查 depth_reviews_v4 表里 pr_ 论文的 QF reasoning。"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "mock_api" / "paperforge_mock.db"
conn = sqlite3.connect(str(DB))
cur = conn.cursor()

# 查 pr_ 论文是否有记录
cur.execute("SELECT paper_id, created_at FROM depth_reviews_v4 WHERE paper_id LIKE 'pr_%' ORDER BY created_at DESC LIMIT 10")
rows = cur.fetchall()
print(f"=== depth_reviews_v4 里 pr_ 论文记录: {len(rows)} ===")
for pid, created in rows:
    print(f"  {pid} | {created}")

# 取最新的一条，dump final_verdict 看 QF 字段
if rows:
    pid = rows[0][0]
    cur.execute("SELECT final_verdict FROM depth_reviews_v4 WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1", (pid,))
    fv = cur.fetchone()[0]
    if isinstance(fv, str):
        try:
            fv = json.loads(fv)
        except Exception:
            pass
    print(f"\n=== {pid} final_verdict ===")
    if isinstance(fv, dict):
        print(f"keys: {list(fv.keys())}")
        # 找 QF 相关
        for k, v in fv.items():
            if any(x in k.lower() for x in ("qf", "figure", "consist")):
                print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:400] if isinstance(v,(dict,list)) else v}")
    else:
        print(f"  raw: {str(fv)[:500]}")

conn.close()
