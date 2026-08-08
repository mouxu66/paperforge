"""感悟报告显式评分量表加载器（ADR-014 · P1，reflection 侧）。

从 ``reflection_rubric.yaml`` 读取 4 维评分锚点与硬校验阈值治理记录。
与论文侧 ``depth_panel.load_rubric`` 同构，全部 **fail-open**：文件缺失 /
解析失败一律返回空 dict，绝不因量表加载失败阻断评测管线。

职责边界：
- **锚点**（dimensions / verdicts）：供人类复核、评分档位对照与金标回归使用。
- **阈值**（thresholds）：治理记录 + 一致性测试基准。**运行时的真实裁决来源
  仍是代码常量**（depth_eval_reflection.py / reflection_fidelity.py），
  ``tests/test_reflection_rubric_consistency.py`` 断言 yaml 值与常量一致，
  防止魔数漂移——改阈值必须同时改两处，且 yaml 强制要求填写依据。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_RUBRIC_PATH = os.path.join(os.path.dirname(__file__), "reflection_rubric.yaml")


def load_rubric(path: str | None = None) -> dict:
    """加载感悟报告量表 YAML。失败时返回空 dict（fail-open）。"""
    try:
        import yaml

        with open(path or _RUBRIC_PATH, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 - rubric 加载失败不应阻断评测
        logger.warning("reflection rubric 加载失败(%s): %s", path or _RUBRIC_PATH, e)
        return {}


def load_thresholds() -> dict[str, float | int]:
    """加载治理后的阈值表：{常量名: value}。

    供一致性测试与诊断使用；运行时阈值仍以代码常量为准。
    """
    rubric = load_rubric()
    th = rubric.get("thresholds") or {}
    out: dict[str, float | int] = {}
    for name, entry in th.items():
        if isinstance(entry, dict) and "value" in entry:
            out[str(name)] = entry["value"]
        elif isinstance(entry, (int, float)):
            out[str(name)] = entry
    return out


def score_to_band(dimension: str, score: float) -> str:
    """把 4 维分数映射到量表档位 label（好/中/差）。未命中返回空串。"""
    rubric = load_rubric()
    bands = (rubric.get("dimensions") or {}).get(dimension)
    if not bands:
        return ""
    s = float(score)
    for band in sorted(bands, key=lambda b: b.get("lo", 0.0), reverse=True):
        if s >= float(band.get("lo", 0.0)):
            return band.get("label", "")
    return bands[-1].get("label", "")
