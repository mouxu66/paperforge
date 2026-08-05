"""
DEPTH v4.1 学术论文审稿流水线 —— Prompt 模板（9 节点）。

每个节点的 Prompt 作为独立字符串常量，方便后续微调措辞。
Q2-Q4 评分含 0.9/0.7/0.5 分段锚定描述，提升跨论文可比性。
QE 输出含 keywords 字段，用于证据双重定位。
"""

from __future__ import annotations

# =============================================================================
# 默认热点词列表
# =============================================================================
DEFAULT_HOTSPOTS = [
    "大模型对齐",
    "扩散模型",
    "多模态融合",
    "强化学习",
    "图神经网络",
    "自监督学习",
    "知识图谱",
    "联邦学习",
    "因果推断",
    "可解释AI",
    "边缘计算",
    "量子机器学习",
]


# =============================================================================
# Q0：整体印象（纯文本 + CRITICAL 前缀）
# =============================================================================
PROMPT_Q0 = """[CRITICAL: Output ONLY these 4 lines. NO thinking. NO JSON. Start immediately.]

你是顶会领域主席。阅读以下摘要+引言，给出整体判断。

论文：
{paper}

示例：
reasoning: 提出了全新的理论框架并给出了证明思路。
evidence: 首次将X与Y统一在一个框架中
has_substance: true
expectation: 0.75"""


# =============================================================================
# Q1：类型判别（纯文本 + CRITICAL 前缀）
# =============================================================================
PROMPT_Q1 = """[CRITICAL: Output ONLY these 5 lines. NO thinking. NO JSON. Start immediately.]

你是资深审稿人。判断论文主类型和辅类型。A=理论突破 B=方法改进 C=应用迁移 D=综述。secondary_type 填 A/B/C/D 或 none。

论文：
{paper}

示例：
reasoning: 提出新损失函数并从理论上证明了收敛性，同时应用于医学图像分割。
evidence: 证明了该损失的全局收敛性
type: A
secondary_type: C
confidence: 0.88"""


# =============================================================================
# QE：全局证据池（纯文本输出，彻底避开推理模型 JSON 限制）
# =============================================================================
PROMPT_QE = """从以下论文中提取5-7条关键证据。每条证据单独一行，格式为"E编号: 证据内容"。

不要输出 JSON、不要输出思考过程、不要输出解释。直接列出证据。

论文：
{paper}

示例输出：
E1: 我们提出了一种基于注意力机制的新型网络架构Transformer。
E2: 该模型在WMT 2014英德翻译任务上达到28.4 BLEU分数。
E3: Transformer的训练时间远少于RNN/CNN模型。"""


# =============================================================================
# Q2：创新与热点（纯文本输出 + 反思考前缀 + 大 token 预算）
# =============================================================================
PROMPT_Q2 = """[CRITICAL: DO NOT output thinking process. Output ONLY the 5 key:value lines. No JSON, no explanation.]

评估以下论文的创新程度和热点契合度。评分标准：0.9=颠覆性创新（新范式/全新方法论），0.7=显著改进（新变体/改进基线），0.5=渐进式；仅范式突破或全新方法论可给 ≥0.85，单纯基线改进/新数据集/新任务应用归 0.5–0.7，不要虚高。

论文类型：{paper_type}
热点：{hotspots}
证据池：{evidence_pool_text}

论文：
{paper}

直接输出以下5行（key: value格式，每行一个字段）：
reasoning: 1-2句话分析
core_contribution: 一句话概括核心贡献
novelty_score: 创新程度数值(0-1)
hotspot_alignment_score: 热点契合度数值(0-1)
evidence_id: 证据ID（如E1，无证据则填none）

示例：
reasoning: 该工作提出了新的图神经网络变体并引入时间衰减机制。
core_contribution: 基于时间衰减图神经网络的动态异常检测框架
novelty_score: 0.82
hotspot_alignment_score: 0.88
evidence_id: E1"""


