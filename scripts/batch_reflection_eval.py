"""批量导入并评价桌面感悟报告。

流程：
1. 删除现有 27 条测试感悟报告（papers.category='report'）
2. 复制桌面 docx/doc 文件到 uploads/
3. 解析每篇感悟报告，抽取 full_text
4. 调用 ReflectionReviewer 评价
5. 输出分数表格（CSV + HTML）+ 微调建议
"""
from __future__ import annotations

import csv
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOURCE_DIR = Path(
    os.environ.get(
        "PAPERFORGE_REFLECTION_SOURCE_DIR",
        str(Path.home() / "Desktop" / "Word文档"),
    )
)
UPLOADS_DIR = ROOT / "mock_api" / "uploads"
DB_PATH = ROOT / "mock_api" / "paperforge_mock.db"
OUT_CSV = ROOT / "deliverables" / "reflection_scores.csv"
OUT_HTML = ROOT / "deliverables" / "reflection_scores.html"

os.environ.setdefault("PAPERFORGE_LLM_TEMPERATURE", "0")


def step1_delete_old_reports():
    """删除现有 27 条测试感悟报告"""
    print("=== 步骤 1: 删除现有感悟报告 ===")
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM papers WHERE category='report'")
    n = cur.fetchone()[0]
    print(f"  现有 report 记录: {n} 条")

    # 先删 depth_reviews_v4 里关联的记录
    cur.execute("""
        DELETE FROM depth_reviews_v4
        WHERE paper_id IN (SELECT id FROM papers WHERE category='report')
    """)
    rv = cur.rowcount
    # 再删 papers
    cur.execute("DELETE FROM papers WHERE category='report'")
    pv = cur.rowcount
    conn.commit()
    conn.close()
    print(f"  删除 depth_reviews_v4: {rv} 条")
    print(f"  删除 papers: {pv} 条")


def step2_copy_files():
    """复制桌面感悟报告到 uploads/"""
    print("\n=== 步骤 2: 复制感悟报告到 uploads/ ===")
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

    files = []
    for ext in ("*.docx",):
        files.extend(SOURCE_DIR.glob(ext))
    print(f"  桌面文件数: {len(files)}")
    # 也包含已转换的 .docx
    pdf_files = list(SOURCE_DIR.glob("*.pdf"))
    if pdf_files:
        print(f"  PDF 文件（需另外处理）: {len(pdf_files)}")

    copied = 0
    skipped = 0
    for f in files:
        # 用固定文件名避免中文路径问题
        safe_name = f"reflection_{copied:03d}_{f.stem[:30].replace(' ', '_')}{f.suffix}"
        dst = UPLOADS_DIR / safe_name
        try:
            shutil.copy2(f, dst)
            copied += 1
        except Exception as e:
            print(f"  [skip] {f.name}: {e}")
            skipped += 1
    print(f"  复制成功: {copied}, 跳过: {skipped}")
    return copied


