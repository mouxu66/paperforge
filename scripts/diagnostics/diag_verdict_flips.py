"""诊断 36 篇 figures-ON vs OFF verdict 翻转情况。

分类：
  - ON=correct → OFF=correct: 都对（verdict 改变但都对或都错改对）
  - ON=correct → OFF=wrong:   ON 对，OFF 错（figure 证据有帮助）
  - ON=wrong   → OFF=correct: ON 错，OFF 对（figure 证据有害）
  - ON=wrong   → OFF=wrong:   都错（verdict 改变但都没救回来）

并分析翻转方向（accept↔reject）和 score 偏移模式。
"""
from __future__ import annotations
import json
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS_OFF = ROOT / "deliverables" / "figures_qf_test_results_off.jsonl"
RESULTS_ON = ROOT / "deliverables" / "figures_qf_test_results_on.jsonl"

def verdict_to_accept_int(v: str | None, tier: str = "loose") -> int:
    """loose: accept/minor 视为 accept；strict: 仅 accept"""
    if not v:
        return 0
    v = v.replace("_revision", "")
    if tier == "strict":
        return 1 if v == "accept" else 0
    return 1 if v in ("accept", "minor") else 0

def load(path: Path) -> dict[str, dict]:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return {r["stem"]: r for r in rows}