# =============================================================================
# Q3：类型自适应严谨性审查（纯文本 + 示例）
# =============================================================================
PROMPT_Q3_A = """[CRITICAL: Output ONLY these 4 lines. NO thinking. NO JSON. Start immediately.]

理论论文严谨性审查。评分：0.9=证明完整假设清晰，0.7=缺边界讨论，0.5=证明跳跃。
论文类型：{paper_type}（理论{secondary_type_info}）
证据：{evidence_pool_text}{secondary_checklist}
论文：{paper}

示例：
reasoning: 证明完整但未讨论假设失效场景。
rigor_score: 0.65
missing_items: 边界条件讨论
evidence_id: E2"""

PROMPT_Q3_B = """[CRITICAL: Output ONLY these 4 lines. NO thinking. NO JSON. Start immediately.]

方法论文实验严谨性审查。评分：0.9=消融+强基线+显著性+开源，0.7=缺1-2项，0.5=仅基本对比。
论文类型：{paper_type}（方法{secondary_type_info}）
证据：{evidence_pool_text}{secondary_checklist}
论文：{paper}

示例：
reasoning: 缺消融实验和代码开源，但有强基线对比。
rigor_score: 0.60
missing_items: 消融实验, 代码开源
evidence_id: E3"""

PROMPT_Q3_C = """[CRITICAL: Output ONLY these 4 lines. NO thinking. NO JSON. Start immediately.]

应用论文实践严谨性审查。评分：0.9=多真实场景+指标完整+局限深入，0.7=场景不足/指标缺失，0.5=依赖仿真。
论文类型：{paper_type}（应用{secondary_type_info}）
证据：{evidence_pool_text}{secondary_checklist}
论文：{paper}

示例：
reasoning: 多场景验证但未讨论局限性。
rigor_score: 0.70
missing_items: 局限性讨论
evidence_id: E4"""

PROMPT_Q3_D = """[CRITICAL: Output ONLY these 4 lines. NO thinking. NO JSON. Start immediately.]

综述论文严谨性审查。评分：0.9=文献全面+框架自洽+批判深入，0.7=覆盖较广有遗漏，0.5=覆盖面窄缺批判。
论文类型：{paper_type}（综述{secondary_type_info}）
证据：{evidence_pool_text}{secondary_checklist}
论文：{paper}

示例：
reasoning: 文献覆盖全面但缺方法优劣对比。
rigor_score: 0.75
missing_items: 方法对比分析
evidence_id: E1"""

Q3_PROMPT_VARIANTS = {"A": PROMPT_Q3_A, "B": PROMPT_Q3_B, "C": PROMPT_Q3_C, "D": PROMPT_Q3_D}

Q3_SECONDARY_CHECKLIST = {
    "A": "（辅类型-理论）额外检查：理论推导是否有严格证明？假设是否清晰？",
    "B": "（辅类型-方法）额外检查：实验是否包含消融和显著性检验？",
    "C": "（辅类型-应用）额外检查：是否在真实场景中验证？局限性是否讨论？",
    "D": "（辅类型-综述）额外检查：文献是否系统全面？是否有批判性分析？",
}


# =============================================================================
# Q4：影响力与可复现性（纯文本 + 大 token 预算）
# =============================================================================
PROMPT_Q4 = """[CRITICAL: Output ONLY these 4 lines. NO thinking. NO JSON. Start immediately.]

评估论文的影响力与可复现性。影响力：0.9=里程碑级，0.7=显著推动，0.5=细分领域参考。可复现：0.9=完整代码+数据+环境，0.7=详细实验设置（超参/数据集/环境描述），0.5=仅算法描述无细节。若正文未提供任何代码/数据/权重公开获取方式（无 github/huggingface/开源声明），reproducibility 不应高于 0.6；只有完全缺少实验细节时才给 0.5 以下。

证据池：{evidence_pool_text}
论文：
{paper}

示例：
reasoning: 该方法大幅提升效率，实验设置详细（超参、数据集、评估指标完备），虽未开源但可复现性较高。
influence_score: 0.82
reproducibility_score: 0.72
evidence_id: E5"""


