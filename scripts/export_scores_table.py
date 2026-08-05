"""把 613 篇论文的审稿结果做成表格（CSV + HTML）发给老师。

输出：
- deliverables/depth_scores_613.csv（Excel 友好，UTF-8 BOM）
- deliverables/depth_scores_613.html（手机可直接打开）
"""
from __future__ import annotations

import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "mock_api" / "paperforge_mock.db"
OUT_CSV = ROOT / "deliverables" / "depth_scores_613.csv"
OUT_HTML = ROOT / "deliverables" / "depth_scores_613.html"


def verdict_zh(v: str | None) -> str:
    if not v:
        return "—"
    m = {
        "accept": "接受",
        "minor_revision": "小修",
        "minor": "小修",
        "major_revision": "大修",
        "major": "大修",
        "reject": "拒绝",
        "reject_revision": "拒绝",
    }
    return m.get(v.replace("_revision", ""), v)


def main():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # 联表查询：DepthReviewV4 + Paper（取最新一条 kind='paper' 的记录）
    cur.execute("""
        SELECT d.paper_id, p.title, p.year, p.source, p.journal,
               d.final_verdict, d.created_at
        FROM depth_reviews_v4 d
        JOIN papers p ON p.id = d.paper_id
        WHERE d.kind = 'paper' AND d.final_verdict IS NOT NULL
        ORDER BY d.created_at DESC
    """)

    rows = []
    for paper_id, title, year, source, journal, fv_json, created_at in cur.fetchall():
        try:
            fv = json.loads(fv_json) if isinstance(fv_json, str) else fv_json
        except Exception:
            fv = {}

        score = fv.get("calibrated_score") or fv.get("score") or 0
        base = fv.get("base_score") or 0
        verdict = fv.get("final_verdict") or fv.get("verdict") or "—"
        delta = fv.get("delta") or 0
        qf = fv.get("qf_score")
        qf_str = f"{qf:.2f}" if qf is not None else "—"

        # 评分节点明细（可选）
        q0 = fv.get("q0_result", {})
        q1 = fv.get("q1_result", {})
        q234 = fv.get("q234_result", {})

        rows.append({
            "序号": 0,  # 后面填
            "论文ID": paper_id,
            "标题": title[:80] if title else "",
            "年份": year or "",
            "来源": source or "",
            "期刊/会议": journal or "",
            "最终分数": round(score, 3),
            "基础分数": round(base, 3),
            "校准偏移": round(delta, 3),
            "裁决": verdict_zh(verdict),
            "QF图文一致性": qf_str,
            "审稿时间": created_at[:19] if created_at else "",
        })
    conn.close()

    # 按分数降序排
    rows.sort(key=lambda r: r["最终分数"], reverse=True)
    for i, r in enumerate(rows, 1):
        r["序号"] = i

    print(f"总篇数: {len(rows)}")
    print(f"分数分布: min={min(r['最终分数'] for r in rows):.3f} "
          f"max={max(r['最终分数'] for r in rows):.3f} "
          f"mean={sum(r['最终分数'] for r in rows)/len(rows):.3f}")

    # ── 写 CSV（UTF-8 BOM，Excel 友好）──
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nCSV 已写入: {OUT_CSV}  ({OUT_CSV.stat().st_size//1024} KB)")

    # ── 写 HTML（手机可查看）──
    accept = sum(1 for r in rows if r["裁决"] == "接受")
    minor = sum(1 for r in rows if r["裁决"] == "小修")
    major = sum(1 for r in rows if r["裁决"] == "大修")
    reject = sum(1 for r in rows if r["裁决"] == "拒绝")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>PaperForge DEPTH 审稿结果 - {len(rows)} 篇</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
       background: #f5f5f5; color: #333; padding: 12px; font-size: 14px; }}
h1 {{ font-size: 18px; margin-bottom: 8px; color: #1a237e; }}
.summary {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }}
.card {{ background: white; padding: 8px 12px; border-radius: 6px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1); min-width: 80px; }}
.card .num {{ font-size: 20px; font-weight: 700; }}
.card .lbl {{ font-size: 11px; color: #666; }}
.card.accept .num {{ color: #2e7d32; }}
.card.reject .num {{ color: #c62828; }}
.card.minor .num {{ color: #f57f17; }}
.card.major .num {{ color: #e65100; }}
.search {{ margin-bottom: 8px; }}
.search input {{ width: 100%; padding: 8px; border: 1px solid #ddd;
                 border-radius: 4px; font-size: 14px; }}
table {{ width: 100%; border-collapse: collapse; background: white;
        border-radius: 6px; overflow: hidden;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
th, td {{ padding: 8px 6px; text-align: left; border-bottom: 1px solid #eee;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
th {{ background: #1a237e; color: white; font-size: 12px; position: sticky;
      top: 0; z-index: 1; }}
td {{ font-size: 13px; }}
td.title {{ white-space: normal; max-width: 280px; }}
tr.accept {{ background: #f1f8e9; }}
tr.reject {{ background: #ffebee; }}
tr.minor {{ background: #fff8e1; }}
tr.major {{ background: #fff3e0; }}
.score {{ font-weight: 700; font-family: monospace; }}
.score-high {{ color: #2e7d32; }}
.score-mid {{ color: #f57f17; }}
.score-low {{ color: #c62828; }}
.footer {{ margin-top: 12px; font-size: 11px; color: #999; text-align: center; }}
@media (max-width: 600px) {{
  th, td {{ font-size: 12px; padding: 6px 4px; }}
  td.title {{ max-width: 140px; }}
  .hide-mobile {{ display: none; }}
}}
</style>
</head>
<body>
<h1>PaperForge DEPTH 审稿结果</h1>
<div style="font-size:12px;color:#666;margin-bottom:12px;">
  共 {len(rows)} 篇 ｜ 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")} ｜ 模型：Qwen3.5-9B
</div>
<div class="summary">
  <div class="card accept"><div class="num">{accept}</div><div class="lbl">接受</div></div>
  <div class="card minor"><div class="num">{minor}</div><div class="lbl">小修</div></div>
  <div class="card major"><div class="num">{major}</div><div class="lbl">大修</div></div>
  <div class="card reject"><div class="num">{reject}</div><div class="lbl">拒绝</div></div>
</div>
<div class="search"><input type="text" id="q" placeholder="搜索标题或ID..." oninput="filter()"></div>
<table id="t">
<thead><tr>
<th>#</th><th>论文ID</th><th>标题</th><th class="hide-mobile">年份</th>
<th class="hide-mobile">来源</th><th>分数</th><th>裁决</th>
<th class="hide-mobile">QF</th><th class="hide-mobile">审稿时间</th>
</tr></thead>
<tbody>
"""
    for r in rows:
        verdict_class = {
            "接受": "accept", "小修": "minor", "大修": "major", "拒绝": "reject"
        }.get(r["裁决"], "")
        score_class = "score-high" if r["最终分数"] >= 0.75 else (
            "score-mid" if r["最终分数"] >= 0.5 else "score-low")
        html += f"""<tr class="{verdict_class}">
<td>{r['序号']}</td>
<td>{r['论文ID'][:30]}</td>
<td class="title">{r['标题']}</td>
<td class="hide-mobile">{r['年份']}</td>
<td class="hide-mobile">{r['来源']}</td>
<td class="score {score_class}">{r['最终分数']:.3f}</td>
<td>{r['裁决']}</td>
<td class="hide-mobile">{r['QF图文一致性']}</td>
<td class="hide-mobile">{r['审稿时间'][:10]}</td>
</tr>\n"""

    html += f"""</tbody></table>
<div class="footer">PaperForge DEPTH v4.2 · {len(rows)} papers · {datetime.now().strftime('%Y-%m-%d')}</div>
<script>
function filter(){{
  var q=document.getElementById('q').value.toLowerCase();
  var rows=document.querySelectorAll('#t tbody tr');
  rows.forEach(function(r){{
    var t=r.innerText.toLowerCase();
    r.style.display=t.indexOf(q)>-1?'':'none';
  }});
}}
</script>
</body></html>"""

    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"HTML 已写入: {OUT_HTML}  ({OUT_HTML.stat().st_size//1024} KB)")


if __name__ == "__main__":
    main()
