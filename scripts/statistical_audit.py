"""数据造假指纹检测 CLI（零 LLM，纯正则/统计）。

把 depth_eval_v4 的统计合理性检测（等差 / 重复 / 恒定偏移 / Benford / p 值聚集等）
扩展到「正文 + 补充数据表 + 图内曲线点」三类输入，离线对一篇论文的数值做指纹扫描。

用法：
    .venv/Scripts/python.exe scripts/statistical_audit.py --text paper.txt \
        --table supp1.csv --table supp2.tsv --curve curves.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mock_api.depth_eval_v4 import (  # noqa: E402
    _statistical_plausibility_check,
    statistical_flags_from_curve_points,
    statistical_flags_from_table_file,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", help="论文全文文本文件（txt/md）")
    ap.add_argument("--table", action="append", default=[], help="补充数据表（CSV/TSV，可重复）")
    ap.add_argument(
        "--curve",
        action="append",
        default=[],
        help="图内曲线点 JSON（extract_curve_points 输出格式，可重复）",
    )
    args = ap.parse_args()

    flags: list[str] = []
    if args.text:
        try:
            text = Path(args.text).read_text(encoding="utf-8-sig", errors="ignore")
        except OSError as e:
            print(f"读取正文失败: {e}")
            return 1
        flags += _statistical_plausibility_check(text)

    for t in args.table:
        flags = statistical_flags_from_table_file(t, existing_flags=flags)

    for c in args.curve:
        try:
            points = json.loads(Path(c).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"读取曲线点失败 {c}: {e}")
            continue
        flags = statistical_flags_from_curve_points(points, existing_flags=flags)

    # 去重保序
    seen: set[str] = set()
    unique = [f for f in flags if not (f in seen or seen.add(f))]
    if not unique:
        print("未检出数据造假指纹。")
        return 0
    print(f"共检出 {len(unique)} 条可疑指纹：")
    for f in unique:
        print(f"  - {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