# =============================================================================
# Q234：合并多维评分（v4.2 降本核心 —— 单次调用替代原 Q2/Q3/Q4 三次调用）
# =============================================================================
# 按类型提取的严谨性锚点（沿用原 Q3 各变体的评分标准，压缩为一行注入合并提示词）
Q234_RIGOR_GUIDE = {
    "A": "严谨性（理论）：0.9=证明完整假设清晰，0.7=缺边界讨论，0.5=证明跳跃。",
    "B": "严谨性（方法）：0.9=消融+强基线+显著性+开源，0.7=缺1-2项，0.5=仅基本对比。",
    "C": "严谨性（应用）：0.9=多真实场景+指标完整+局限深入，0.7=场景不足/指标缺失，0.5=依赖仿真。",
    "D": "严谨性（综述）：0.9=文献全面+框架自洽+批判深入，0.7=覆盖较广有遗漏，0.5=覆盖面窄缺批判。",
}

PROMPT_Q234 = """[CRITICAL: DO NOT output thinking process. Output ONLY the key:value lines below. No JSON, no explanation.]

你是顶会审稿人，基于论文与证据池一次性完成五个维度的评分。评分锚点：
创新：0.9=颠覆性创新（新范式/全新方法论），0.7=显著改进（新变体/改进基线），0.5=渐进式；仅范式突破或全新方法论可给 ≥0.85，单纯基线改进/新数据集/新任务应用归 0.5–0.7，不要虚高。
{rigor_guide}
影响力：0.9=里程碑级，0.7=显著推动，0.5=细分领域参考。
可复现：0.9=完整代码+数据+环境，0.7=详细实验设置（超参/数据集/环境描述），0.5=仅算法描述无细节。若正文未提供任何代码/数据/权重公开获取方式（无 github/huggingface/开源声明），reproducibility 不应高于 0.6。

论文类型：{paper_type}{secondary_type_info}
热点：{hotspots}
证据池（含【图表】前缀的图表证据，可正常引用）：{evidence_pool_text}{secondary_checklist}

论文：
{paper}

直接输出以下 11 行（key: value格式，每行一个字段）：
reasoning: 1-2句话综合分析
core_contribution: 一句话概括核心贡献
novelty_score: 创新程度数值(0-1)
hotspot_alignment_score: 热点契合度数值(0-1)
rigor_score: 严谨性数值(0-1)
missing_items: 缺失项（逗号分隔，无则填none）
influence_score: 影响力数值(0-1)
reproducibility_score: 可复现性数值(0-1)
q2_evidence_id: 支撑创新判断的证据ID（如E1，无则none）
q3_evidence_id: 支撑严谨判断的证据ID
q4_evidence_id: 支撑影响/复现判断的证据ID

示例：
reasoning: 提出时态注意力GNN新架构，实验含消融与显著性检验，但缺大规模图可扩展性讨论。
core_contribution: 基于时态注意力图神经网络的动态异常检测框架
novelty_score: 0.82
hotspot_alignment_score: 0.88
rigor_score: 0.78
missing_items: 大规模图可扩展性讨论
influence_score: 0.85
reproducibility_score: 0.88
q2_evidence_id: E1
q3_evidence_id: E4
q4_evidence_id: E5"""


# =============================================================================
# QF：图文一致性审查（v4.2 新增 —— 消费 PaperFigure 图表证据）
# =============================================================================
PROMPT_QF = """[CRITICAL: Output ONLY these 4 lines. NO thinking. NO JSON. Start immediately.]

你是严谨的审稿人，专门核对论文图表与正文的一致性。以下提供该论文的图表信息（作者图注 caption / OCR文字 / Qwen语义桥解读摘要）与正文节选。
核对要点：图表数据是否支撑正文声明？正文与图表有无矛盾？图表是否缺失关键信息（基线/单位/图例）？

【重要】作者图注(caption)是论文作者亲自撰写的对图表内容的高度概括，属于零幻觉、高可信信号；OCR文字与Qwen语义桥摘要可能包含识别/理解误差，请仅作为辅助参考，不要过度依赖。当 caption 与 OCR/Qwen语义桥摘要冲突时，请以 caption 为准。

论文（摘要+结论节选）：
{paper}

图表信息：
{figure_text}

证据池：{evidence_pool_text}

输出以下4行（key: value格式，每行一个字段）：
reasoning: 1-2句话分析
figure_consistency_score: 图文一致性数值(0-1)（0.9=图表充分支撑且无矛盾，0.5=部分支撑或无法完全判断，0.2=存在明显矛盾）
inconsistency_flags: 发现的问题（逗号分隔，无则填none）
evidence_id: 最相关证据ID（如E1，无则none）

示例：
reasoning: 图3的F1曲线与正文报告的89.3%一致，但图5缺少基线对比曲线。
figure_consistency_score: 0.65
inconsistency_flags: 图5缺少基线对比
evidence_id: E3"""


