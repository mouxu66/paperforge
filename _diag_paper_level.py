"""论文级（卡片口径）分数分布诊断。

卡片显示口径 = 每篇论文最新一条 completed v4 评审的 calibrated_score * 100。
本脚本回答：
  1. 用户实际在卡片上看到的分数分布
  2. 下压三段的贡献量（维度加权 → Q5c delta → 全局 offset）
  3. 哪些论文"分数不低却被拒"（fatal veto 占比）
  4. 各语料来源（source）的分数差异
"""
from __future__ import annotations

import json
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

DB = Path(__file__).resolve().parent / "mock_api" / "paperforge_mock.db"


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 每篇论文最新一条 completed v4 记录
    rows = cur.execute(
        """
        SELECT r.paper_id, r.final_verdict, r.q2_result, r.q5a_result, r.created_at
        FROM depth_reviews_v4 r
        JOIN (
            SELECT paper_id, MAX(created_at) AS mx
            FROM depth_reviews_v4
            WHERE status='completed' AND version IN ('v4.1','v4.2')
              AND final_verdict IS NOT NULL AND final_verdict != ''
            GROUP BY paper_id
        ) t ON r.paper_id = t.paper_id AND r.created_at = t.mx
        WHERE r.status='completed' AND r.version IN ('v4.1','v4.2')
        """
    ).fetchall()

    print(f"有卡片分数的论文数: {len(rows)}")

    # 论文元数据
    meta = {}
    for r in cur.execute("SELECT id, source, year, title FROM papers"):
        meta[r["id"]] = {"source": r["source"], "year": r["year"], "title": r["title"]}

    total_papers = cur.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    print(f"库内论文总数: {total_papers}  → 无分数: {total_papers - len(rows)}")

    recs = []
    for r in rows:
        try:
            fv = json.loads(r["final_verdict"])
        except Exception:
            continue
        q5a = r["q5a_result"] or {}
        try:
            q5a = json.loads(q5a) if isinstance(q5a, str) else q5a
        except Exception:
            q5a = {}
        pts = q5a.get("critique_points") or []
        fatal = sum(1 for c in pts if isinstance(c, dict) and c.get("severity") == "fatal")
        m = meta.get(r["paper_id"], {})
        recs.append({
            "pid": r["paper_id"],
            "final": fv.get("calibrated_score"),
            "raw": fv.get("calibrated_score_raw"),
            "base": fv.get("base_score"),
            "verdict": fv.get("final_verdict"),
            "fatal": fatal,
            "source": m.get("source"),
            "year": m.get("year"),
            "title": m.get("title"),
        })

    vals = [x["final"] for x in recs if isinstance(x["final"], (int, float))]
    print("\n===== 卡片显示分（0-100）分布 =====")
    print(f"mean={st.mean(vals)*100:.1f} median={st.median(vals)*100:.1f} "
          f"min={min(vals)*100:.1f} max={max(vals)*100:.1f}")
    buckets = Counter()
    for v in vals:
        s = v * 100
        lo = int(s // 10) * 10
        buckets[lo] += 1
    for lo in sorted(buckets):
        n = buckets[lo]
        print(f"  {lo:>3}-{lo+9:<3}分 : {n:>4}  {'#' * min(60, n)}")

    print("\n===== 判决分布 =====")
    for k, v in Counter(x["verdict"] for x in recs).most_common():
        print(f"  {k:<18} {v:>4}  ({v/len(recs)*100:.1f}%)")

    print("\n===== 致命缺陷数分布 =====")
    for k, v in sorted(Counter(x["fatal"] for x in recs).items()):
        print(f"  fatal={k} : {v:>4}  ({v/len(recs)*100:.1f}%)")

    # 分数不低却被拒
    hi_reject = [x for x in recs
                 if isinstance(x["final"], (int, float)) and x["final"] >= 0.6
                 and x["verdict"] == "reject"]
    print(f"\n===== 分数≥60 但被判 reject: {len(hi_reject)} 篇 =====")
    for x in sorted(hi_reject, key=lambda y: -y["final"])[:20]:
        print(f"  {x['pid'][:40]:<42} {x['final']*100:5.1f}分 fatal={x['fatal']} "
              f"{(x['title'] or '')[:34]}")

    # 三段下压贡献
    print("\n===== 三段下压贡献（均值）=====")
    b = [x["base"] for x in recs if isinstance(x["base"], (int, float))]
    rw = [x["raw"] for x in recs if isinstance(x["raw"], (int, float))]
    fi = [x["final"] for x in recs if isinstance(x["final"], (int, float))]
    print(f"  ① 维度加权 base_score          : {st.mean(b):.4f}  (={st.mean(b)*100:.1f}分)")
    print(f"  ② +Q5c delta → raw             : {st.mean(rw):.4f}  (Δ={st.mean(rw)-st.mean(b):+.4f})")
    print(f"  ③ +全局 offset → final         : {st.mean(fi):.4f}  (Δ={st.mean(fi)-st.mean(rw):+.4f})")
    print(f"  总损失: {st.mean(fi)-st.mean(b):+.4f}  (={abs(st.mean(fi)-st.mean(b))*100:.1f}分)")

    # 按 source 分组
    print("\n===== 按 source 分组（top）=====")
    g = defaultdict(list)
    for x in recs:
        if isinstance(x["final"], (int, float)):
            g[x["source"] or "(null)"].append(x["final"])
    for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))[:12]:
        print(f"  {str(k):<22} n={len(v):>4} mean={st.mean(v)*100:5.1f}分 "
              f"median={st.median(v)*100:5.1f}分")

    # 阈值可达性
    print("\n===== 阈值可达性检查 =====")
    accept_floor = 0.75
    n_accept_band = sum(1 for v in vals if v >= accept_floor)
    print(f"  accept 档下界 VERDICT_ACCEPT_FLOOR = {accept_floor}（{accept_floor*100:.0f}分）")
    print(f"  达到该阈值的论文: {n_accept_band} / {len(vals)} ({n_accept_band/len(vals)*100:.1f}%)")
    print(f"  raw（offset 前）达到该阈值的: "
          f"{sum(1 for x in recs if isinstance(x['raw'],(int,float)) and x['raw']>=accept_floor)}")
    print(f"  base（维度层）达到该阈值的: "
          f"{sum(1 for x in recs if isinstance(x['base'],(int,float)) and x['base']>=accept_floor)}")


if __name__ == "__main__":
    main()
