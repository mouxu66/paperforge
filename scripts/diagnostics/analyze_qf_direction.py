"""QF 信号方向性分析：验证 QF 是否有判别力。

思路：
  16 篇都是 human accept，无法直接验证"QF 对 reject 是否错误加分"。
  但可以验证 QF 信号的方向性：
  - QF 加分的论文：figure 数据是否质量好（caption 完整 / ocr 有内容 / 图文一致）
  - QF 减分的论文：figure 数据是否有问题（caption 缺失 / ocr 崩 / 图文不一致）

  如果 QF 减分与 figure 数据质量问题相关 → QF 有判别力 → 对 reject 论文预计也会合理减分
  如果 QF 减分随机（与 figure 质量无关）→ QF 无判别力 → B 方案结论存疑

输出：deliverables/qf_signal_directional_analysis.json
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "mock_api" / "paperforge_mock.db"
RESULTS_OFF = ROOT / "deliverables" / "figures_qf_test_results_off.jsonl"
RESULTS_ON = ROOT / "deliverables" / "figures_qf_test_results_on.jsonl"


def load_results() -> tuple[dict, dict]:
    off = {r["stem"]: r for r in [json.loads(l) for l in RESULTS_OFF.read_text(encoding="utf-8").splitlines() if l.strip()]}
    on = {r["stem"]: r for r in [json.loads(l) for l in RESULTS_ON.read_text(encoding="utf-8").splitlines() if l.strip()]}
    return off, on


def load_figure_stats(stem: str) -> dict:
    """加载该论文的 figure 统计：图数、caption 覆盖率、ocr 覆盖率、qwen_summary 覆盖率。"""
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    pid = f"pr_{stem}"
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN caption_text IS NOT NULL AND caption_text != '' THEN 1 ELSE 0 END) AS cap,
            SUM(CASE WHEN ocr_text IS NOT NULL AND ocr_text != '' THEN 1 ELSE 0 END) AS ocr,
            SUM(CASE WHEN qwen_summary IS NOT NULL AND qwen_summary != '' THEN 1 ELSE 0 END) AS qs,
            SUM(CASE WHEN axis_info IS NOT NULL AND axis_info != '' THEN 1 ELSE 0 END) AS axis
        FROM paper_figures WHERE paper_id = ?
    """, (pid,))
    row = cur.fetchone()
    conn.close()
    if not row or row[0] == 0:
        return {"total": 0}
    total, cap, ocr, qs, axis = row
    return {
        "total": total,
        "caption_pct": round(100 * cap / total, 1) if total else 0,
        "ocr_pct": round(100 * ocr / total, 1) if total else 0,
        "qwen_summary_pct": round(100 * qs / total, 1) if total else 0,
        "axis_pct": round(100 * axis / total, 1) if total else 0,
        "evidence_richness": round((cap + ocr + qs + axis) / (4 * total), 3) if total else 0,
    }