# =============================================================================
# Q5a：质疑者（纯文本 + 大 token 预算）
# =============================================================================
PROMPT_Q5A = """[CRITICAL: Output ONLY the critique lines + evidence_id. NO thinking. NO JSON. Start immediately.]

找出论文最可能被拒绝的2-3个关键理由。已知评分：类型={paper_type}/创新={novelty}/热点={hotspot}/严谨={rigor}/影响={influence}/复现={reproducibility}/图文={figure_consistency}。

证据池：{evidence_pool_text}
论文：
{paper}

重要提示：证据池中若某项以 [FATAL] 标记（尤其来自图文一致性的 out-of-range claim），说明该证据对应的问题可能直接动摇论文结论，应优先、严肃地考虑映射为 severity=fatal 的 critique point，并引用其 evidence_id；但若该问题对全文结论影响有限，仍可判定为 minor。

示例：
critique: 图1 报告 accuracy=1.8 超出坐标轴范围，与正文关键结论矛盾 | severity: fatal
evidence_id: E5

输出格式（每条质疑一行，severity填fatal或minor）：
critique: 消融实验缺失 | severity: fatal
critique: 缺乏真实场景验证 | severity: minor
evidence_id: E3

示例：
critique: 消融实验缺失无法证明各模块贡献 | severity: fatal
critique: 仅在仿真环境测试缺乏真实场景 | severity: minor
evidence_id: E3"""


# =============================================================================
# Q5b：辩护者（纯文本 + 大 token 预算）
# =============================================================================
PROMPT_Q5B = """[CRITICAL: Output ONLY the defense lines + evidence_id. NO thinking. NO JSON. Start immediately.]

你是论文作者。针对以下质疑逐条辩护。严格基于原文，未涉及的回答"原文暂未涉及，将在终稿补充"。

审稿质疑：
{critique_points}

证据池：{evidence_pool_text}
论文：
{paper}

输出格式（每条辩护一行，顺序对应质疑）：
defense: 消融实验已在附录A.3完成，正文因篇幅未展示
defense: 原文暂未涉及，将在终稿补充
evidence_id: E2

示例：
defense: 我们已在附录中补充了消融实验的完整结果
defense: 原文暂未涉及，将在终稿补充
evidence_id: E2"""


# =============================================================================
# Q5c：主席裁决（纯文本 + CRITICAL 前缀）
# =============================================================================
PROMPT_Q5C = """[CRITICAL: Output ONLY these 3 lines. NO thinking. NO JSON. Start immediately.]

你是期刊主编，基于以下信息给出最终裁决。delta 范围 [{delta_min}, {delta_max}]，最终分 = clamp({base_score:.2f} + delta, 0, 1)。verdict 仅能为 accept/minor_revision/major_revision/reject。

多维评分：创新={novelty}/热点={hotspot}/严谨={rigor}/影响={influence}/复现={reproducibility}/图文={figure_consistency}/基础分={base_score:.2f}

质疑：
{critique_points}

辩护：
{defense_points}

论文摘要与结论：
{paper_abstract_conclusion}

示例：
reasoning: 创新性突出且实验充分，辩论后质疑可控，建议接收。
delta: 0.05
verdict: accept"""


