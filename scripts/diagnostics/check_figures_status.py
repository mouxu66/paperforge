"""检查 PaperFigure 表状态 + figure pass 触发机制。"""
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
db_path = ROOT / "mock_api" / "paperforge_mock.db"
if not db_path.exists():
    print(f"DB not found: {db_path}")
    raise SystemExit

conn = sqlite3.connect(str(db_path))
cur = conn.cursor()

# 论文分布
cur.execute("SELECT paper_id, COUNT(*) as n FROM paper_figures GROUP BY paper_id ORDER BY n DESC LIMIT 20")
rows = cur.fetchall()
print("PaperFigure 表中已有图证据的论文（前 20）:")
for pid, n in rows:
    print(f"  {pid}: {n} 张图")

cur.execute("SELECT COUNT(DISTINCT paper_id) FROM paper_figures")
total = cur.fetchone()[0]
cur.execute("SELECT COUNT(*) FROM paper_figures")
total_figs = cur.fetchone()[0]
print(f"\n总论文数: {total}")
print(f"总图数: {total_figs}")

# qwen_summary 占比
cur.execute("SELECT COUNT(*) FROM paper_figures WHERE qwen_summary IS NOT NULL AND qwen_summary != ''")
qs = cur.fetchone()[0]
pct = 100 * qs / total_figs if total_figs else 0
print(f"有 qwen_summary 的图: {qs}/{total_figs} ({pct:.1f}%)")

# ocr_text 占比
cur.execute("SELECT COUNT(*) FROM paper_figures WHERE ocr_text IS NOT NULL AND ocr_text != ''")
ocr = cur.fetchone()[0]
pct = 100 * ocr / total_figs if total_figs else 0
print(f"有 ocr_text 的图: {ocr}/{total_figs} ({pct:.1f}%)")

# caption_text 占比
cur.execute("SELECT COUNT(*) FROM paper_figures WHERE caption_text IS NOT NULL AND caption_text != ''")
cap = cur.fetchone()[0]
pct = 100 * cap / total_figs if total_figs else 0
print(f"有 caption_text 的图: {cap}/{total_figs} ({pct:.1f}%)")

# 跟 PeerRead baseline 对一下（PaperFigure paper_id 用 pr_ 前缀，需剥离后匹配）
import json
cur.execute("SELECT DISTINCT paper_id FROM paper_figures")
pf_pids = {r[0] for r in cur.fetchall()}
pf_stems = {pid[3:] if pid.startswith("pr_") else pid for pid in pf_pids}

baseline_path = ROOT / "deliverables/baseline_fig_pilot.jsonl"
if baseline_path.exists():
    pr_stems = set()
    for line in baseline_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            pr_stems.add(d.get("stem", ""))
    overlap = pr_stems & pf_stems
    print(f"\nPeerRead baseline 里, PaperFigure 表覆盖: {len(overlap)}/{len(pr_stems)}")
    if overlap:
        print(f"  覆盖的 stem: {sorted(overlap)}")

# 跟 PeerRead sample 对一下（实际跑 DEPTH 的子集）
sample_path = ROOT / "deliverables/peerread_sample.json"
if sample_path.exists():
    sel = json.loads(sample_path.read_text(encoding="utf-8"))
    sample_stems = {j["stem"] for j in sel}
    overlap = sample_stems & pf_stems
    print(f"PeerRead sample 里, PaperFigure 表覆盖: {len(overlap)}/{len(sample_stems)}")

# 跟盲评 20 对一下
my_path = ROOT / "calib_papers" / "runs" / "calib_my_review.json"
if my_path.exists():
    my = json.loads(my_path.read_text(encoding="utf-8"))
    my_pids = {r["pid"] for r in my}
    my_stems = {pid[3:] if pid.startswith("pr_") else pid for pid in my_pids}
    overlap = my_stems & pf_stems
    print(f"盲评 20 篇里, PaperFigure 表覆盖: {len(overlap)}/20")

conn.close()