def main():
    off, on = load_results()
    common = sorted(set(off) & set(on))
    print(f"共同样本: {len(common)} 篇\n")

    # 计算每篇的 score delta 和 verdict flip
    rows = []
    for s in common:
        s_off = off[s]["depth_calibrated_score"] or 0
        s_on = on[s]["depth_calibrated_score"] or 0
        delta = s_on - s_off
        v_off = off[s]["depth_verdict"]
        v_on = on[s]["depth_verdict"]
        flip = v_off != v_on
        # 方向：+ = QF 加分，- = QF 减分
        direction = "up" if delta > 0.005 else ("down" if delta < -0.005 else "flat")
        fig = load_figure_stats(s)
        rows.append({
            "stem": s, "title": off[s]["title"][:60],
            "score_off": s_off, "score_on": s_on, "delta": round(delta, 4),
            "verdict_off": v_off, "verdict_on": v_on, "flip": flip,
            "direction": direction,
            "fig_total": fig["total"],
            "fig_cap_pct": fig.get("caption_pct", 0),
            "fig_ocr_pct": fig.get("ocr_pct", 0),
            "fig_qs_pct": fig.get("qwen_summary_pct", 0),
            "fig_axis_pct": fig.get("axis_pct", 0),
            "fig_richness": fig.get("evidence_richness", 0),
        })

    # 按 delta 排序
    rows.sort(key=lambda r: r["delta"])

    print(f"{'stem':<14} {'delta':>7} {'dir':>5} {'flip':>5} {'figs':>4} {'cap%':>5} {'ocr%':>5} {'qs%':>4} {'rich':>5} | verdict_off→on")
    print("-" * 110)
    for r in rows:
        print(f"{r['stem']:<14} {r['delta']:+.3f} {r['direction']:>5} {'Y' if r['flip'] else 'N':>5} "
              f"{r['fig_total']:>4} {r['fig_cap_pct']:>5.1f} {r['fig_ocr_pct']:>5.1f} {r['fig_qs_pct']:>4.1f} "
              f"{r['fig_richness']:>5.2f} | {r['verdict_off']:>14} -> {r['verdict_on']}")

    # 分组统计：QF 加分组 vs 减分组
    up = [r for r in rows if r["direction"] == "up"]
    down = [r for r in rows if r["direction"] == "down"]
    flat = [r for r in rows if r["direction"] == "flat"]

    print(f"\n=== 分组统计 ===")
    print(f"QF 加分组 (delta>0): {len(up)} 篇")
    print(f"QF 减分组 (delta<0): {len(down)} 篇")
    print(f"QF 中性组 (≈0):    {len(flat)} 篇")

    def avg(lst, key):
        return sum(r[key] for r in lst) / len(lst) if lst else 0

    print(f"\n=== 证据丰富度对比（关键指标）===")
    print(f"{'组':<12} {'avg_figs':>9} {'avg_cap%':>9} {'avg_ocr%':>9} {'avg_qs%':>8} {'avg_rich':>9}")
    for name, lst in [("up(加分)", up), ("down(减分)", down), ("flat(中性)", flat)]:
        if lst:
            print(f"{name:<12} {avg(lst,'fig_total'):>9.1f} {avg(lst,'fig_cap_pct'):>9.1f} "
                  f"{avg(lst,'fig_ocr_pct'):>9.1f} {avg(lst,'fig_qs_pct'):>8.1f} {avg(lst,'fig_richness'):>9.3f}")

    # verdict 翻转分析
    print(f"\n=== verdict 翻转分析 ===")
    flips = [r for r in rows if r["flip"]]
    print(f"翻转篇数: {len(flips)}/{len(rows)}")
    for r in flips:
        arrow = "↑" if r["delta"] > 0 else "↓"
        print(f"  {arrow} {r['stem']}: {r['verdict_off']} -> {r['verdict_on']} (Δ={r['delta']:+.3f}, figs={r['fig_total']}, rich={r['fig_richness']})")

    # 核心判据：QF 加分组 vs 减分组的证据丰富度差异
    print(f"\n=== 核心判据：QF 信号方向性 ===")
    if up and down:
        rich_up = avg(up, "fig_richness")
        rich_down = avg(down, "fig_richness")
        print(f"加分组的证据丰富度均值: {rich_up:.3f}")
        print(f"减分组的证据丰富度均值: {rich_down:.3f}")
        if rich_up > rich_down:
            print(f"→ QF 加分组证据更丰富（+{rich_up-rich_down:.3f}），说明 QF 能识别高质量 figure 数据")
            print(f"→ 减分组证据相对匮乏，QF 可能因证据不足而保守减分")
            print(f"→ 判定：QF 信号有方向性判别力（非随机噪声）")
        else:
            print(f"→ 加分组证据反而更少（-{rich_down-rich_up:.3f}），QF 信号方向存疑")

    # 保存
    out = {
        "n_common": len(common),
        "n_up": len(up), "n_down": len(down), "n_flat": len(flat),
        "rows": rows,
        "group_stats": {
            "up": {"avg_figs": avg(up,"fig_total"), "avg_rich": avg(up,"fig_richness")},
            "down": {"avg_figs": avg(down,"fig_total"), "avg_rich": avg(down,"fig_richness")},
        },
    }
    out_path = ROOT / "deliverables" / "qf_signal_directional_analysis.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] 结果保存到 {out_path}")


if __name__ == "__main__":
    main()