def main():
    off = load(RESULTS_OFF)
    on = load(RESULTS_ON)
    common = sorted(set(off) & set(on))
    print(f"=== 36 篇 verdict 翻转诊断（loose 口径 accept/minor=accept）===")
    print(f"OFF: {len(off)} 篇, ON: {len(on)} 篇, 共同: {len(common)} 篇\n")

    # 找翻转的篇
    flips = []
    no_flips = []
    for s in common:
        v_off = off[s]["depth_verdict"]
        v_on = on[s]["depth_verdict"]
        human = off[s]["human_accepted"]
        s_off = off[s]["depth_calibrated_score"] or 0
        s_on = on[s]["depth_calibrated_score"] or 0
        a_off = verdict_to_accept_int(v_off, "loose")
        a_on = verdict_to_accept_int(v_on, "loose")
        d = s_on - s_off

        if a_off != a_on:
            flips.append({
                "stem": s, "human": human,
                "v_off": v_off, "v_on": v_on,
                "a_off": a_off, "a_on": a_on,
                "s_off": s_off, "s_on": s_on, "d": d,
                "title": off[s].get("title", "")[:60],
            })
        else:
            no_flips.append({"stem": s, "human": human, "v_off": v_off, "v_on": v_on, "s_off": s_off, "s_on": s_on, "d": d})

    print(f"verdict 翻转: {len(flips)}/{len(common)} 篇")
    print(f"verdict 未翻转: {len(no_flips)}/{len(common)} 篇\n")

    # 分类 4 类
    print("=== ① 翻转分类（按 human ground truth）===\n")
    cats = {
        "ON对→OFF错(figure有害)": [],
        "ON错→OFF对(figure救回)": [],
        "都对(verdict改但都对)": [],
        "都错(verdict改但都错)": [],
    }
    for f in flips:
        on_correct = (f["a_on"] == 1) == f["human"]
        off_correct = (f["a_off"] == 1) == f["human"]
        if on_correct and not off_correct:
            cats["ON对→OFF错(figure有害)"].append(f)
        elif not on_correct and off_correct:
            cats["ON错→OFF对(figure救回)"].append(f)
        elif on_correct and off_correct:
            cats["都对(verdict改但都对)"].append(f)
        else:
            cats["都错(verdict改但都错)"].append(f)

    for cat, items in cats.items():
        print(f"  [{cat}] {len(items)} 篇")
        for f in items:
            human_s = "accept" if f["human"] else "reject"
            print(f"    {f['stem']:<14} human={human_s:<6} "
                  f"ON:{f['v_on']:<18}({f['s_on']:.3f}) → "
                  f"OFF:{f['v_off']:<18}({f['s_off']:.3f}) "
                  f"Δ={f['d']:+.3f}")
        print()

    # 翻转方向分析
    print("=== ② 翻转方向分析 ===\n")
    dir_cnt = Counter()
    for f in flips:
        # a_on → a_off
        if f["a_on"] == 1 and f["a_off"] == 0:
            dir_cnt["ON=accept→OFF=reject（OFF 收紧）"] += 1
        elif f["a_on"] == 0 and f["a_off"] == 1:
            dir_cnt["ON=reject→OFF=accept（OFF 放宽）"] += 1
    for k, v in dir_cnt.items():
        print(f"  {k}: {v} 篇")
    print()

    # score 偏移模式
    print("=== ③ score 偏移模式（ON - OFF）===\n")
    deltas = [f["d"] for f in flips]
    pos = [d for d in deltas if d > 0.01]
    neg = [d for d in deltas if d < -0.01]
    zero = [d for d in deltas if abs(d) <= 0.01]
    print(f"  正偏移（ON 加分）: {len(pos)} 篇，均值 Δ={sum(pos)/len(pos):+.3f}" if pos else "  正偏移: 0 篇")
    print(f"  负偏移（ON 减分）: {len(neg)} 篇，均值 Δ={sum(neg)/len(neg):+.3f}" if neg else "  负偏移: 0 篇")
    print(f"  无偏移: {len(zero)} 篇")
    print()

    # 按 human 分组看偏移
    print("  按 human 分组:")
    for h in [True, False]:
        grp = [f for f in flips if f["human"] == h]
        if grp:
            ds = [f["d"] for f in grp]
            avg = sum(ds) / len(ds)
            label = "accept" if h else "reject"
            print(f"    human={label}: {len(grp)} 篇，avg Δ={avg:+.3f}，"
                  f"pos={sum(1 for d in ds if d>0.01)}/{len(grp)}，"
                  f"neg={sum(1 for d in ds if d<-0.01)}/{len(grp)}")
    print()

    # 未翻转篇的 score 变化
    print("=== ④ 未翻转篇的 score 变化（verdict 不变但 score 改了）===\n")
    no_flip_changed = [n for n in no_flips if abs(n["d"]) > 0.01]
    print(f"  verdict 未翻转: {len(no_flips)} 篇，其中 score 改变: {len(no_flip_changed)} 篇")
    if no_flip_changed:
        ds = [n["d"] for n in no_flip_changed]
        print(f"  score 改变的均值 Δ={sum(ds)/len(ds):+.3f}")
        print(f"  正偏移: {sum(1 for d in ds if d>0.01)}，负偏移: {sum(1 for d in ds if d<-0.01)}")
    print()

    # 系统性 vs 随机判断
    print("=== ⑤ 系统性偏差 vs 随机扰动判断 ===\n")
    # 统计：figure 证据是系统性加分还是减分？
    all_deltas = [f["d"] for f in flips] + [n["d"] for n in no_flips]
    avg_all = sum(all_deltas) / len(all_deltas) if all_deltas else 0
    pos_all = sum(1 for d in all_deltas if d > 0.01)
    neg_all = sum(1 for d in all_deltas if d < -0.01)
    zero_all = sum(1 for d in all_deltas if abs(d) <= 0.01)
    print(f"  全部 {len(all_deltas)} 篇 score 偏移:")
    print(f"    avg Δ = {avg_all:+.4f}")
    print(f"    正偏移（ON 加分）: {pos_all} 篇 ({100*pos_all//len(all_deltas)}%)")
    print(f"    负偏移（ON 减分）: {neg_all} 篇 ({100*neg_all//len(all_deltas)}%)")
    print(f"    无偏移: {zero_all} 篇 ({100*zero_all//len(all_deltas)}%)")
    print()
    # 如果正负偏移接近 1:1 → 随机扰动；如果严重偏向一边 → 系统性偏差
    ratio = pos_all / max(neg_all, 1)
    print(f"  正负比: {pos_all}:{neg_all} = {ratio:.2f}")
    if ratio > 1.5:
        print(f"  → 偏向加分（系统性正向偏差），figure 证据整体抬高分数")
    elif ratio < 0.67:
        print(f"  → 偏向减分（系统性负向偏差），figure 证据整体压低分数")
    else:
        print(f"  → 正负接近 1:1，更像随机扰动而非系统性偏差")
    print()

    # 按 accept/reject 看翻转正确率
    print("=== ⑥ 按 human 分组看翻转效果 ===\n")
    for h in [True, False]:
        label = "accept" if h else "reject"
        grp = [f for f in flips if f["human"] == h]
        if not grp:
            continue
        on_correct = sum(1 for f in grp if (f["a_on"] == 1) == f["human"])
        off_correct = sum(1 for f in grp if (f["a_off"] == 1) == f["human"])
        print(f"  human={label}: {len(grp)} 篇翻转")
        print(f"    ON 正确: {on_correct}/{len(grp)}")
        print(f"    OFF 正确: {off_correct}/{len(grp)}")
        # 方向
        to_accept = sum(1 for f in grp if f["a_off"] == 0 and f["a_on"] == 1)
        to_reject = sum(1 for f in grp if f["a_off"] == 1 and f["a_on"] == 0)
        print(f"    ON→accept: {to_accept}，ON→reject: {to_reject}")
        print()


if __name__ == "__main__":
    main()
