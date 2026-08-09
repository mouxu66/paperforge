"""P0-5 实验信息完整性检查（可复现性清单，纯正则，无 GPU）。

清单依据指南：seed / std / hardware / lr / batch size / epochs /
early stopping（含 checkpoint 选择规则）。

判定策略：全文（重点实验设置段落）逐项关键词扫描，缺失项汇总为
**一条** MISSING_REPRO_INFO（逐项报告太吵），evidence 列出缺失清单。
"""

from __future__ import annotations

import re

from .schemas import make_finding

# 每项：(key, 中文标签, 命中正则)。命中任一即视为已报告。
REPRO_CHECKLIST: list[tuple[str, str, re.Pattern]] = [
    (
        "seed",
        "随机种子",
        re.compile(r"random seed|seeded|seed\s*(?:=|of|is|was|set)|\bseeds\b", re.IGNORECASE),
    ),
    (
        "std",
        "标准差/多次运行统计",
        re.compile(
            r"standard deviation|\bstd\b|±|confidence interval"
            r"|\bvariance\b|multiple runs|repeated .{0,20}runs|averaged over",
            re.IGNORECASE,
        ),
    ),
    (
        "hardware",
        "硬件环境",
        re.compile(
            r"\bGPU\b|\bCPU\b|\bTPU\b|NVIDIA|RTX|GTX|\bA100\b|\bV100\b"
            r"|\bH100\b|Tesla|GeForce",
            re.IGNORECASE,
        ),
    ),
    (
        "learning_rate",
        "学习率",
        re.compile(
            r"learning rate|\blr\b\s*[=:]|\blr\b of|initial learning rate",
            re.IGNORECASE,
        ),
    ),
    (
        "batch_size",
        "批大小",
        re.compile(r"batch size|batch-size|batch_size|minibatch", re.IGNORECASE),
    ),
    (
        "epochs",
        "训练轮数/步数",
        re.compile(r"\bepochs?\b|training steps|iterations|\btrained for\b", re.IGNORECASE),
    ),
    (
        "checkpoint",
        "early stopping/checkpoint 选择规则",
        re.compile(
            r"early stopping|checkpoint selection|best checkpoint"
            r"|model selection (?:on|based)|selected .{0,30}validation",
            re.IGNORECASE,
        ),
    ),
]


def check_reproducibility_info(full_text: str) -> list[dict]:
    """扫描全文，返回缺失项汇总 Finding（无缺失返回空列表）。"""
    text = full_text or ""
    if not text.strip():
        return []
    missing_labels: list[str] = []
    for _key, label, pattern in REPRO_CHECKLIST:
        if not pattern.search(text):
            missing_labels.append(label)
    if not missing_labels:
        return []
    return [
        make_finding(
            "MISSING_REPRO_INFO",
            title=f"缺少 {len(missing_labels)} 项复现关键信息",
            claim="、".join(missing_labels),
            computed=f"清单共 {len(REPRO_CHECKLIST)} 项，缺失 {len(missing_labels)} 项",
            method="关键词清单扫描: seed/std/hardware/lr/batch/epochs/early stopping",
            normal_explanation=("部分信息可能在附录或开源代码仓库中给出，建议核对后再定性"),
            needs_human_review=True,
        )
    ]
