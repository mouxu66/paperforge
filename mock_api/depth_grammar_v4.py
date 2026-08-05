"""DEPTH v4.1 GBNF 语法约束构建器（仅 llama.cpp / llama-server 后端生效）。

将评分节点的输出约束为**可解析**的 key:value 结构：

- 评分字段强制为 [0,1] 浮点 → 杜绝"中等 / 高"等中文文本分数、以及 2 / 8 等越界值
- evidence_id 强制从证据池枚举中选择 → 从根源消除无效 ID 触发的重试 LLM 调用
- type / secondary_type / verdict 强制枚举

背景（实测 Qwen3.5-9B-Q4_K_M）：无约束时模型频繁产出
``novelty_score: 中等``、``hotspot_alignment_score: 8``、``evidence_id: E1,E2,E3``，
分别导致分数被静默回退 0.5、钳到 1.0、以及触发浪费的重试。GBNF 约束后输出
稳定合法，且解码更快（约 1.7s vs 3.0s）。

设计约束
--------
- 仅约束**固定结构**节点：Q0 / Q1 / Q2 / Q3 / Q4 / Q5c。
- QE / Q5a / Q5b 输出变长列表（证据池 / 质疑 / 辩护），不适合语法硬约束，保持原解析器。
- 语法产物必须与 depth_eval_v4._extract_llm_fields 的正则完全兼容
  （``key: value`` 单行、英文冒号 + 空格、评分为 [0-9.] 串）。

这些语法字符串通过 OpenAI 兼容接口的顶层 ``grammar`` 字段发给 llama-server。
"""

from __future__ import annotations

# 公共子规则 --------------------------------------------------------------
# NOTE: GBNF 中换行写作 "\n"；此处用普通字符串，\n 即换行转义交给 llama.cpp 解析。
_COMMON = (
    "line ::= [^\\n]+\n"
    'nl ::= "\\n"\n'
    # 评分严格 [0,1]：0 / 0.x / 1 / 1.0（1 开头仅允许纯 0 小数，杜绝 1.35 越界）
    'score ::= "0" ("." [0-9]+)? | "1" ("." "0"+)?\n'
    # 带符号浮点（Q5c delta，范围 -0.08..0.12，最终由上层 clamp 兜底）
    'sfloat ::= "-"? "0" ("." [0-9]+)?\n'
)


def _eid_rule(valid_ids: list[str]) -> str:
    """构建 evidence_id 枚举规则：从证据池 ID 中选，或 none。"""
    ids = [v for v in valid_ids if v]
    if not ids:
        return 'eid ::= "none"\n'
    alts = " | ".join(f'"{v}"' for v in ids)
    return f'eid ::= ({alts} | "none")\n'


def q0_grammar() -> str:
    """Q0 整体印象：reasoning / evidence / has_substance(bool) / expectation(score)。"""
    root = (
        'root ::= "reasoning: " line nl '
        '"evidence: " line nl '
        '"has_substance: " bool nl '
        '"expectation: " score\n'
        'bool ::= "true" | "false"\n'
    )
    return root + _COMMON


def q1_grammar() -> str:
    """Q1 类型判别：reasoning / evidence / type(A-D) / secondary_type(A-D|none) / confidence。"""
    root = (
        'root ::= "reasoning: " line nl '
        '"evidence: " line nl '
        '"type: " ptype nl '
        '"secondary_type: " stype nl '
        '"confidence: " score\n'
        'ptype ::= "A" | "B" | "C" | "D"\n'
        'stype ::= "A" | "B" | "C" | "D" | "none"\n'
    )
    return root + _COMMON


def q2_grammar(valid_ids: list[str]) -> str:
    """Q2 创新与热点：reasoning / core_contribution / novelty / hotspot / evidence_id。"""
    root = (
        'root ::= "reasoning: " line nl '
        '"core_contribution: " line nl '
        '"novelty_score: " score nl '
        '"hotspot_alignment_score: " score nl '
        '"evidence_id: " eid\n'
    )
    return root + _eid_rule(valid_ids) + _COMMON


def q3_grammar(valid_ids: list[str]) -> str:
    """Q3 严谨性：reasoning / rigor_score / missing_items / evidence_id。"""
    root = (
        'root ::= "reasoning: " line nl '
        '"rigor_score: " score nl '
        '"missing_items: " line nl '
        '"evidence_id: " eid\n'
    )
    return root + _eid_rule(valid_ids) + _COMMON


def q4_grammar(valid_ids: list[str]) -> str:
    """Q4 影响力与可复现性：reasoning / influence / reproducibility / evidence_id。"""
    root = (
        'root ::= "reasoning: " line nl '
        '"influence_score: " score nl '
        '"reproducibility_score: " score nl '
        '"evidence_id: " eid\n'
    )
    return root + _eid_rule(valid_ids) + _COMMON


def q5c_grammar() -> str:
    """Q5c 主席裁决：reasoning / delta(sfloat) / verdict(enum)。"""
    root = (
        'root ::= "reasoning: " line nl '
        '"delta: " sfloat nl '
        '"verdict: " verdict\n'
        'verdict ::= "accept" | "minor_revision" | "major_revision" | "reject"\n'
    )
    return root + _COMMON


def q234_grammar(valid_ids: list[str]) -> str:
    """Q234 合并多维评分（v4.2）：单次输出五维分数 + 三个维度证据 ID。"""
    root = (
        'root ::= "reasoning: " line nl '
        '"core_contribution: " line nl '
        '"novelty_score: " score nl '
        '"hotspot_alignment_score: " score nl '
        '"rigor_score: " score nl '
        '"missing_items: " line nl '
        '"influence_score: " score nl '
        '"reproducibility_score: " score nl '
        '"q2_evidence_id: " eid nl '
        '"q3_evidence_id: " eid nl '
        '"q4_evidence_id: " eid\n'
    )
    return root + _eid_rule(valid_ids) + _COMMON


def qf_grammar(valid_ids: list[str]) -> str:
    """QF 图文一致性审查（v4.2）：reasoning / figure_consistency / flags / evidence_id。"""
    root = (
        'root ::= "reasoning: " line nl '
        '"figure_consistency_score: " score nl '
        '"inconsistency_flags: " line nl '
        '"evidence_id: " eid\n'
    )
    return root + _eid_rule(valid_ids) + _COMMON
