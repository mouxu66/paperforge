"""统计可用数据集规模与图证据覆盖率。"""
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DB = ROOT / "mock_api" / "paperforge_mock.db"
idx_path = ROOT / "deliverables" / "peerread_index.json"

# 1. PeerRead 索引总数
idx = json.loads(idx_path.read_text(encoding="utf-8"))
print(f"=== PeerRead 索引 ===")
print(f"总数: {len(idx)}")
print(f"accepted: {sum(1 for x in idx if x.get('accepted'))}")
print(f"reject:   {sum(1 for x in idx if not x.get('accepted'))}")
print(f"keys: {list(idx[0].keys()) if idx else []}")

# 2. PaperFigure 表覆盖率
conn = sqlite3.connect(str(DB))
cur = conn.cursor()
cur.execute("SELECT DISTINCT paper_id FROM paper_figures WHERE paper_id LIKE 'pr_%'")
fig_pids = set(r[0] for r in cur.fetchall())
print(f"\n=== PaperFigure 表 ===")
print(f"有图证据的 pr_ 论文: {len(fig_pids)} 篇")

# 3. 看 peerread_index 里有 parsed_path 的有多少
with_parsed = [x for x in idx if x.get('parsed_path')]
print(f"\n=== PeerRead 索引里 parsed_path 存在 ===")
print(f"有 parsed_path: {len(with_parsed)}")
# 检查文件是否真实存在
existing = []
for x in with_parsed:
    p = x.get('parsed_path', '')
    if p and Path(p).exists():
        existing.append(x)
print(f"parsed_path 文件存在: {len(existing)}")

# 4. 看看有 parsed_path 但没图证据的论文（可批量补图）
no_fig = []
for x in existing:
    pid = f"pr_{x['stem']}"
    if pid not in fig_pids:
        no_fig.append(x)
print(f"\n=== 有全文但无图证据的论文 ===")
print(f"数量: {len(no_fig)}")
if no_fig:
    print(f"前 5 个 stem: {[x['stem'] for x in no_fig[:5]]}")
    print(f"accepted 分布: {sum(1 for x in no_fig if x.get('accepted'))} accept, {sum(1 for x in no_fig if not x.get('accepted'))} reject")

# 5. 总览：可立即用于 figures-OFF 测试的论文（有全文 + 有金标）
all_with_text = existing
print(f"\n=== 立即可用于测试的论文 ===")
print(f"有全文 + 有金标: {len(all_with_text)}")
print(f"  accept: {sum(1 for x in all_with_text if x.get('accepted'))}")
print(f"  reject: {sum(1 for x in all_with_text if not x.get('accepted'))}")

conn.close()