# =============================================================================
# COT 变体：允许模型输出 <thinking>...</thinking> 后再给出关键字段。
# 解析时会先剔除 thinking 块，再按原正则提取。
# =============================================================================
_COT_HINT = """你可以在 <thinking>...</thinking> 标签内做简短推理，
但标签之后必须按下方示例格式输出最终字段，不要省略任何字段。"""


PROMPT_Q0_COT = f"""你是顶会领域主席。阅读以下摘要+引言，给出整体判断。

{_COT_HINT}

论文：
{{paper}}

示例：
<thinking>这篇论文提出了新的理论框架...</thinking>
reasoning: 提出了全新的理论框架并给出了证明思路。
evidence: 首次将X与Y统一在一个框架中
has_substance: true
expectation: 0.75"""


PROMPT_Q1_COT = f"""你是资深审稿人。判断论文主类型和辅类型。A=理论突破 B=方法改进 C=应用迁移 D=综述。secondary_type 填 A/B/C/D 或 none。

{_COT_HINT}

论文：
{{paper}}

示例：
<thinking>本文主要提出新损失函数...</thinking>
reasoning: 提出新损失函数并从理论上证明了收敛性，同时应用于医学图像分割。
evidence: 证明了该损失的全局收敛性
type: A
secondary_type: C
confidence: 0.88"""


PROMPT_Q2_COT = f"""评估以下论文的创新程度和热点契合度。评分标准：0.9=颠覆性创新（新范式/全新方法论），0.7=显著改进（新变体/改进基线），0.5=渐进式；仅范式突破或全新方法论可给 ≥0.85，单纯基线改进/新数据集/新任务应用归 0.5–0.7，不要虚高。

{_COT_HINT}

论文类型：{{paper_type}}
热点：{{hotspots}}
证据池：{{evidence_pool_text}}

论文：
{{paper}}

<thinking>...</thinking> 后直接输出以下5行（key: value格式，每行一个字段）：
reasoning: 1-2句话分析
core_contribution: 一句话概括核心贡献
novelty_score: 创新程度数值(0-1)
hotspot_alignment_score: 热点契合度数值(0-1)
evidence_id: 证据ID（如E1，无证据则填none）"""


PROMPT_Q3_A_COT = f"""理论论文严谨性审查。评分：0.9=证明完整假设清晰，0.7=缺边界讨论，0.5=证明跳跃。

{_COT_HINT}

论文类型：{{paper_type}}（理论{{secondary_type_info}}）
证据：{{evidence_pool_text}}{{secondary_checklist}}
论文：{{paper}}

<thinking>...</thinking> 后输出：
reasoning: 证明完整但未讨论假设失效场景。
rigor_score: 0.65
missing_items: 边界条件讨论
evidence_id: E2"""


PROMPT_Q3_B_COT = f"""方法论文实验严谨性审查。评分：0.9=消融+强基线+显著性+开源，0.7=缺1-2项，0.5=仅基本对比。

{_COT_HINT}

论文类型：{{paper_type}}（方法{{secondary_type_info}}）
证据：{{evidence_pool_text}}{{secondary_checklist}}
论文：{{paper}}

<thinking>...</thinking> 后输出：
reasoning: 缺消融实验和代码开源，但有强基线对比。
rigor_score: 0.60
missing_items: 消融实验, 代码开源
evidence_id: E3"""


PROMPT_Q3_C_COT = f"""应用论文实践严谨性审查。评分：0.9=多真实场景+指标完整+局限深入，0.7=场景不足/指标缺失，0.5=依赖仿真。

{_COT_HINT}

论文类型：{{paper_type}}（应用{{secondary_type_info}}）
证据：{{evidence_pool_text}}{{secondary_checklist}}
论文：{{paper}}

<thinking>...</thinking> 后输出：
reasoning: 多场景验证但未讨论局限性。
rigor_score: 0.70
missing_items: 局限性讨论
evidence_id: E4"""


