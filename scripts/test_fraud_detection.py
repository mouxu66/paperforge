#!/usr/bin/env python3
"""论文数据造假检测系统测试（端到端、零 LLM、可复跑）。

对 mock_api/experiment_audit/table_numbers.py 的每个造假指纹检测器，
用一组构造好的数值序列验证「期望检测的类型确实能被检出」。

与生产链路一致：复用 ``_run_figure_fraud_detection`` 相同的检测组合
（等差/重复/恒定偏移/Benford → 跨组重复 → 末位偏好 → 精度一致 → 互补/高度相似）。

用法（仓库根目录）：
    .venv\\Scripts\\python scripts\\test_fraud_detection.py

退出码：全部用例通过 = 0，任一失败 = 1。
"""

from __future__ import annotations

import os
import sys

# 把仓库根目录加入 sys.path，使脚本从任意目录/无需 PYTHONPATH 也能 import mock_api。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Windows GBK 控制台不炸：强制 UTF-8 输出，失败替换而非抛异常。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from mock_api.experiment_audit.table_numbers import (  # noqa: E402
    detect_complementary_groups,
    detect_cross_figure_duplicates,
    detect_cross_group_duplicates,
    detect_decimal_precision_consistency,
    detect_digit_preference,
)
from mock_api.depth_eval_v4 import statistical_flags_from_series  # noqa: E402

# ── 旗标前缀 → 类型名 ────────────────────────────────────────────────────────
FLAG_TYPE_MAP: list[tuple[str, str]] = [
    ("[末位偏好]", "digit_preference"),
    ("[精度一致]", "decimal_precision"),
    ("[互补]", "complementary"),
    ("[高度相似]", "highly_similar"),
    ("[跨组重复]", "cross_group_duplicate"),
    ("[等差数字]", "arith_progression"),
    ("[重复数字]", "repeated_value"),
    ("[恒定偏移]", "constant_offset"),
    ("[Benford偏离]", "benford"),
]


def flag_to_type(flag: str) -> str:
    for prefix, name in FLAG_TYPE_MAP:
        if flag.startswith(prefix):
            return name
    return "unknown"


def detect_fraud(series: list[tuple[str, list[float]]]) -> list[tuple[str, str]]:
    """跑完整单图统计指纹组合，返回 [(类型, 详情), ...]。"""
    all_vals = [v for _, vals in series for v in vals]
    hits: list[tuple[str, str]] = []

    # 等差 / 重复 / 恒定偏移 / Benford（复用 depth_eval_v4 零 LLM helper）
    for f in statistical_flags_from_series(series):
        hits.append((flag_to_type(f), f))

    # 跨组重复
    for f in detect_cross_group_duplicates(series):
        hits.append(("cross_group_duplicate", f))

    # 末位偏好
    pref = detect_digit_preference(all_vals)
    if pref:
        hits.append(("digit_preference", pref))

    # 精度一致
    prec = detect_decimal_precision_consistency(series)
    if prec:
        hits.append(("decimal_precision", prec))

    # 互补 / 高度相似
    for f in detect_complementary_groups(series):
        hits.append((flag_to_type(f), f))

    return hits


