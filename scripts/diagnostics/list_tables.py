"""查所有表名 + 重抽检 QF 减分论文 figure 内容。"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "mock_api" / "paperforge_mock.db"
conn = sqlite3.connect(str(DB))
cur = conn.cursor()

# 1. 所有表名
cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
print("=== 所有表 ===")
for (n,) in cur.fetchall():
    print(f"  {n}")

# 2. 找 depth 相关表
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE '%depth%' OR name LIKE '%review%')")
print("\n=== depth/review 表 ===")
for (n,) in cur.fetchall():
    print(f"  {n}")
    cur2 = conn.cursor()
    cur2.execute(f"PRAGMA table_info({n})")
    cols = [r[1] for r in cur2.fetchall()]
    print(f"    cols: {cols[:15]}")

conn.close()
