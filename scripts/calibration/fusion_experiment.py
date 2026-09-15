"""双模型校准融合实验（本地 + 云端 GLM-4.7-Flash → 人类金标 PeerRead）。

数据来源：
  - 本地 rescore:  deliverables/peerread_rescore_500_base.jsonl  (Ornstein-V2 DEPTH v4)
  - 云端 rescore:  deliverables/peerread_cloud_rescore_500.jsonl (GLM-4.7-Flash)
  - 人类标签:      人工 accept/reject (PeerRead); ICLR 2017 有连续推荐分 1-10

实验设计（严格 70/15/15 分割，无泄漏）：
  1. 合并双模型数据 → 去重取最新成功记录 → 有效配对
  2. 分割: train 70% / val 15% / test 15%（分层随机，固定 seed）
  3. 训练集上拟合校准：
     - 本地 isotonic regression → calibrated_local（P(accept) 连续分）
     - 云端 isotonic regression → calibrated_cloud（P(accept) 连续分）
  4. 验证集上选超参（ridge alpha）
  5. 测试集上评估5个配置：
     A. 本地裸分
     B. 云端裸分
     C. 本地校准后
     D. 云端校准后
     E. 加权融合（校准后加权平均，权重从 val 选）
     F. 元模型（ridge regression，特征=[calibrated_local, calibrated_cloud]）
  6. 输出指标：AUC、precision/recall/F1@threshold、Pearson、MAE

运行：
  python scripts/calibration/fusion_experiment.py
  python scripts/calibration/fusion_experiment.py --seed 42 --train-ratio 0.7
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

LOCAL_PATH = ROOT / "deliverables" / "peerread_rescore_500_base.jsonl"
CLOUD_PATH = ROOT / "deliverables" / "peerread_cloud_rescore_500.jsonl"
OUT_DIR = ROOT / "deliverables" / "fusion_experiment"


# ── 数据加载与配对 ──────────────────────────────────────────────────────

def load_paired() -> list[dict]:
    """加载本地+云端 jsonl → 去重取最新成功记录 → 返回配对列表。"""
    # 读本地
    local_by_stem: dict[str, dict] = {}
    for line in LOCAL_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("depth_calibrated_score") is not None:
            local_by_stem[r["stem"]] = r

    # 读云端（每 stem 取最后一条成功记录）
    cloud_by_stem: dict[str, dict] = {}
    for line in CLOUD_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("cloud_score") is not None:
            cloud_by_stem[r["stem"]] = r  # 追加写，最后一条 = 最新成功

    # 配对
    pairs = []
    for stem in sorted(set(local_by_stem) & set(cloud_by_stem)):
        l = local_by_stem[stem]
        c = cloud_by_stem[stem]
        pairs.append({
            "stem": stem,
            "venue": l.get("venue", ""),
            "human_accept": 1 if l.get("human_accepted") else 0,
            "recommendation_score": l.get("recommendation_score"),
            "local_raw": l["depth_calibrated_score"],
            "cloud_raw": c["cloud_score"],
            "local_verdict": l.get("depth_verdict"),
            "cloud_verdict": c.get("cloud_verdict"),
        })
    return pairs


# ── 分层抽样分割 ──────────────────────────────────────────────────────

def stratified_split(pairs: list[dict], train_r: float, val_r: float,
                     seed: int) -> tuple[list, list, list]:
    """分层随机 split（按 human_accept × venue 分层），固定 seed。"""
    import random
    rng = random.Random(seed)

    # 分层 key: (human_accept, venue)
    layers: dict[tuple, list[dict]] = {}
    for p in pairs:
        key = (p["human_accept"], p["venue"])
        layers.setdefault(key, []).append(p)

    train, val, test = [], [], []
    for key, items in layers.items():
        rng.shuffle(items)
        n = len(items)
        n_train = max(1, int(n * train_r))
        n_val = max(1, int(n * val_r)) if n > 2 else 0
        n_test = n - n_train - n_val
        train.extend(items[:n_train])
        val.extend(items[n_train:n_train + n_val])
        test.extend(items[n_train + n_val:])

    return train, val, test


# ── Isotonic Regression（手写，无需 sklearn）─────────────────────────

class IsotonicRegression:
    """简单保序回归：将 raw_score 映射到 P(accept)。"""

    def __init__(self):
        self.x_thresholds: list[float] = []
        self.y_values: list[float] = []

    def fit(self, x: list[float], y: list[float]):
        """x=raw scores, y=0/1 labels → fit isotonic map."""
        data = sorted(zip(x, y))
        # pool adjacent violators (PAV)
        values = [(xi, yi, 1) for xi, yi in data]
        changed = True
        while changed:
            changed = False
            i = 0
            while i < len(values) - 1:
                if values[i][1] > values[i + 1][1]:  # 违反单调
                    # pool
                    sx, sy, sw = values[i]
                    nx, ny, nw = values[i + 1]
                    total_w = sw + nw
                    pooled_y = (sy * sw + ny * nw) / total_w
                    values[i] = (sx, pooled_y, total_w)
                    del values[i + 1]
                    changed = True
                else:
                    i += 1
        self.x_thresholds = [v[0] for v in values]
        self.y_values = [v[1] for v in values]

    def predict(self, x: float) -> float:
        if not self.x_thresholds:
            return 0.5
        # binary search for position
        lo, hi = 0, len(self.x_thresholds) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.x_thresholds[mid] <= x:
                lo = mid
            else:
                hi = mid - 1
        return self.y_values[lo]


# ── Ridge Regression（手写，无需 sklearn）─────────────────────────────

class RidgeRegression:
    """最小二乘 + L2 正则化：predict = X @ w + b。"""

    def __init__(self, alpha: float = 1.0):
        self.alpha = alpha
        self.w: list[float] = []
        self.b: float = 0.0

    def fit(self, X: list[list[float]], y: list[float]):
        n = len(X)
        d = len(X[0])
        # 增广矩阵 [X|1]，正则 = alpha * I（不含 bias 项）
        # Solve (X^T X + alpha * I) w = X^T y  via Gauss elimination
        XtX = [[0.0] * (d + 1) for _ in range(d + 1)]
        Xty = [0.0] * (d + 1)
        for i in range(n):
            row = X[i] + [1.0]
            for j in range(d + 1):
                Xty[j] += row[j] * y[i]
                for k in range(d + 1):
                    XtX[j][k] += row[j] * row[k]
        # 正则（前 d 维）
        for j in range(d):
            XtX[j][j] += self.alpha
        # Gauss elimination
        mat = [XtX[j] + [Xty[j]] for j in range(d + 1)]
        for col in range(d + 1):
            # partial pivot
            max_row = col
            for row in range(col + 1, d + 1):
                if abs(mat[row][col]) > abs(mat[max_row][col]):
                    max_row = row
            mat[col], mat[max_row] = mat[max_row], mat[col]
            if abs(mat[col][col]) < 1e-12:
                continue
            for row in range(d + 1):
                if row == col:
                    continue
                factor = mat[row][col] / mat[col][col]
                for k in range(col, d + 2):
                    mat[row][k] -= factor * mat[col][k]
        self.w = [mat[j][d + 1] / mat[j][j] if abs(mat[j][j]) > 1e-12 else 0.0 for j in range(d)]
        self.b = mat[d][d + 1] / mat[d][d] if abs(mat[d][d]) > 1e-12 else 0.0

    def predict(self, X: list[list[float]]) -> list[float]:
        return [sum(x[j] * self.w[j] for j in range(len(self.w))) + self.b for x in X]


# ── 评估指标 ────────────────────────────────────────────────────────

def pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return float("nan")
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs) / n)
    sy = math.sqrt(sum((y - my) ** 2 for y in ys) / n)
    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n * sx * sy)


def auc_roc(scores: list[float], labels: list[int]) -> float:
    """AUC via Mann-Whitney U (with tie correction)."""
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    # count concordant pairs + ties
    concordant = tie = 0
    for i in range(len(labels)):
        if labels[i] == 1:
            for j in range(len(labels)):
                if labels[j] == 0:
                    if scores[i] > scores[j]:
                        concordant += 1
                    elif scores[i] == scores[j]:
                        tie += 1
    return (concordant + 0.5 * tie) / (n_pos * n_neg)


def precision_recall_f1(threshold: float, scores: list[float], labels: list[int]):
    tp = sum(1 for s, l in zip(scores, labels) if s >= threshold and l == 1)
    fp = sum(1 for s, l in zip(scores, labels) if s >= threshold and l == 0)
    fn = sum(1 for s, l in zip(scores, labels) if s < threshold and l == 1)
    tn = sum(1 for s, l in zip(scores, labels) if s < threshold and l == 0)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    acc = (tp + tn) / len(labels) if labels else 0.0
    return {"threshold": threshold, "precision": prec, "recall": rec, "f1": f1, "accuracy": acc,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def mae(xs: list[float], ys: list[float]) -> float:
    return sum(abs(x - y) for x, y in zip(xs, ys)) / len(xs) if xs else 0.0


# ── 主实验 ──────────────────────────────────────────────────────────

def run_experiment(seed: int = 20260818, train_r: float = 0.7, val_r: float = 0.15):
    pairs = load_paired()
    print(f"有效配对: {len(pairs)} 篇")

    train, val, test = stratified_split(pairs, train_r, val_r, seed)
    print(f"分割: train={len(train)} val={len(val)} test={len(test)}")
    print(f"  train accept rate: {sum(p['human_accept'] for p in train)/len(train):.3f}")
    print(f"  val accept rate:   {sum(p['human_accept'] for p in val)/len(val):.3f}")
    print(f"  test accept rate:  {sum(p['human_accept'] for p in test)/len(test):.3f}")

    # ── Step 1: 拟合 isotonic 校准（训练集）──
    iso_local = IsotonicRegression()
    iso_cloud = IsotonicRegression()
    iso_local.fit([p["local_raw"] for p in train], [p["human_accept"] for p in train])
    iso_cloud.fit([p["cloud_raw"] for p in train], [p["human_accept"] for p in train])

    # ── Step 2: 在 val 上选超参（ridge alpha + 融合权重）──
    val_cal_local = [iso_local.predict(p["local_raw"]) for p in val]
    val_cal_cloud = [iso_cloud.predict(p["cloud_raw"]) for p in val]
    val_labels = [p["human_accept"] for p in val]

    best_alpha = 1.0
    best_auc = 0.0
    for alpha in [0.01, 0.1, 1.0, 10.0, 100.0]:
        ridge = RidgeRegression(alpha=alpha)
        ridge.fit([[cl, cc] for cl, cc in zip(val_cal_local, val_cal_cloud)], val_labels)
        pred = ridge.predict([[cl, cc] for cl, cc in zip(val_cal_local, val_cal_cloud)])
        a = auc_roc(pred, val_labels)
        if not math.isnan(a) and a > best_auc:
            best_auc = a
            best_alpha = alpha

    # 同时在 val 上选加权融合权重
    best_w = 0.5
    best_fused_auc = 0.0
    for w in [x / 10.0 for x in range(0, 11)]:
        fused = [w * cl + (1 - w) * cc for cl, cc in zip(val_cal_local, val_cal_cloud)]
        a = auc_roc(fused, val_labels)
        if not math.isnan(a) and a > best_fused_auc:
            best_fused_auc = a
            best_w = w

    print(f"\nval 超参选择: ridge_alpha={best_alpha} (val_AUC={best_auc:.4f})")
    print(f"val 融合权重:  w_local={best_w:.1f} (val_AUC={best_fused_auc:.4f})")

    # ── Step 3: 在 test 上评估所有配置 ──
    test_labels = [p["human_accept"] for p in test]

    # 计算各配置分数
    configs = {}

    # A. 本地裸分（归一化到 0-1 用于公平比较）
    local_raws = [p["local_raw"] for p in test]
    configs["A_local_raw"] = local_raws

    # B. 云端裸分
    cloud_raws = [p["cloud_raw"] for p in test]
    configs["B_cloud_raw"] = cloud_raws

    # C. 本地 isotonic 校准
    cal_local = [iso_local.predict(p["local_raw"]) for p in test]
    configs["C_local_cal"] = cal_local

    # D. 云端 isotonic 校准
    cal_cloud = [iso_cloud.predict(p["cloud_raw"]) for p in test]
    configs["D_cloud_cal"] = cal_cloud

    # E. 加权融合（校准后）
    fused_w = [best_w * cl + (1 - best_w) * cc for cl, cc in zip(cal_local, cal_cloud)]
    configs["E_fused_w"] = fused_w

    # F. 元模型（ridge regression，训练集拟合）
    ridge_final = RidgeRegression(alpha=best_alpha)
    ridge_final.fit(
        [[iso_local.predict(p["local_raw"]), iso_cloud.predict(p["cloud_raw"])] for p in train],
        [p["human_accept"] for p in train],
    )
    fused_ridge = ridge_final.predict([[cl, cc] for cl, cc in zip(cal_local, cal_cloud)])
    configs["F_ridge"] = fused_ridge

    # ── 评估输出 ──
    results = []
    print(f"\n{'='*80}")
    print(f"{'配置':<16} {'AUC':>6} {'Pearson':>8} {'F1@0.5':>8} {'prec@0.5':>9} {'rec@0.5':>8} {'MAE':>6}")
    print(f"{'='*80}")

    for name, scores in configs.items():
        labels = test_labels
        a = auc_roc(scores, labels)
        r = pearson(scores, [float(l) for l in labels])
        pr = precision_recall_f1(0.5, scores, labels)
        m = mae(scores, [float(l) for l in labels])
        line = f"{name:<16} {a:6.4f} {r:8.4f} {pr['f1']:8.4f} {pr['precision']:9.4f} {pr['recall']:8.4f} {m:6.4f}"
        print(line)
        results.append({"name": name, "auc": a, "pearson": r,
                         "precision_0.5": pr["precision"], "recall_0.5": pr["recall"],
                         "f1_0.5": pr["f1"], "mae": m, "threshold_detail": pr})

    # ── 对比 A vs F（核心：融合 vs 本地单独）──
    a_auc = configs["A_local_raw"] and auc_roc(configs["A_local_raw"], test_labels)
    f_auc = auc_roc(configs["F_ridge"], test_labels)
    delta_auc = f_auc - a_auc
    print(f"\n{'='*80}")
    print(f"核心对比: 本地裸分 AUC={a_auc:.4f} → ridge 融合 AUC={f_auc:.4f}  Δ={delta_auc:+.4f}")
    if delta_auc > 0.02:
        print("  ✅ 融合显著优于本地（AUC 提升 >0.02）→ 建议部署融合系统")
    elif delta_auc > 0:
        print("  ⚠️ 融合略优于本地（AUC 提升 <0.02）→ 可能部署，需更多验证")
    else:
        print("  ❌ 融合不优于本地 → 维持现状，云端仅作影子记录")

    # ── 条件触发区间扫描（边界仲裁部署方案验证）──
    print(f"\n{'='*80}")
    print("条件触发区间扫描（仅边界区间用融合分，其余用本地裸分）")
    print(f"{'='*80}")
    print(f"{'区间':<16} {'AUC':>6} {'ΔAUC':>7} {'prec@0.5':>9} {'rec@0.5':>8} {'API调用率':>9} {'调用数':>6}")
    print(f"{'-'*70}")

    sweep_rows = []
    for low, high in [(0.60, 0.75), (0.65, 0.75), (0.65, 0.80), (0.65, 0.85), (0.65, 0.90), (0.70, 0.85), (0.70, 0.80)]:
        hybrid = []
        n_cloud_call = 0
        for i, p in enumerate(test):
            if low <= p["local_raw"] <= high:
                hybrid.append(fused_ridge[i])  # 融合分
                n_cloud_call += 1
            else:
                hybrid.append(p["local_raw"])  # 本地裸分
        api_ratio = n_cloud_call / len(test)
        h_auc = auc_roc(hybrid, test_labels)
        h_pr = precision_recall_f1(0.5, hybrid, test_labels)
        d = h_auc - a_auc
        print(f"  [{low:.2f},{high:.2f}]:  AUC={h_auc:.4f} Δ={d:+.4f}  prec={h_pr['precision']:.3f}  rec={h_pr['recall']:.3f}  API={api_ratio:.1%} ({n_cloud_call}/{len(test)})")
        sweep_rows.append({"low": low, "high": high, "auc": h_auc, "delta_auc": d,
                           "precision": h_pr["precision"], "recall": h_pr["recall"],
                           "api_ratio": api_ratio, "api_calls": n_cloud_call})

    # 找最优区间（AUC 损失 <0.01 的最小 API 调用率）
    viable = [r for r in sweep_rows if r["delta_auc"] >= -0.01]
    if viable:
        best_sweep = min(viable, key=lambda r: r["api_ratio"])
        print(f"\n  → 推荐区间: [{best_sweep['low']:.2f},{best_sweep['high']:.2f}]  AUC Δ={best_sweep['delta_auc']:+.4f}  API调用率={best_sweep['api_ratio']:.1%}")
    else:
        best_sweep = max(sweep_rows, key=lambda r: r["delta_auc"])
        print(f"\n  → 无区间满足 AUC 损失<0.01，最佳: [{best_sweep['low']:.2f},{best_sweep['high']:.2f}]  AUC Δ={best_sweep['delta_auc']:+.4f}")

    sweep_data = {"threshold_sweep": sweep_rows,
                   "recommended_interval": {"low": best_sweep["low"], "high": best_sweep["high"],
                                              "delta_auc": best_sweep["delta_auc"], "api_ratio": best_sweep["api_ratio"]}}

    # ── 保存结果 ──
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "seed": seed,
        "n_pairs": len(pairs),
        "train": len(train), "val": len(val), "test": len(test),
        "best_alpha": best_alpha, "best_w": best_w,
        "configurations": results,
        "ridge_weights": {"w": ridge_final.w, "b": ridge_final.b},
        "iso_local_thresholds": len(iso_local.x_thresholds),
        "iso_cloud_thresholds": len(iso_cloud.x_thresholds),
        **sweep_data,
    }
    out_path = OUT_DIR / f"fusion_report_seed{seed}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告 -> {out_path}")

    # 保存 test 逐篇明细
    detail_path = OUT_DIR / f"fusion_test_detail_seed{seed}.jsonl"
    with open(detail_path, "w", encoding="utf-8") as f:
        for i, p in enumerate(test):
            row = {
                "stem": p["stem"], "venue": p["venue"], "human_accept": p["human_accept"],
                "local_raw": p["local_raw"], "cloud_raw": p["cloud_raw"],
                "cal_local": cal_local[i], "cal_cloud": cal_cloud[i],
                "fused_w": fused_w[i], "ridge": fused_ridge[i],
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"测试集明细 -> {detail_path}")

    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="PeerRead 双模型校准融合实验")
    ap.add_argument("--seed", type=int, default=20260818)
    ap.add_argument("--train-ratio", type=float, default=0.7)
    args = ap.parse_args()
    run_experiment(seed=args.seed, train_r=args.train_ratio)