PROMPT_Q3_D_COT = f"""综述论文严谨性审查。评分：0.9=文献全面+框架自洽+批判深入，0.7=覆盖较广有遗漏，0.5=覆盖面窄缺批判。

{_COT_HINT}

论文类型：{{paper_type}}（综述{{secondary_type_info}}）
证据：{{evidence_pool_text}}{{secondary_checklist}}
论文：{{paper}}

<thinking>...</thinking> 后输出：
reasoning: 文献覆盖全面但缺方法优劣对比。
rigor_score: 0.75
missing_items: 方法对比分析
evidence_id: E1"""


Q3_PROMPT_VARIANTS_COT = {
    "A": PROMPT_Q3_A_COT,
    "B": PROMPT_Q3_B_COT,
    "C": PROMPT_Q3_C_COT,
    "D": PROMPT_Q3_D_COT,
}


PROMPT_Q4_COT = f"""评估论文的影响力与可复现性。影响力：0.9=里程碑级，0.7=显著推动，0.5=细分领域参考。可复现：0.9=完整代码+数据+环境，0.7=详细实验设置（超参/数据集/环境描述），0.5=仅算法描述无细节。重要：代码未公开 ≠ 不可复现。

{_COT_HINT}

证据池：{{evidence_pool_text}}
论文：
{{paper}}

<thinking>...</thinking> 后输出：
reasoning: 该方法大幅提升效率，实验设置详细，虽未开源但可复现性较高。
influence_score: 0.82
reproducibility_score: 0.72
evidence_id: E5"""


PROMPT_Q5A_COT = f"""找出论文最可能被拒绝的2-3个关键理由。已知评分：类型={{paper_type}}/创新={{novelty}}/热点={{hotspot}}/严谨={{rigor}}/影响={{influence}}/复现={{reproducibility}}/图文={{figure_consistency}}。

{_COT_HINT}

证据池：{{evidence_pool_text}}
论文：
{{paper}}

<thinking>...</thinking> 后输出（每条质疑一行，severity填fatal或minor）：
critique: 消融实验缺失 | severity: fatal
critique: 缺乏真实场景验证 | severity: minor
evidence_id: E3"""


PROMPT_Q5B_COT = f"""你是论文作者。针对以下质疑逐条辩护。严格基于原文，未涉及的回答"原文暂未涉及，将在终稿补充"。

{_COT_HINT}

审稿质疑：
{{critique_points}}

证据池：{{evidence_pool_text}}
论文：
{{paper}}

<thinking>...</thinking> 后输出（每条辩护一行，顺序对应质疑）：
defense: 消融实验已在附录A.3完成，正文因篇幅未展示
defense: 原文暂未涉及，将在终稿补充
evidence_id: E2"""


PROMPT_Q5C_COT = f"""你是期刊主编，基于以下信息给出最终裁决。delta 范围 [{{delta_min}}, {{delta_max}}]，最终分 = clamp({{base_score:.2f}} + delta, 0, 1)。verdict 仅能为 accept/minor_revision/major_revision/reject。

{_COT_HINT}

多维评分：创新={{novelty}}/热点={{hotspot}}/严谨={{rigor}}/影响={{influence}}/复现={{reproducibility}}/图文={{figure_consistency}}/基础分={{base_score:.2f}}

质疑：
{{critique_points}}

辩护：
{{defense_points}}

论文摘要与结论：
{{paper_abstract_conclusion}}

<thinking>...</thinking> 后输出：
reasoning: 创新性突出且实验充分，辩论后质疑可控，建议接收。
delta: 0.05
verdict: accept"""


# =============================================================================
# DWM 基础权重表
# =============================================================================
TYPE_WEIGHTS = {
    "A": {"beta": 0.50, "gamma": 0.20, "delta": 0.15, "epsilon": 0.15},
    "B": {"beta": 0.25, "gamma": 0.45, "delta": 0.15, "epsilon": 0.15},
    "C": {"beta": 0.20, "gamma": 0.25, "delta": 0.30, "epsilon": 0.25},
    "D": {"beta": 0.30, "gamma": 0.25, "delta": 0.20, "epsilon": 0.25},
}

ELASTIC_THRESHOLD = 0.8
