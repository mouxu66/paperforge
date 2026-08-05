"""调研：reject 论文数据可用性 + figure pass 来源。"""
import json
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "mock_api" / "paperforge_mock.db"
IDX = ROOT / "deliverables" / "peerread_index.json"

# 1. pr_ 前缀论文的 figure_path 完整路径
conn = sqlite3.connect(str(DB))
cur = conn.cursor()
cur.execute("SELECT DISTINCT paper_id, figure_path FROM paper_figures WHERE paper_id LIKE 'pr_%' LIMIT 3")
print("=== pr_ 论文 figure_path 样例 ===")
for pid, fp in cur.fetchall():
    print(f"  {pid}: {fp}")

# 2. PeerRead 论文 PDF 是否在某处
cur.execute("SELECT DISTINCT paper_id FROM paper_figures WHERE paper_id LIKE 'pr_%'")
pr_pids = [r[0] for r in cur.fetchall()]
pr_stems = [p[3:] for p in pr_pids]
conn.close()

# 3. uploads/ 下是否有 pr_ PDF
uploads = ROOT / "uploads"
pdf_hits = []
for stem in pr_stems:
    p = uploads / f"pr_{stem}.pdf"
    if p.exists():
        pdf_hits.append(str(p))
print(f"\n=== uploads/ 下 PeerRead PDF ===")
print(f"  找到: {len(pdf_hits)}/{len(pr_stems)}")
if pdf_hits:
    print(f"  样例: {pdf_hits[0]}")

# 4. 全盘搜 PeerRead stem 的 PDF
print(f"\n=== 全盘搜 PeerRead PDF ===")
for stem in pr_stems[:5]:
    # 在项目根下搜
    hits = list(ROOT.rglob(f"*{stem}*.pdf"))
    print(f"  {stem}: {hits[:2] if hits else '无 PDF'}")

# 5. PeerRead reject 论文 venue 分布（取 arxiv 子集，arxiv 有 PDF 可下载）
idx = json.loads(IDX.read_text(encoding="utf-8"))
rej = [j for j in idx if not j["accepted"]]
arxiv_rej = [j for j in rej if j["venue"].startswith("arxiv.cs")]
iclr_rej = [j for j in rej if j["venue"] == "iclr_2017"]
print(f"\n=== PeerRead reject 论文 ===")
print(f"  总数: {len(rej)}")
print(f"  arxiv 子集（可下载 PDF）: {len(arxiv_rej)}")
print(f"  iclr 子集（PDF 难拿）: {len(iclr_rej)}")
print(f"  arxiv reject venue 分布: {dict(Counter(j['venue'] for j in arxiv_rej))}")

# 6. 看 arxiv reject 论文的 stem 格式（确认可拼 arxiv URL）
print(f"\n=== arxiv reject stem 样例（可拼 arxiv.org/abs/{stem}）===")
for j in arxiv_rej[:5]:
    print(f"  {j['stem']} | {j['title'][:50]}")