# ── 用例：每类造假指纹一个（或多个变体）───────────────────────────────────────
CASES: list[dict] = [
    {
        "name": "末位偏好（小数）",
        "desc": "所有数值末位都是 1，典型人手编造随机数",
        "expected": ["digit_preference"],
        "series": [("WT", [0.21, 0.51, 0.91, 1.31, 1.71, 2.11, 2.81, 3.31, 4.01, 4.51])],
    },
    {
        "name": "末位偏好（整数）",
        "desc": "整数末位都是 1（回归 float 表示 11.0 把末位误读成 0 的 bug）",
        "expected": ["digit_preference"],
        "series": [
            ("WT", [11.0, 21.0, 51.0, 61.0, 91.0, 121.0, 141.0, 171.0, 201.0, 231.0])
        ],
    },
    {
        "name": "等差数列",
        "desc": "≥4 个数值差值恒等（82.5/84.0/85.5/87.0/88.5）",
        "expected": ["arith_progression"],
        "series": [("WT", [82.5, 84.0, 85.5, 87.0, 88.5])],
    },
    {
        "name": "跨组重复",
        "desc": "同一数值 0.35 同时出现在 WT 与 H186R 两组",
        "expected": ["cross_group_duplicate"],
        "series": [("WT", [0.35, 0.42, 0.51]), ("H186R", [0.35, 0.63, 0.72])],
    },
    {
        "name": "互补",
        "desc": "两组逐行之和恒为常数（0.1+0.9, 0.2+0.8…）",
        "expected": ["complementary"],
        "series": [("WT", [0.1, 0.2, 0.3, 0.4]), ("M", [0.9, 0.8, 0.7, 0.6])],
    },
    {
        "name": "精度一致",
        "desc": "同组多个数值小数位数完全相同（5 位）",
        "expected": ["decimal_precision"],
        "series": [("WT", [1.23456, 2.34567, 3.45678])],
    },
    {
        "name": "高度相似",
        "desc": "两组数值平均相对差异 <5%",
        "expected": ["highly_similar"],
        "series": [("WT", [0.500, 0.600, 0.700]), ("M", [0.510, 0.612, 0.714])],
    },
    {
        "name": "恒定偏移",
        "desc": "两组逐行差值恒为常数（1.1 vs 1.2, 2.2 vs 2.3…）",
        "expected": ["constant_offset"],
        "series": [("WT", [1.1, 2.2, 3.3, 4.4]), ("M", [1.2, 2.3, 3.4, 4.5])],
    },
    {
        "name": "重复数字",
        "desc": "同组 ≥3 行数值完全相同（0.35 ×3）",
        "expected": ["repeated_value"],
        "series": [("WT", [0.35, 0.35, 0.35, 0.42])],
    },
    {
        "name": "Benford 偏离",
        "desc": "≥20 个数值首位全是 9，首位分布严重偏离 Benford",
        "expected": ["benford"],
        "series": [("WT", [90.0 + i * 0.1 for i in range(25)])],
    },
]

# 跨表复制需要 ≥2 张 figure，单独一组。
CROSS_FIGURE_CASE: dict = {
    "name": "跨表完全复制",
    "desc": "两张 figure 共享完全相同的数值（0.35 / 0.42）",
    "expected": ["cross_figure_duplicate"],
    "fig_data": [
        ("Figure 1", [("WT", [0.35, 0.42, 0.51])]),
        ("Figure 2", [("H186R", [0.35, 0.42, 0.63])]),
    ],
}


def _fmt_types(types: list[str]) -> str:
    return ", ".join(types) if types else "（无）"


def run_case(idx: int, case: dict, detected: list[str]) -> bool:
    missing = [e for e in case["expected"] if e not in detected]
    extra = [t for t in detected if t not in case["expected"]]
    ok = not missing

    print("─" * 70)
    print(f"📄 Paper {idx} ({case['name']})")
    print(f"   描述: {case['desc']}")
    print(f"   期望检测: {_fmt_types(case['expected'])}")
    print(f"   检测结果: {_fmt_types(detected)}")
    if ok:
        note = f"（另检出 {_fmt_types(extra)}）" if extra else ""
        print(f"   ✅ 通过{note}")
    else:
        print(f"   ❌ 失败：未检出 {_fmt_types(missing)}")
    return ok


def main() -> int:
    print("=" * 70)
    print("论文数据造假检测系统测试")
    print("=" * 70)

    total = ok_count = 0
    for i, case in enumerate(CASES, 1):
        hits = detect_fraud(case["series"])
        detected = sorted({t for t, _ in hits})
        total += 1
        if run_case(i, case, detected):
            ok_count += 1

    # 跨表复制（detect_cross_figure_duplicates 返回 dict 而非旗标字符串，按命中与否判定）
    cross = detect_cross_figure_duplicates(CROSS_FIGURE_CASE["fig_data"])
    detected = ["cross_figure_duplicate"] if cross else []
    total += 1
    if run_case(len(CASES) + 1, CROSS_FIGURE_CASE, detected):
        ok_count += 1

    print("=" * 70)
    print(f"汇总: {ok_count}/{total} 用例通过")
    print("=" * 70)
    return 0 if ok_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