def step3_parse_and_review():
    """解析并评价每篇感悟报告"""
    print("\n=== 步骤 3: 解析+评价感悟报告 ===")
    from mock_api.depth_eval_reflection import ReflectionReviewer
    from mock_api.reflection_docx_parser import parse_docx_from_bytes

    reviewer = ReflectionReviewer()

    # 收集所有感悟报告文件（避免 f-string 里 * 被误解析）
    files = []
    for ext in (".docx",):
        files.extend(sorted(UPLOADS_DIR.glob(f"reflection_*{ext}")))
    print(f"  待评价文件: {len(files)}")

    results = []
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    for i, f in enumerate(files, 1):
        fname = f.name
        print(f"\n  [{i}/{len(files)}] {fname}")

        # 解析
        try:
            data = f.read_bytes()
            rdoc = parse_docx_from_bytes(data, filename=fname)
        except Exception as e:
            print(f"    [parse fail] {e}")
            continue

        full_text = rdoc.raw_text.strip()
        if not full_text or len(full_text) < 50:
            print(f"    [skip] full_text 太短({len(full_text)}c)")
            continue

        # 从文件名提取学号-姓名
        stem = f.stem  # reflection_000_999900000001-学生01
        # 尝试提取原始学号和姓名
        m = re.search(r"(\d{12})[-_\s]*([^\-_\s]+)", stem)
        student_id = m.group(1) if m else ""
        student_name = m.group(2) if m else ""

        # paper_id 用文件名（去掉扩展名）
        paper_id = f"reflection_{f.stem}"

        # title 用文件名或解析出的 paper_title
        title = rdoc.paper_title or fname

        print(f"    学号={student_id}, 姓名={student_name}, 全文={len(full_text)}c")
        print(f"    论文题目={rdoc.paper_title or '(未提取)'}")

        # 取绑定原论文全文（供千问对照核验理解准确性，可选）
        src_full = ""
        try:
            cur.execute(
                "select source_paper_id from papers where id = ?",
                (f"reflection_{student_id}",),
            )
            row = cur.fetchone()
            if row and row[0]:
                cur.execute("select full_text from papers where id = ?", (row[0],))
                prow = cur.fetchone()
                src_full = prow[0] if prow and prow[0] else ""
        except Exception:  # noqa: BLE001 - 论文参考注入失败不阻塞评审
            src_full = ""

        # 评价
        try:
            result = reviewer.review(
                paper_id=paper_id,
                title=title,
                full_text=full_text,
                student_id=student_id,
                paper_text=src_full,
            )
            scores = result.scores or {}
            avg = scores.get("average", 0)
            verdict = result.verdict
            # 检测是否有交叉验证加分
            crossval_bonus = 0.0
            for ov in result.hardcoded_overrides:
                if "交叉验证加分" in ov:
                    # 从原因里提取加分值
                    m = re.search(r"\+([\d.]+)$", ov)
                    if m:
                        crossval_bonus = float(m.group(1))
                    break
            print(f"    → verdict={verdict}, avg={avg:.3f}, "
                  f"evidence={result.effective_evidence_count}, "
                  f"crossval_bonus={crossval_bonus:.4f}")

            results.append({
                "序号": i,
                "文件名": fname,
                "学号": student_id,
                "姓名": student_name,
                "论文题目": rdoc.paper_title or "",
                "全文长度": len(full_text),
                "理解准确性": round(scores.get("understanding_accuracy", 0), 3),
                "分析深度": round(scores.get("analysis_depth", 0), 3),
                "创新见解": round(scores.get("innovative_insights", 0), 3),
                "证据支撑": round(scores.get("evidence_support", 0), 3),
                "平均分": round(avg, 3),
                "有效证据数": result.effective_evidence_count,
                "交叉验证加分": round(crossval_bonus, 4),
                "裁决": verdict_zh(verdict),
                "LLM摘要": result.summary[:100] if result.summary else "",
                "裁决原因": result.verdict_reason[:150] if result.verdict_reason else "",
            })

            # 存入 DB（papers 表 + depth_reviews_v4 表）
            # 注意：DB 存【完整全文】；16000 截断只发生在 LLM prompt 拼装时
            # （depth_eval_reflection._truncate_head_tail），避免超长报告尾部永久丢失。
            try:
                cur.execute("""
                    INSERT OR REPLACE INTO papers (id, title, authors, abstract, category, source, full_text, year, tags, journal)
                    VALUES (?, ?, '[]', ?, 'report', 'reflection', ?, 2026, '[]', '')
                """, (paper_id, title, result.summary[:500], full_text))
                # 存 reflection_result 到 depth_reviews_v4
                reflection_data = {
                    "paper_id": paper_id,
                    "title": title,
                    "scores": scores,
                    "verdict": verdict,
                    "verdict_reason": result.verdict_reason,
                    "effective_evidence_count": result.effective_evidence_count,
                    "summary": result.summary,
                    "claims": result.claims,
                    "evidence_pool": result.evidence_pool,
                    "evaluated_at": result.evaluated_at,
                }
                cur.execute("""
                    INSERT OR REPLACE INTO depth_reviews_v4 (id, paper_id, kind, final_verdict, reflection_result, created_at)
                    VALUES (?, ?, 'report', ?, ?, ?)
                """, (
                    f"{paper_id}_review", paper_id,
                    json.dumps({"verdict": verdict, "scores": scores}, ensure_ascii=False),
                    json.dumps(reflection_data, ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                ))
                conn.commit()
            except Exception as e:
                print(f"    [DB warn] {e}")
        except Exception as e:
            print(f"    [review fail] {e}")
            import traceback
            traceback.print_exc()

    conn.close()
    return results


def verdict_zh(v: str) -> str:
    m = {
        "well_done": "优秀",
        "needs_evidence": "需补证据",
        "needs_depth": "需加深",
        "rewrite_required": "需重写",
    }
    return m.get(v, v or "")


def step4_output(results):
    """输出分数表格 + 微调建议"""
    print("\n=== 步骤 4: 输出分数表格 ===")
    if not results:
        print("  无结果")
        return

    # CSV
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        w.writeheader()
        w.writerows(results)
    print(f"  CSV: {OUT_CSV} ({OUT_CSV.stat().st_size//1024} KB)")

    # 统计
    n = len(results)
    scores = [r["平均分"] for r in results]
    avg_score = sum(scores) / n
    verdicts = {}
    for r in results:
        v = r["裁决"]
        verdicts[v] = verdicts.get(v, 0) + 1

    print(f"\n  总数: {n}")
    print(f"  平均分: {avg_score:.3f}")
    print(f"  分数范围: {min(scores):.3f} ~ {max(scores):.3f}")
    print(f"  裁决分布: {verdicts}")

    # 微调建议
    print("\n=== 微调建议 ===")
    n_well = verdicts.get("优秀", 0)
    n_ev = verdicts.get("需补证据", 0)
    n_depth = verdicts.get("需加深", 0)
    n_rewrite = verdicts.get("需重写", 0)

    print(f"  优秀(well_done): {n_well}/{n} ({n_well*100//n}%)")
    print(f"  需补证据(needs_evidence): {n_ev}/{n} ({n_ev*100//n}%)")
    print(f"  需加深(needs_depth): {n_depth}/{n} ({n_depth*100//n}%)")
    print(f"  需重写(rewrite_required): {n_rewrite}/{n} ({n_rewrite*100//n}%)")

    # 维度均值
    dim_avg = {
        "理解准确性": sum(r["理解准确性"] for r in results) / n,
        "分析深度": sum(r["分析深度"] for r in results) / n,
        "创新见解": sum(r["创新见解"] for r in results) / n,
        "证据支撑": sum(r["证据支撑"] for r in results) / n,
    }
    print("\n  4 维均值:")
    for k, v in dim_avg.items():
        print(f"    {k}: {v:.3f}")

    # 判断微调方向
    print("\n  --- 算法微调建议 ---")
    if n_well * 100 / n > 70:
        print("  ⚠ 优秀率 >70%，算法偏松，建议:")
        print("    - 提高 AVERAGE_SCORE_POOR (0.50 → 0.60)")
        print("    - 提高 MIN_EVIDENCE_FOR_HIGH_SCORE (4 → 5)")
    elif n_rewrite * 100 / n > 50:
        print("  ⚠ 需重写率 >50%，算法偏严，建议:")
        print("    - 降低 MIN_EVIDENCE_FOR_VALID_REVIEW (2 → 1)")
        print("    - 降低 MAX_SCORE_WHEN_EVIDENCE_INSUFFICIENT (0.3 → 0.4)")
    else:
        print("  ✓ 分布合理，算法无需大幅微调")

    # 维度短板
    min_dim = min(dim_avg, key=dim_avg.get)
    max_dim = max(dim_avg, key=dim_avg.get)
    print(f"\n  最弱维度: {min_dim} ({dim_avg[min_dim]:.3f})")
    print(f"  最强维度: {max_dim} ({dim_avg[max_dim]:.3f})")
    if dim_avg[min_dim] < 0.5:
        print(f"  ⚠ {min_dim}维度均分<0.5，可能是 prompt 没引导 LLM 关注此维度")

    # HTML
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>感悟报告评分 - {n} 篇</title>
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
.card.well .num {{ color: #2e7d32; }}
.card.ev .num {{ color: #f57f17; }}
.card.depth .num {{ color: #e65100; }}
.card.rewrite .num {{ color: #c62828; }}
table {{ width: 100%; border-collapse: collapse; background: white;
        border-radius: 6px; overflow: hidden;
        box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
th, td {{ padding: 8px 6px; text-align: left; border-bottom: 1px solid #eee;
          white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
th {{ background: #1a237e; color: white; font-size: 12px; position: sticky; top: 0; }}
td {{ font-size: 13px; }}
td.title {{ white-space: normal; max-width: 200px; }}
tr.well {{ background: #f1f8e9; }}
tr.ev {{ background: #fff8e1; }}
tr.depth {{ background: #fff3e0; }}
tr.rewrite {{ background: #ffebee; }}
.score {{ font-weight: 700; font-family: monospace; }}
.score-high {{ color: #2e7d32; }}
.score-mid {{ color: #f57f17; }}
.score-low {{ color: #c62828; }}
.footer {{ margin-top: 12px; font-size: 11px; color: #999; text-align: center; }}
@media (max-width: 600px) {{
  th, td {{ font-size: 12px; padding: 6px 4px; }}
  td.title {{ max-width: 120px; }}
  .hide-mobile {{ display: none; }}
}}
</style>
</head>
<body>
<h1>感悟报告评分结果</h1>
<div style="font-size:12px;color:#666;margin-bottom:12px;">
  共 {n} 篇 ｜ 平均分：{avg_score:.3f} ｜ 生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M")}
</div>
<div class="summary">
  <div class="card well"><div class="num">{n_well}</div><div class="lbl">优秀</div></div>
  <div class="card ev"><div class="num">{n_ev}</div><div class="lbl">需补证据</div></div>
  <div class="card depth"><div class="num">{n_depth}</div><div class="lbl">需加深</div></div>
  <div class="card rewrite"><div class="num">{n_rewrite}</div><div class="lbl">需重写</div></div>
</div>
<table>
<thead><tr>
<th>#</th><th>学号</th><th>姓名</th><th>论文题目</th>
<th>理解准确性</th><th>分析深度</th><th>创新见解</th><th>证据支撑</th>
<th>平均分</th><th>裁决</th><th class="hide-mobile">摘要</th>
</tr></thead>
<tbody>
"""
    # 按平均分降序
    for r in sorted(results, key=lambda x: x["平均分"], reverse=True):
        verdict_class = {
            "优秀": "well", "需补证据": "ev", "需加深": "depth", "需重写": "rewrite"
        }.get(r["裁决"], "")
        score_class = "score-high" if r["平均分"] >= 0.7 else (
            "score-mid" if r["平均分"] >= 0.4 else "score-low")
        html += f"""<tr class="{verdict_class}">
<td>{r['序号']}</td>
<td>{r['学号']}</td>
<td>{r['姓名']}</td>
<td class="title">{r['论文题目'] or r['文件名']}</td>
<td class="score">{r['理解准确性']:.3f}</td>
<td class="score">{r['分析深度']:.3f}</td>
<td class="score">{r['创新见解']:.3f}</td>
<td class="score">{r['证据支撑']:.3f}</td>
<td class="score {score_class}">{r['平均分']:.3f}</td>
<td>{r['裁决']}</td>
<td class="hide-mobile">{r['LLM摘要']}</td>
</tr>\n"""

    html += f"""</tbody></table>
<div class="footer">PaperForge DEPTH reflection · {n} 篇 · {datetime.now().strftime('%Y-%m-%d')}</div>
</body></html>"""

    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"  HTML: {OUT_HTML} ({OUT_HTML.stat().st_size//1024} KB)")


def main():
    step1_delete_old_reports()
    n_copied = step2_copy_files()
    if n_copied == 0:
        print("无文件可处理")
        return
    results = step3_parse_and_review()
    step4_output(results)


if __name__ == "__main__":
    main()
