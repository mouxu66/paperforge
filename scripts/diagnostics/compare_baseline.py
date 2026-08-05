"""对比 415 篇重跑前后基线分数分布变化。

旧基线：2026-07-24/25 跑的（bug 修复前）
新基线：2026-07-28 跑完的 613 篇（bug 修复后）
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # scripts/diagnostics -> scripts -> paperforge
sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "mock_api" / "paperforge_mock.db"


def cohen_kappa_2tier(y_pred, y_true):
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


def confusion(y_pred, y_true):
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


def verdict_to_accept_int(verdict, tier="strict"):
    if not verdict:
        return 0
    v = verdict.replace("_revision", "")
    if tier == "strict":
        return 1 if v == "accept" else 0
    return 1 if v in ("accept", "minor") else 0


def main():
    print("=== 新旧基线对比分析 ===\n")

    # 新基线（从 DB 读取）
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    # DepthReviewV4 表名是 depth_reviews_v4，final_verdict 是 JSON 列
    cur.execute("""
        SELECT paper_id, final_verdict
        FROM depth_reviews_v4
        WHERE final_verdict IS NOT NULL AND kind='paper'
    """)
    new_rows = {}
    for paper_id, fv_json in cur.fetchall():
        try:
            fv = json.loads(fv_json) if isinstance(fv_json, str) else fv_json
        except Exception:
            fv = {}
        new_rows[paper_id] = {
            "calibrated_score": fv.get("calibrated_score") or fv.get("score"),
            "base_score": fv.get("base_score"),
            "final_verdict": fv.get("final_verdict") or fv.get("verdict"),
        }
    conn.close()
    print(f"新基线 DB 记录: {len(new_rows)} 篇")

    # 旧基线（从 jsonl 读取，如果存在）
    old_jsonl = ROOT / "deliverables" / "figures_off_large_scale.jsonl"
    old_rows = {}
    if old_jsonl.exists():
        for line in old_jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    old_rows[r["stem"]] = r
                except Exception:
                    pass
        print(f"旧基线 jsonl 记录: {len(old_rows)} 篇")
    else:
        print(f"旧基线 jsonl 不存在: {old_jsonl}")

    # PeerRead 金标
    peerread_idx = ROOT / "deliverables" / "peerread_index.json"
    human_labels = {}
    if peerread_idx.exists():
        idx = json.loads(peerread_idx.read_text(encoding="utf-8"))
        for j in idx:
            stem = j["stem"]
            paper_id = f"pr_{stem}"
            human_labels[paper_id] = 1 if j["accepted"] else 0
            human_labels[stem] = 1 if j["accepted"] else 0
        print(f"PeerRead 金标: {len(idx)} 篇")
    else:
        print(f"PeerRead 索引不存在: {peerread_idx}")
    print()

    # 新基线整体指标（只在 PeerRead 金标集上算）
    pr_papers = [(pid, v) for pid, v in new_rows.items() if pid in human_labels]
    print(f"=== 新基线 PeerRead 交集: {len(pr_papers)} 篇 ===")
    if pr_papers:
        y_true = [human_labels[pid] for pid, _ in pr_papers]
        # 阈值扫描
        print("\n--- 阈值扫描（strict 口径）---")
        for th in [0.50, 0.60, 0.70, 0.74, 0.80, 0.85, 0.90]:
            y_pred = [1 if (v["calibrated_score"] or 0) >= th else 0 for _, v in pr_papers]
            kappa = cohen_kappa_2tier(y_pred, y_true)
            cm = confusion(y_pred, y_true)
            print(f"  th={th:.2f}: κ={kappa:+.4f}  acc={cm['accuracy']}  "
                  f"TP={cm['tp']} FN={cm['fn']} FP={cm['fp']} TN={cm['tn']}  "
                  f"prec={cm['precision']} rec={cm['recall']} F1={cm['f1']}")

        # verdict 分布
        print("\n--- verdict 分布 ---")
        dist = Counter(v["final_verdict"] for _, v in pr_papers)
        print(f"  {dict(dist)}")

        # 分数分布
        print("\n--- 分数分布 ---")
        scores = [v["calibrated_score"] or 0 for _, v in pr_papers]
        scores.sort()
        print(f"  min={min(scores):.3f} max={max(scores):.3f} "
              f"median={scores[len(scores)//2]:.3f} "
              f"mean={sum(scores)/len(scores):.3f}")
        # 按 human 分组
        accept_scores = [v["calibrated_score"] or 0 for pid, v in pr_papers if human_labels[pid] == 1]
        reject_scores = [v["calibrated_score"] or 0 for pid, v in pr_papers if human_labels[pid] == 0]
        if accept_scores:
            accept_scores.sort()
            print(f"  accept({len(accept_scores)}): min={min(accept_scores):.3f} "
                  f"max={max(accept_scores):.3f} median={accept_scores[len(accept_scores)//2]:.3f} "
                  f"mean={sum(accept_scores)/len(accept_scores):.3f}")
        if reject_scores:
            reject_scores.sort()
            print(f"  reject({len(reject_scores)}): min={min(reject_scores):.3f} "
                  f"max={max(reject_scores):.3f} median={reject_scores[len(reject_scores)//2]:.3f} "
                  f"mean={sum(reject_scores)/len(reject_scores):.3f}")

    # 新旧对比（如果有旧基线）
    if old_rows:
        common_pids = []
        for pid in new_rows:
            stem = pid[3:] if pid.startswith("pr_") else pid
            if stem in old_rows:
                common_pids.append((pid, stem))

        print(f"\n=== 新旧基线对比（共同 PeerRead 篇数: {len(common_pids)}）===")
        if common_pids:
            diffs = []
            for pid, stem in common_pids:
                s_new = new_rows[pid]["calibrated_score"] or 0
                s_old = old_rows[stem]["depth_calibrated_score"] or 0
                v_new = new_rows[pid]["final_verdict"]
                v_old = old_rows[stem]["depth_verdict"]
                d = s_new - s_old
                diffs.append((stem, s_old, s_new, d, v_old, v_new))

            n_changed = sum(1 for d in diffs if abs(d[3]) > 1e-4)
            n_flip = sum(1 for d in diffs if d[4] != d[5])
            print(f"  score 改变: {n_changed}/{len(common_pids)}")
            print(f"  verdict 翻转: {n_flip}/{len(common_pids)}")

            # 旧基线指标
            y_true_old = [human_labels.get(s, 0) for _, s in common_pids]
            y_pred_old = [verdict_to_accept_int(old_rows[s]["depth_verdict"], "strict") for _, s in common_pids]
            kappa_old = cohen_kappa_2tier(y_pred_old, y_true_old)
            cm_old = confusion(y_pred_old, y_true_old)

            # 新基线指标（同集合）
            y_pred_new = [verdict_to_accept_int(new_rows[p]["final_verdict"], "strict") for p, _ in common_pids]
            kappa_new = cohen_kappa_2tier(y_pred_new, y_true_old)
            cm_new = confusion(y_pred_new, y_true_old)

            print(f"\n  --- 旧基线（bug 修复前）---")
            print(f"  κ={kappa_old:+.4f}  acc={cm_old['accuracy']}  "
                  f"TP={cm_old['tp']} FN={cm_old['fn']} FP={cm_old['fp']} TN={cm_old['tn']}  "
                  f"prec={cm_old['precision']} rec={cm_old['recall']} F1={cm_old['f1']}")
            print(f"\n  --- 新基线（bug 修复后）---")
            print(f"  κ={kappa_new:+.4f}  acc={cm_new['accuracy']}  "
                  f"TP={cm_new['tp']} FN={cm_new['fn']} FP={cm_new['fp']} TN={cm_new['tn']}  "
                  f"prec={cm_new['precision']} rec={cm_new['recall']} F1={cm_new['f1']}")

            # 分数偏移统计
            if diffs:
                ds = [d[3] for d in diffs]
                pos = sum(1 for d in ds if d > 0.01)
                neg = sum(1 for d in ds if d < -0.01)
                zero = len(ds) - pos - neg
                print(f"\n  --- score 偏移 ---")
                print(f"  正偏移(>+0.01): {pos} 篇")
                print(f"  负偏移(<-0.01): {neg} 篇")
                print(f"  无变化: {zero} 篇")
                if pos + neg > 0:
                    pos_avg = sum(d[3] for d in diffs if d[3] > 0.01) / max(pos, 1)
                    neg_avg = sum(d[3] for d in diffs if d[3] < -0.01) / max(neg, 1)
                    print(f"  正偏移均值: +{pos_avg:.4f}")
                    print(f"  负偏移均值: {neg_avg:.4f}")

                # 按 human 分组看偏移
                print(f"\n  --- 按 human 分组 score 偏移 ---")
                for h in [1, 0]:
                    label = "accept" if h == 1 else "reject"
                    grp = [d for d in diffs if human_labels.get(d[0], 0) == h]
                    if grp:
                        gs = [d[3] for d in grp]
                        avg = sum(gs) / len(gs)
                        gpos = sum(1 for d in gs if d > 0.01)
                        gneg = sum(1 for d in gs if d < -0.01)
                        print(f"  human={label}({len(grp)}): avg Δ={avg:+.4f}, pos={gpos}, neg={gneg}")


if __name__ == "__main__":
    main()
