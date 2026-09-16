"""反事实重算：如果修掉三个病根，分数分布会变成什么样。

不需要 LLM：用已落库的 calibrated_score_raw（offset 前）+ q5a 的 critique_points
重放裁决逻辑。

场景：
  S0 现状          ：raw + offset(-0.09) + fatal≥2 一票否决
  S1 修 offset 覆盖：raw + offset(尊重 .env=0) + fatal≥2 一票否决
  S2 S1 + fatal 收紧：把"常见局限"类 fatal 重判为 minor 后再算 fatal_count
  S3 S2 + 裁决门槛  ：fatal≥2 只降级不否决（分数≥0.8 已是 major_revision 档）

输出：各场景的分数分布 + 判决分布 + 高分论文数
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics as st
from collections import Counter
from pathlib import Path

DB = Path(__file__).resolve().parent / "mock_api" / "paperforge_mock.db"
OUT_DIR = Path(__file__).resolve().parent / "deliverables" / "diag"

# ── 裁决阈值（与 mock_api/depth_eval_v4.py 一致）──
VERDICT_ACCEPT_FLOOR = 0.75
VERDICT_MINOR_FLOOR = 0.65
FATAL_VETO_MIN = 2
FATAL_VETO_ACCEPT_FLOOR = 0.9
CAP_THRESHOLD = 0.85
CAP_TAPER = 0.15

# ── "常见局限"型 fatal 的识别模式 ──
# 这些是审稿里最常见的"实验不够充分"，不构成"致命缺陷"
COMMON_LIMITATION_PATTERNS = [
    r"消融", r"ablation",
    r"缺[乏少]|未(与|和|对).{0,25}(对比|比较|验证)|没有(对比|基线)|基线(不足|单一|缺失)|缺乏对比",
    r"敏感性|sensitivity",
    r"理论|证明|bound|上界|下界|形式化",
    r"复现|可复现|细节(不足|缺失)|超参|未(说明|给出|提供|报告|披露)",
    r"数据(集)?(不足|单一|规模)|样本(不足|少)|规模(小|不足)|仅在一个|仅在仿真",
    r"创新(性)?(不足|薄弱|有限)",
    r"假设.{0,10}未|隐含假设|未验证",
    r"表述|写作|语言|排版|格式|夸大",
]
COMMON_RE = re.compile("|".join(COMMON_LIMITATION_PATTERNS), re.I)


def is_common_limitation(point: str) -> bool:
    return bool(COMMON_RE.search(point or ""))


def apply_offset_env_respected(raw: float, source: str | None, year: int | None) -> float:
    """S1 口径：尊重 .env 的 PAPERFORGE_DEPTH_SCORE_OFFSET=0 → 不加偏移。

    保留顶刊封顶函数形态（offset=0 时恒等），故这里直接返回 raw。
    """
    return max(0.0, min(1.0, raw))


def apply_offset_legacy(raw: float, source: str | None, year: int | None) -> float:
    """S0 现状口径：DEFAULT_OFFSET_TABLE 分档偏移 + 顶刊封顶 taper。"""
    src = (source or "").lower().strip()
    off = 0.0 if src == "peerread" else -0.09
    if off >= 0.0:
        return max(0.0, min(1.0, raw + off))
    if raw < CAP_THRESHOLD:
        return max(0.0, min(1.0, raw + off))
    t = max(0.0, min(1.0, (1.0 - raw) / (1.0 - CAP_THRESHOLD)))
    return max(0.0, min(1.0, raw + off * t))


def derive_verdict(score: float, fatal_count: int, llm_verdict: str,
                   fatal_is_veto: bool = True) -> str:
    """复刻 _apply_hard_verdict 的路径覆盖矩阵（简化：不含语义脱耦分支）。"""
    strong_and_accepted = (score >= FATAL_VETO_ACCEPT_FLOOR) and (llm_verdict == "accept")
    if fatal_is_veto and fatal_count >= FATAL_VETO_MIN and not strong_and_accepted:
        return "major_revision" if score >= 0.8 else "reject"
    if score >= VERDICT_ACCEPT_FLOOR:
        align = "accept"
    elif score >= VERDICT_MINOR_FLOOR:
        align = "minor_revision"
    else:
        align = "major_revision"
    if llm_verdict == "reject" or align == "reject":
        return "major_revision"
    return align


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute(
        """
        SELECT r.paper_id, r.final_verdict, r.q5a_result
        FROM depth_reviews_v4 r
        JOIN (
            SELECT paper_id, MAX(created_at) AS mx FROM depth_reviews_v4
            WHERE status='completed' AND version IN ('v4.1','v4.2')
              AND final_verdict IS NOT NULL AND final_verdict != ''
            GROUP BY paper_id
        ) t ON r.paper_id=t.paper_id AND r.created_at=t.mx
        """
    ).fetchall()

    meta = {r["id"]: (r["source"], r["year"])
            for r in cur.execute("SELECT id, source, year FROM papers")}

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
        pts = [c for c in (q5a.get("critique_points") or []) if isinstance(c, dict)]
        fatal_pts = [str(c.get("point") or "") for c in pts if c.get("severity") == "fatal"]
        fatal_common = sum(1 for p in fatal_pts if is_common_limitation(p))
        src, yr = meta.get(r["paper_id"], (None, None))
        raw = fv.get("calibrated_score_raw")
        if raw is None:
            continue
        recs.append({
            "pid": r["paper_id"],
            "raw": raw,
            "source": src,
            "year": yr,
            "fatal": len(fatal_pts),
            "fatal_common": fatal_common,
            "fatal_after_tighten": len(fatal_pts) - fatal_common,
            "llm_verdict": fv.get("llm_verdict") or "major_revision",
        })

    print(f"样本论文数: {len(recs)}")
    tot_fatal = sum(r["fatal"] for r in recs)
    tot_common = sum(r["fatal_common"] for r in recs)
    print(f"fatal 条目总数: {tot_fatal}, 其中'常见局限'型: {tot_common} "
          f"({tot_common/max(1,tot_fatal)*100:.1f}%)")
    veto_now = sum(1 for r in recs if r["fatal"] >= FATAL_VETO_MIN)
    veto_after = sum(1 for r in recs if r["fatal_after_tighten"] >= FATAL_VETO_MIN)
    print(f"触发一票否决的论文: 现状 {veto_now} ({veto_now/len(recs)*100:.1f}%) "
          f"→ 收紧后 {veto_after} ({veto_after/len(recs)*100:.1f}%)")

    scenarios = {}

    def run(label, score_fn, fatal_key, fatal_is_veto):
        finals, verdicts = [], []
        for r in recs:
            s = score_fn(r["raw"], r["source"], r["year"])
            fc = r[fatal_key]
            v = derive_verdict(s, fc, r["llm_verdict"], fatal_is_veto)
            finals.append(s)
            verdicts.append(v)
        scenarios[label] = (finals, verdicts)

    run("S0 现状", apply_offset_legacy, "fatal", True)
    run("S1 修 offset 覆盖", apply_offset_env_respected, "fatal", True)
    run("S2 S1+fatal 收紧", apply_offset_env_respected, "fatal_after_tighten", True)
    run("S3 S2+否决仅降级", apply_offset_env_respected, "fatal_after_tighten", False)

    print("\n" + "=" * 78)
    print(f"{'场景':<20}{'均值':>8}{'中位':>8}{'≥70分':>8}{'≥75分':>8}{'≥80分':>8}")
    print("=" * 78)
    for label, (finals, _) in scenarios.items():
        print(f"{label:<20}{st.mean(finals)*100:>8.1f}{st.median(finals)*100:>8.1f}"
              f"{sum(1 for v in finals if v>=0.7):>8}"
              f"{sum(1 for v in finals if v>=0.75):>8}"
              f"{sum(1 for v in finals if v>=0.8):>8}")

    print("\n" + "=" * 78)
    print("判决分布")
    print("=" * 78)
    verdict_keys = ["accept", "minor_revision", "major_revision", "reject"]
    print(f"{'场景':<20}" + "".join(f"{k:>17}" for k in verdict_keys))
    for label, (_, verdicts) in scenarios.items():
        cnt = Counter(verdicts)
        print(f"{label:<20}" + "".join(
            f"{cnt.get(k,0):>10}({cnt.get(k,0)/len(verdicts)*100:>4.1f}%)" for k in verdict_keys))

    # 落盘
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "score_counterfactual_20260916.json"
    payload = {
        "sample_papers": len(recs),
        "fatal_total": tot_fatal,
        "fatal_common_limitation": tot_common,
        "veto_before": veto_now,
        "veto_after_tighten": veto_after,
        "scenarios": {
            label: {
                "mean": round(st.mean(f), 4),
                "median": round(st.median(f), 4),
                "ge70": sum(1 for v in f if v >= 0.7),
                "ge75": sum(1 for v in f if v >= 0.75),
                "ge80": sum(1 for v in f if v >= 0.8),
                "verdicts": dict(Counter(vd)),
            }
            for label, (f, vd) in scenarios.items()
        },
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已落盘: {out}")


if __name__ == "__main__":
    main()
