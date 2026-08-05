"""B 方案测试：figures-ON vs figures-OFF LLM QF 路径对比。

对已有 PaperFigure 证据的 16 篇 PeerRead 论文跑两次 DEPTH v4：
  - figures-OFF（DEPTH_FIGURE_EVIDENCE_ENABLED=false）：QF 节点走 P0-B 文本层 / 中性
  - figures-ON （DEPTH_FIGURE_EVIDENCE_ENABLED=true）：QF 节点消费已有图证据 + LLM 评分

两次都用生产 offset 配置（peerread=0.18 + default=-0.09）+ cap 0.80，
区别只在 figures-ON 开关。每篇产出 verdict/score/qf，最后算 2 档 κ 看收益。

输出：deliverables/figures_qf_test_results.jsonl

用法：
    python scripts/figures_qf_test.py              # 续跑
    python scripts/figures_qf_test.py --fresh      # 重置两个结果文件重跑
    python scripts/figures_qf_test.py --calibrate  # 只读已有结果算 κ
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# 关键开关（必须在 import depth_eval_v4 之前落地）
# 1) 禁用 bulk DEPTH 自动触发 figure_understanding（VRAM 互斥会 segfault）
os.environ.setdefault("PAPERFORGE_DISABLE_FIGURE_TRIGGER", "1")
# 2) 温度固定 greedy：确定性评分（QF 信号差异才不被 run-to-run 方差淹没）
os.environ.setdefault("PAPERFORGE_LLM_TEMPERATURE", "0")
# 3) 保持 QF 节点在 DAG 里（DEPTH_QF_NODE_ENABLED=true）
os.environ.setdefault("PAPERFORGE_DEPTH_QF_NODE_ENABLED", "true")
# 4) QF 微调权重 w_fig=0（默认关闭，9B 模型 LLM 随机性淹没 QF 微调）
os.environ.setdefault("PAPERFORGE_DEPTH_FIGURE_WEIGHT", "0.0")

# 默认生产 offset（不 baseline，保持现状）
DEFAULT_OFFSET_TABLE = '{"default":-0.09,"peerread":0.18}'
DEFAULT_SCORE_OFFSET = "-0.09"
DEFAULT_CAP_THRESHOLD = "0.80"
DEFAULT_CAP_TAPER = "0.15"

DB_PATH = ROOT / "mock_api" / "paperforge_mock.db"
PEERREAD_IDX = ROOT / "deliverables" / "peerread_index.json"
RESULTS_OFF = ROOT / "deliverables" / "figures_qf_test_results_off.jsonl"
RESULTS_ON = ROOT / "deliverables" / "figures_qf_test_results_on.jsonl"


def list_fig_stems() -> list[str]:
    """从 PaperFigure 表取所有 pr_ 前缀论文的 stem。"""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT paper_id FROM paper_figures WHERE paper_id LIKE 'pr_%'")
    stems = [r[0][3:] for r in cur.fetchall()]
    conn.close()
    return sorted(stems)


def load_target_papers() -> list[dict]:
    """合并 PaperFigure 表 + peerread_index.json，得到有图证据 + 有金标的论文。"""
    fig_stems = set(list_fig_stems())
    idx = json.loads(PEERREAD_IDX.read_text(encoding="utf-8"))
    by_stem = {j["stem"]: j for j in idx}
    rows = []
    missing = []
    for s in sorted(fig_stems):
        if s in by_stem:
            rows.append(by_stem[s])
        else:
            missing.append(s)
    if missing:
        print(f"[warn] {len(missing)} 篇在 peerread_index.json 找不到金标: {missing}")
    return rows


def extract_full_text(parsed_path: str) -> tuple[str, str]:
    o = json.loads(Path(parsed_path).read_text(encoding="utf-8"))
    md = o.get("metadata", o)
    sections = md.get("sections", []) or []
    parts = []
    for s in sections:
        h = s.get("heading", "") or ""
        t = s.get("text", "") or ""
        if h:
            parts.append(f"\n## {h}\n")
        if t:
            parts.append(t)
    full = "\n".join(parts).strip()
    abstract = (md.get("abstractText") or "").strip()
    return full, abstract


def cohen_kappa_2tier(y_pred: list[int], y_true: list[int]) -> float:
    n = len(y_pred)
    if n == 0:
        return float("nan")
    po = sum(1 for p, t in zip(y_pred, y_true) if p == t) / n
    pa = sum(y_pred) / n
    pb = sum(y_true) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1.0:
        return 1.0 if po == 1.0 else 0.0
    return (po - pe) / (1 - pe)


def confusion(y_pred: list[int], y_true: list[int]) -> dict:
    tp = sum(1 for p, t in zip(y_pred, y_true) if p == 1 and t == 1)
    fn = sum(1 for p, t in zip(y_pred, y_true) if p == 1 and t == 0)
    fp = sum(1 for p, t in zip(y_pred, y_true) if p == 0 and t == 1)
    tn = sum(1 for p, t in zip(y_pred, y_true) if p == 0 and t == 0)
    n = len(y_pred)
    return {
        "n": n, "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "accuracy": round((tp + tn) / n, 4) if n else 0,
        "precision": round(tp / (tp + fp), 4) if (tp + fp) else 0,
        "recall": round(tp / (tp + fn), 4) if (tp + fn) else 0,
        "f1": round(2 * tp / (2 * tp + fp + fn), 4) if (2 * tp + fp + fn) else 0,
    }


def verdict_to_accept_int(verdict: str | None, threshold_tier: str = "strict") -> int:
    """strict: 仅 accept 视为 accept；loose: accept/minor 视为 accept。"""
    if not verdict:
        return 0
    v = verdict.replace("_revision", "")
    if threshold_tier == "strict":
        return 1 if v == "accept" else 0
    return 1 if v in ("accept", "minor") else 0


def run_one_mode(
    mode: str,  # "off" | "on"
    papers: list[dict],
    out_path: Path,
    fresh: bool,
    samples: int = 1,
) -> None:
    """对每个论文跑 N 次 DEPTH 评审，落库到 out_path。

    samples > 1 时，每篇论文跑 N 次，每条记录带 sample_id 字段。
    用于 Self-Consistency：多数票 + 一致性置信度。
    """
    if fresh and out_path.exists():
        out_path.unlink()

    # 关键开关：在 import depth_eval_v4 之前切 figures-ON/OFF
    os.environ["PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED"] = "true" if mode == "on" else "false"
    os.environ["DEPTH_FIGURE_EVIDENCE_ENABLED"] = "true" if mode == "on" else "false"
    os.environ["PAPERFORGE_DEPTH_OFFSET_TABLE"] = DEFAULT_OFFSET_TABLE
    os.environ["PAPERFORGE_DEPTH_SCORE_OFFSET"] = DEFAULT_SCORE_OFFSET
    os.environ["PAPERFORGE_DEPTH_CAP_THRESHOLD"] = DEFAULT_CAP_THRESHOLD
    os.environ["PAPERFORGE_DEPTH_CAP_TAPER"] = DEFAULT_CAP_TAPER

    # 延迟 import：保证 env 先落地
    from mock_api.depth_eval_v4 import DepthReviewer  # noqa: E402

    # done 改成 (stem, sample_id) 元组，支持多次采样续跑
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    done.add((r["stem"], r.get("sample_id", 0)))
                except Exception:
                    pass

    reviewer = DepthReviewer(compute_mode="deep")
    out = open(out_path, "a", encoding="utf-8")
    total = len(papers)
    print(f"\n=== [{mode}] figures-{'ON' if mode=='on' else 'OFF'} 模式启动 ===")
    print(f"  DEPTH_FIGURE_EVIDENCE_ENABLED={os.environ['PAPERFORGE_DEPTH_FIGURE_EVIDENCE_ENABLED']}")
    print(f"  待跑: {total} 篇 × {samples} 次 = {total * samples} 个采样")
    print(f"  已完成: {len(done)} 个采样")

    for i, j in enumerate(papers, 1):
        stem = j["stem"]
        try:
            full, abstract = extract_full_text(j["parsed_path"])
        except Exception as e:
            print(f"  [{i}/{total}] {stem} 全文提取失败: {e}")
            continue
        if len(full) < 1500:
            print(f"  [{i}/{total}] {stem} 全文过短({len(full)})，跳过")
            continue
        for sid in range(samples):
            if (stem, sid) in done:
                print(f"  [{i}/{total}] skip {stem}#{sid} (已跑)")
                continue
            try:
                paper_meta = {"source": "peerread", "year": j.get("year")}
                result = asyncio.run(reviewer.review_async_dag(
                    paper_id=f"pr_{stem}", title=j["title"] or "", full_text=full,
                    abstract=abstract, paper_meta=paper_meta,
                ))
                rd = result.model_dump()
                rec = {
                    "stem": stem, "title": j["title"], "venue": j["venue"],
                    "human_accepted": j["accepted"], "full_text_len": len(full),
                    "mode": mode, "sample_id": sid,
                    "depth_calibrated_score": rd.get("calibrated_score"),
                    "depth_base_score": rd.get("base_score"),
                    "depth_delta": rd.get("delta"),
                    "depth_verdict": rd.get("final_verdict"),
                    "novelty": rd.get("novelty_score"),
                    "rigor": rd.get("rigor_score"),
                    "influence": rd.get("influence_score"),
                    "reproducibility": rd.get("reproducibility_score"),
                    "objectivity": rd.get("objectivity_score"),
                }
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                out.flush()
                print(f"  [{i}/{total}] {stem}#{sid} score={rec['depth_calibrated_score']} "
                      f"verdict={rec['depth_verdict']} human_accept={j['accepted']}")
            except Exception as e:
                print(f"  [{i}/{total}] {stem}#{sid} DEPTH 失败: {e}")
    out.close()
    print(f"  [{mode}] 完成，结果写入 {out_path}")


def calibrate() -> None:
    """读两个结果文件，对比 κ / 混淆矩阵 / score 差异。"""
    rows_off = [json.loads(l) for l in RESULTS_OFF.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows_on = [json.loads(l) for l in RESULTS_ON.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_stem_off = {r["stem"]: r for r in rows_off}
    by_stem_on = {r["stem"]: r for r in rows_on}

    common = sorted(set(by_stem_off) & set(by_stem_on))
    print(f"\n=== figures-ON vs figures-OFF 对比 ===")
    print(f"  OFF 完成 {len(rows_off)} 篇, ON 完成 {len(rows_on)} 篇, 共同 {len(common)} 篇")

    if not common:
        print("  无共同样本，无法对比")
        return

    y_true = [1 if by_stem_off[s]["human_accepted"] else 0 for s in common]

    for tier in ("strict", "loose"):
        print(f"\n  --- {tier} 口径 ---")
        for mode, by in (("OFF", by_stem_off), ("ON", by_stem_on)):
            y_pred = [verdict_to_accept_int(by[s]["depth_verdict"], tier) for s in common]
            kappa = cohen_kappa_2tier(y_pred, y_true)
            cm = confusion(y_pred, y_true)
            print(f"  [{mode}] κ={kappa:+.4f}  acc={cm['accuracy']}  "
                  f"TP={cm['tp']} FN={cm['fn']} FP={cm['fp']} TN={cm['tn']}  "
                  f"prec={cm['precision']} rec={cm['recall']} F1={cm['f1']}")

    # verdict 分布对比
    print("\n  --- verdict 分布 ---")
    for mode, by in (("OFF", by_stem_off), ("ON", by_stem_on)):
        dist = Counter(by[s]["depth_verdict"] for s in common)
        print(f"  [{mode}] {dict(dist)}")

    # score 差异（看 figures-ON 把哪些篇评分拉偏）
    print("\n  --- 每篇 score 差异（ON - OFF） ---")
    diffs = []
    for s in common:
        s_off = by_stem_off[s]["depth_calibrated_score"] or 0
        s_on = by_stem_on[s]["depth_calibrated_score"] or 0
        d = s_on - s_off
        diffs.append((s, s_off, s_on, d, by_stem_off[s]["depth_verdict"], by_stem_on[s]["depth_verdict"]))
        if abs(d) > 1e-4:
            print(f"  {s}: OFF={s_off:.3f}({by_stem_off[s]['depth_verdict']}) -> "
                  f"ON={s_on:.3f}({by_stem_on[s]['depth_verdict']}) Δ={d:+.3f}")
    n_changed = sum(1 for d in diffs if abs(d[3]) > 1e-4)
    n_verdict_flip = sum(1 for d in diffs if d[4] != d[5])
    print(f"\n  score 改变篇数: {n_changed}/{len(common)}")
    print(f"  verdict 翻转篇数: {n_verdict_flip}/{len(common)}")


def calibrate_consensus() -> None:
    """Self-Consistency 校准：每篇论文多次采样，多数票 + 一致性置信度。

    读取 RESULTS_ON，按 stem 分组所有 sample_id，对每篇论文：
    - verdict 多数票（2档：accept vs non-accept）
    - score 中位数
    - 一致性 = max_count / total_samples（1.0 = 全一致，0.6 = 3/5）

    输出：
    - 每篇论文的 consensus_verdict / consensus_score / confidence
    - 整体 acc / κ / FP / FN（基于 consensus_verdict）
    - 按置信度分层统计（高/中/低）
    """
    from statistics import median

    if not RESULTS_ON.exists():
        print("[consensus] 无结果文件")
        return

    rows = [json.loads(l) for l in RESULTS_ON.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_stem: dict[str, list[dict]] = {}
    for r in rows:
        by_stem.setdefault(r["stem"], []).append(r)

    # 单次模式（samples=1）直接走旧 calibrate
    max_samples = max(len(v) for v in by_stem.values()) if by_stem else 1
    if max_samples <= 1:
        print(f"[consensus] 检测到单次采样（max_samples={max_samples}），走旧 calibrate")
        calibrate()
        return

    print(f"\n=== Self-Consistency 校准 ===")
    print(f"  论文数: {len(by_stem)}，最大采样数: {max_samples}")

    # 按论文算 consensus
    consensus_rows = []
    print(f"\n{'stem':<14} {'H':>3} | {'samples':>7} {'acc_cnt':>7} {'cons_v':<14} {'med_score':>10} {'conf':>6} | {'all_verdicts'}")
    print("-" * 110)
    for stem in sorted(by_stem):
        samples = by_stem[stem]
        n = len(samples)
        human = samples[0].get("human_accepted")
        verdicts = [s["depth_verdict"] for s in samples]
        scores = [s["depth_calibrated_score"] or 0 for s in samples]

        # 2档多数票：accept vs non-accept
        accept_cnt = sum(1 for v in verdicts if v == "accept")
        non_accept_cnt = n - accept_cnt
        consensus_verdict = "accept" if accept_cnt > non_accept_cnt else "reject"
        confidence = max(accept_cnt, non_accept_cnt) / n
        med_score = median(scores)

        consensus_rows.append({
            "stem": stem, "human_accepted": human,
            "samples": n, "consensus_verdict": consensus_verdict,
            "consensus_score": med_score, "confidence": confidence,
            "accept_cnt": accept_cnt,
        })

        all_v = "/".join(v[:6] for v in verdicts)
        print(f"{stem:<14} {'Y' if human else 'N':>3} | {n:>7} {accept_cnt:>7} {consensus_verdict:<14} {med_score:>10.3f} {confidence:>6.2f} | {all_v}")

    # 整体指标
    humans = ["accept" if r["human_accepted"] else "reject" for r in consensus_rows]
    preds = [r["consensus_verdict"] for r in consensus_rows]
    n = len(preds)
    acc = sum(1 for p, h in zip(preds, humans) if p == h) / n
    fp = sum(1 for p, h in zip(preds, humans) if p == "accept" and h == "reject")
    fn = sum(1 for p, h in zip(preds, humans) if p == "reject" and h == "accept")

    # κ
    from collections import Counter
    ca = Counter(preds)
    cb = Counter(humans)
    po = sum(1 for p, h in zip(preds, humans) if p == h) / n
    pe = sum((ca[k] / n) * (cb[k] / n) for k in set(ca) | set(cb))
    k = (po - pe) / (1 - pe) if pe < 1 else 1.0

    print(f"\n=== Consensus 整体指标 ===")
    print(f"  acc = {acc:.3f}  FP = {fp}  FN = {fn}  κ = {k:.3f}")

    # 按置信度分层
    print(f"\n=== 按置信度分层 ===")
    high = [r for r in consensus_rows if r["confidence"] >= 0.8]
    mid = [r for r in consensus_rows if 0.6 <= r["confidence"] < 0.8]
    low = [r for r in consensus_rows if r["confidence"] < 0.6]
    for tier, group, label in [("high", high, "≥0.8 强一致"), ("mid", mid, "0.6-0.8 中一致"), ("low", low, "<0.6 分裂")]:
        if not group:
            print(f"  [{tier}] {label}: 0 篇")
            continue
        correct = sum(1 for r in group if (r["consensus_verdict"] == "accept") == r["human_accepted"])
        print(f"  [{tier}] {label}: {len(group)} 篇, acc = {correct/len(group):.3f}")

    # 对比单次（取 sample_id=0）
    print(f"\n=== 对比单次采样（sample_id=0）vs Consensus ===")
    single_rows = [by_stem[s][0] for s in sorted(by_stem) if by_stem[s]]
    single_preds = ["accept" if s["depth_verdict"] == "accept" else "reject" for s in single_rows]
    single_humans = ["accept" if s["human_accepted"] else "reject" for s in single_rows]
    single_acc = sum(1 for p, h in zip(single_preds, single_humans) if p == h) / len(single_preds)
    single_fn = sum(1 for p, h in zip(single_preds, single_humans) if p == "reject" and h == "accept")
    print(f"  单次:    acc = {single_acc:.3f}  FN = {single_fn}")
    print(f"  Consensus: acc = {acc:.3f}  FN = {fn}")
    print(f"  Δ acc = {acc - single_acc:+.3f}, Δ FN = {fn - single_fn:+d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", action="store_true", help="清空两个结果文件重跑")
    ap.add_argument("--calibrate", action="store_true", help="仅读已有结果算 κ")
    ap.add_argument("--consensus", action="store_true", help="读已有结果算 Self-Consensus κ")
    ap.add_argument("--mode", choices=["off", "on", "both"], default="both",
                    help="跑哪个模式（默认 both）")
    ap.add_argument("--limit", type=int, default=0,
                    help="试点模式：只跑前 N 篇（默认 0=全部）")
    ap.add_argument("--samples", type=int, default=1,
                    help="每篇论文采样次数（默认 1，推荐 5 用于 Self-Consistency）")
    args = ap.parse_args()

    if args.calibrate:
        calibrate()
        return
    if args.consensus:
        calibrate_consensus()
        return

    # --mode both 用 subprocess 隔离跑两次：env 顶层常量单进程切换不生效
    if args.mode == "both":
        import subprocess
        # 优先用项目 .venv（sys.executable 可能是 TRAE 内置 Python 没装依赖）
        venv_py = ROOT / ".venv" / "Scripts" / "python.exe"
        py = str(venv_py) if venv_py.exists() else sys.executable
        cmd_base = [py, str(Path(__file__).resolve())]
        if args.fresh:
            # 先清两个文件
            if RESULTS_OFF.exists():
                RESULTS_OFF.unlink()
            if RESULTS_ON.exists():
                RESULTS_ON.unlink()
        env = dict(os.environ)
        # 子进程继承父进程 env；PAPERFORGE_* 在 run_one_mode 内部再设
        # 关键：关闭 fresh，子进程走续跑（fresh 已在父进程处理）
        cmd_off = cmd_base + ["--mode", "off", "--samples", str(args.samples)]
        cmd_on = cmd_base + ["--mode", "on", "--samples", str(args.samples)]
        if args.limit:
            cmd_off += ["--limit", str(args.limit)]
            cmd_on += ["--limit", str(args.limit)]
        print(f"[both] 启动 OFF 子进程: {' '.join(cmd_off)}")
        r1 = subprocess.run(cmd_off, env=env)
        print(f"[both] OFF 子进程退出码: {r1.returncode}")
        print(f"[both] 启动 ON 子进程: {' '.join(cmd_on)}")
        r2 = subprocess.run(cmd_on, env=env)
        print(f"[both] ON 子进程退出码: {r2.returncode}")
        if args.samples > 1:
            calibrate_consensus()
        else:
            calibrate()
        return

    papers = load_target_papers()
    if args.limit and args.limit > 0:
        papers = papers[:args.limit]
    print(f"[准备] 有图证据 + 有金标的 PeerRead 论文: {len(papers)} 篇")
    print(f"  accepted 分布: {sum(1 for p in papers if p['accepted'])} accept, "
          f"{sum(1 for p in papers if not p['accepted'])} reject")
    if args.samples > 1:
        print(f"  采样次数: {args.samples}（Self-Consistency 模式）")

    run_one_mode(args.mode, papers,
                 RESULTS_OFF if args.mode == "off" else RESULTS_ON,
                 fresh=args.fresh, samples=args.samples)


if __name__ == "__main__":
    main()
