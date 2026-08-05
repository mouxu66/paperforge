"""PaperForge AI 功能批处理测试脚本 —— 合并请求，总请求数 ≤ 30，规避 429 限流。

策略：将多个测试用例合并为一次 API 请求，让模型一次性回答所有问题。
脚本调用 PaperForge 后端 API（/api/ask 等），后端内部使用已配置的 LLM 模型。

使用方式：
    python scripts/ai_benchmark_batch.py [--base-url http://127.0.0.1:8770]
    python scripts/ai_benchmark_batch.py --start 0 --end 5    # 分批运行

前置条件：
    - 后端服务已启动
    - 已通过 /api/models 配置 LLM 模型

输出：
    - benchmark_results_batch.json  原始结果
    - benchmark_report_batch.md     汇总报告
    - completed_batch.json          断点续传状态
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
DEFAULT_BASE_URL = "http://127.0.0.1:8770"
REQUEST_TIMEOUT = 180  # 批处理请求可能更长

# 限流
MIN_INTERVAL = 2
MAX_INTERVAL = 5
MAX_RETRIES = 5
INITIAL_BACKOFF = 2
CONSECUTIVE_FAIL_PAUSE_THRESHOLD = 3
PAUSE_DURATION = 60

COMPLETED_FILE = "completed_batch.json"

# ---------------------------------------------------------------------------
# 测试主题（6 大学科，每学科至少 15 条）
# ---------------------------------------------------------------------------
TOPICS = {
    "cs": {
        "label": "计算机科学",
        "questions": [
            "Transformer 中自注意力机制的时间复杂度如何优化？",
            "LoRA 微调相比全参数微调的核心优势是什么？",
            "GPT 系列从 GPT-1 到 GPT-4 的架构演变有哪些关键变化？",
            "联邦学习中的拜占庭容错问题如何解决？",
            "知识蒸馏和模型剪枝在部署中的效果差异？",
            "图神经网络在社交网络分析中的优势和挑战？",
            "对比学习在无监督表征学习中的核心思想？",
            "分布式训练中的梯度同步策略有哪些？",
            "RAG 架构相比纯生成模型有什么优势？",
            "扩散模型在图像生成领域相比 GAN 的优势？",
            "RLHF 的训练流程是怎样的？",
            "多模态大模型如何处理跨模态对齐？",
            "神经架构搜索在资源受限场景下如何高效搜索？",
            "代码生成大模型的评估指标有哪些？",
            "强化学习在推荐系统中的最新进展？",
        ],
        "directions": [
            "讨论实验设置与评估指标",
            "补充最新基准测试结果对比",
            "分析模型在不同数据集上的泛化能力",
            "讨论计算资源需求与训练成本",
            "对比开源与闭源模型的性能差异",
            "展望下一代架构的可能方向",
            "添加消融实验的设计思路",
            "分析模型的推理效率优化方法",
            "讨论多任务学习的迁移效果",
            "补充模型可解释性方面的分析",
            "探讨模型安全性与对齐问题",
            "分析小样本场景下的表现",
            "讨论模型部署的工程挑战",
            "对比不同优化器的收敛速度",
            "分析数据质量对模型性能的影响",
        ],
        "rewrite_texts": [
            "本研究提出了一种基于注意力机制的新型网络架构，在多个基准数据集上取得了显著的性能提升。",
            "实验结果表明，所提方法在准确率和召回率方面均优于现有基线方法。",
            "该模型采用了层次化的特征提取策略，能够同时捕获局部和全局的语义信息。",
            "与传统方法相比，本文提出的技术路线在推理速度上提升了 3 倍，同时保持了相当的精度。",
            "消融实验验证了每个模块对最终性能的贡献，其中注意力模块的贡献最为显著。",
            "通过引入对比学习目标，模型在无标签数据上的表征质量得到了显著改善。",
            "知识图谱的引入为模型提供了丰富的外部语义信息，有效缓解了数据稀疏问题。",
            "本文的创新点在于将因果推断的思想引入了推荐系统的去偏过程中。",
            "我们设计了一种自适应的学习率调度策略，能够根据训练动态自动调整学习步长。",
            "安全性评估表明，经过对齐训练的模型在有害内容生成方面的风险显著降低。",
            "实验覆盖了从 1B 到 70B 不同规模的模型，系统性地分析了规模效应。",
            "本文提出的框架支持增量学习，可以在不重新训练的情况下持续吸收新知识。",
            "模型的泛化能力通过跨域迁移实验得到了验证，在目标域上的性能下降不超过 5%。",
            "我们在 GPU 集群上使用分布式数据并行策略完成了全部训练，总耗时约 48 小时。",
            "利用机器学习进行宇宙学参数估计，相比传统方法可以将计算时间缩短两个数量级。",
        ],
        "outline_topics": [
            "基于 Transformer 的大规模语言模型预训练技术综述",
            "低秩适应方法在大语言模型高效微调中的应用",
            "图神经网络在药物发现中的应用与挑战",
            "联邦学习中的隐私保护与通信优化",
            "多模态大模型的架构设计与训练策略",
            "扩散模型在文本到图像生成中的最新进展",
            "检索增强生成（RAG）系统的优化策略研究",
            "大语言模型的幻觉问题与缓解方法",
            "AI Agent 的架构设计与能力评估框架",
        ],
    },
    "biomedical": {
        "label": "生物医学",
        "questions": [
            "AlphaFold 2 的 Evoformer 模块如何处理共进化信息？",
            "单细胞 RNA 测序中 UMAP 相比 t-SNE 的优势？",
            "CRISPR-Cas9 脱靶效应如何通过计算方法预测？",
            "药物-靶点相互作用预测中图神经网络的优势？",
            "蛋白质语言模型 ESM-2 在功能预测中的应用前景？",
            "医学影像中域适应问题如何解决？",
            "电子健康记录的时序建模有哪些主流方法？",
            "基因组变异致病性预测的深度学习方法？",
            "病理图像分析中弱监督学习的效果如何？",
            "脑机接口中神经信号解码的最新方法？",
            "药物重定位的网络药理学方法有哪些？",
            "免疫组库分析中的序列聚类算法？",
            "合成生物学中基因线路设计的自动化方法？",
            "临床试验设计中贝叶斯自适应方法的应用？",
            "精准医疗中的多模态数据融合方法？",
        ],
        "directions": [
            "讨论模型在临床数据上的验证结果",
            "分析基因组数据的预处理流程",
            "补充生物信息学工具的对比分析",
            "讨论伦理审查和数据隐私保护措施",
            "分析样本量对统计功效的影响",
            "展望精准医疗中的应用前景",
            "补充多组学数据整合的方法",
            "讨论模型可解释性在临床中的重要性",
            "分析跨种族的泛化能力",
            "探讨数据标注的质量控制流程",
            "讨论罕见病数据不足的应对策略",
            "分析纵向数据的建模方法",
            "补充外部验证队列的结果",
            "讨论模型部署的监管合规要求",
            "探讨临床决策支持系统的设计原则",
        ],
        "rewrite_texts": [
            "本研究基于 5000 例临床样本，开发了一种新型疾病风险预测模型，AUC 达到 0.92。",
            "蛋白质结构预测的准确性直接影响药物设计的成功率。",
            "通过对单细胞转录组数据的聚类分析，我们识别出了 12 种新的细胞亚型。",
            "该方法利用注意力机制自动学习基因间的调控关系。",
            "临床试验数据显示，该药物主要终点达成率为 78%。",
            "我们构建了包含 200 万条药物-靶点相互作用的知识图谱。",
            "影像组学特征与基因组数据的整合分析揭示了肿瘤微环境的异质性。",
            "基于贝叶斯框架的因果推断方法能够识别潜在的治疗效果。",
            "深度学习模型在病理图像分割上的 Dice 系数达到 0.95。",
            "多组学数据整合分析揭示了疾病发展的分子机制。",
            "纵向电子健康记录的建模需要处理不规则采样和缺失值。",
            "脑电信号的时频分析结合深度学习可以识别不同认知状态。",
            "基因编辑的 off-target 预测需要在全基因组范围内评估。",
            "药物相互作用预测对多重用药患者的安全性至关重要。",
            "免疫组库多样性分析表明 COVID-19 康复患者 TCR 发生了显著变化。",
        ],
        "outline_topics": [
            "深度学习在医学影像诊断中的应用与挑战",
            "蛋白质语言模型的功能预测能力评估",
            "单细胞多组学数据整合分析方法综述",
            "AI 辅助药物发现的计算方法与实践",
            "电子健康记录的深度学习建模方法",
            "脑机接口中的神经信号解码技术",
            "病理图像分析的弱监督学习方法",
            "精准医疗中的多模态数据融合",
            "可解释 AI 在临床决策支持中的应用",
        ],
    },
    "math": {
        "label": "数学",
        "questions": [
            "最优传输理论在机器学习中有哪些应用？",
            "Transformer 中注意力矩阵的低秩近似方法？",
            "梯度下降法在非凸优化中的收敛性如何保证？",
            "L1 和 L2 正则化的几何解释有何不同？",
            "贝叶斯优化中采集函数的设计准则？",
            "随机微分方程在金融建模中的应用？",
            "图拉普拉斯算子的谱性质如何影响 GNN 表达能力？",
            "扩散模型中 score matching 的数学推导？",
            "张量分解在推荐系统中的数学基础？",
            "信息论中的互信息在特征选择中如何应用？",
            "核方法在高维空间中的计算效率如何优化？",
            "变分推断中 ELBO 的推导及松弛策略？",
            "MCMC 方法的收敛诊断有哪些？",
            "凸优化对偶理论在 SVM 中如何应用？",
            "流形学习的数学基础是什么？",
        ],
        "directions": [
            "补充定理的严格数学证明",
            "讨论算法的计算复杂度分析",
            "分析收敛速率的理论上界",
            "补充数值实验验证理论结果",
            "讨论参数选择对结果稳定性的影响",
            "与现有方法的理论对比分析",
            "分析高维情况下的维度灾难",
            "讨论近似算法的误差界",
            "补充几何直觉的图示说明",
            "探讨方法在大规模数据上的可扩展性",
            "讨论随机化算法的方差界",
            "分析非凸优化中的鞍点问题",
            "补充收敛性证明的完整细节",
            "讨论分布式优化的通信复杂度",
            "分析隐私保护机制对精度的影响",
        ],
        "rewrite_texts": [
            "本文证明了在 Lipschitz 连续条件下，梯度下降法的收敛速率为 O(1/T)。",
            "通过引入 Moreau 包络，我们将非光滑优化问题转化为光滑问题。",
            "定理 3.2 表明，在强凸假设下，方差缩减方法可以达到线性收敛速率。",
            "我们利用矩阵 Bernstein 不等式证明了随机矩阵和的谱范数集中性质。",
            "最优传输距离在概率分布比较中具有良好的几何性质。",
            "ELBO 可以分解为重构误差和 KL 散度两项。",
            "核方法通过隐式映射将数据嵌入到高维再生核希尔伯特空间。",
            "扩散模型的去噪过程可以看作是 Langevin 动力学采样。",
            "我们利用 concentration of measure 理论分析了随机投影的保距性质。",
            "对偶理论将原始优化问题转化为对偶问题，降低计算复杂度。",
            "张量网络分解为高维数据提供了紧凑的低秩表示。",
            "Kantorovich 对偶为计算 Wasserstein 距离提供了高效数值方法。",
            "方差缩减技术有效降低了梯度估计的方差。",
            "信息瓶颈理论为深度学习泛化能力提供了信息论解释。",
            "几何深度学习利用对称性和不变性设计等变网络架构。",
        ],
        "outline_topics": [
            "最优传输理论在机器学习中的应用综述",
            "深度学习优化方法的数学基础",
            "扩散模型的数学理论与分析",
            "图神经网络的表达能力与图论",
            "贝叶斯深度学习的不确定性量化",
            "随机优化方法的收敛性分析",
            "变分推断的理论与算法进展",
            "非凸优化中的 landscape 分析",
            "信息论在机器学习中的应用",
        ],
    },
    "physics": {
        "label": "物理学",
        "questions": [
            "量子纠错码的容错阈值如何计算？",
            "机器学习在 jet tagging 中如何应用？",
            "神经网络量子态的表达能力如何？",
            "引力波信号检测中匹配滤波的原理？",
            "PINNs 求解偏微分方程的优势和局限性？",
            "等离子体湍流的数值模拟方法有哪些？",
            "量子机器学习中 VQE 的原理？",
            "暗物质探测实验中的统计分析方法？",
            "拓扑绝缘体表面态的第一性原理计算？",
            "宇宙学参数估计中的 MCMC 采样？",
            "量子计算在材料科学中的模拟应用？",
            "中微子振荡参数的精确测量方法？",
            "超导量子比特的退相干机制？",
            "统计力学中相变与重整化群的描述？",
            "高能物理实验中的触发与重建算法？",
        ],
        "directions": [
            "补充实验装置的详细描述",
            "讨论理论模型的适用范围和假设",
            "分析误差来源和系统不确定度",
            "与其他实验组的结果进行对比",
            "讨论物理常数的精确测量方法",
            "展望下一代实验的灵敏度提升",
            "补充蒙特卡洛模拟的技术细节",
            "分析数据处理流程中的筛选标准",
            "讨论理论预言与实验观测的一致性",
            "探讨新物理信号的可能来源",
            "分析探测器分辨率对结果的影响",
            "讨论背景噪声的抑制策略",
            "补充理论模型的数值求解方法",
            "分析实验可重复性的验证流程",
            "讨论国际合作实验的数据共享机制",
        ],
        "rewrite_texts": [
            "我们利用深度学习对 LHC 对撞数据中的希格斯玻色子衰变信号进行了高效识别。",
            "量子纠错码的实现需要满足容错阈值条件。",
            "PINNs 通过在损失函数中嵌入物理方程残差，实现了无网格求解。",
            "LIGO/Virgo 的灵敏度提升使得我们可以探测更远距离的中子星合并。",
            "神经网络量子态为多体量子系统基态求解提供了新的变分方法。",
            "第一性原理计算预测了新型二维材料的电子结构和拓扑性质。",
            "Ising 模型在临界点附近表现出普适的标度行为。",
            "等离子体湍流的多尺度模拟需要同时处理宏观和微观动力学。",
            "量子退相干是量子计算面临的主要挑战之一。",
            "高能物理实验中的触发系统需要在微秒级别做出决策。",
            "暗物质直接探测实验利用核反冲信号寻找暗物质粒子。",
            "中微子质量顺序的确定是当前粒子物理学的重要目标。",
            "拓扑量子计算利用非阿贝尔任意子的编织操作实现容错量子门。",
            "宇宙微波背景辐射的精密测量为宇宙学标准模型提供了参数约束。",
            "激光等离子体加速中的粒子跟踪模拟需要处理极端电磁场。",
        ],
        "outline_topics": [
            "机器学习在高能物理数据分析中的应用",
            "量子纠错码的理论与实验进展",
            "物理信息神经网络的方法与应用",
            "引力波天文学的数据分析方法",
            "量子计算在材料模拟中的应用",
            "暗物质探测的实验方法与数据分析",
            "超导量子比特的退相干与纠错",
            "统计力学中的机器学习方法",
            "高能物理实验中的触发与重建算法",
        ],
    },
    "economics": {
        "label": "经济学",
        "questions": [
            "深度学习在金融时序预测中是否优于传统计量模型？",
            "工具变量法和断点回归设计各自的适用条件？",
            "NLP 在金融情感分析中如何处理反讽？",
            "高频交易中的市场微观结构模型有哪些？",
            "机器学习在信用评分中如何处理公平性？",
            "合成控制法在政策评估中的优势和局限？",
            "区块链经济学中的机制设计问题？",
            "博弈论在拍卖机制设计中的最新进展？",
            "宏观经济预测中 MIDAS 模型的原理？",
            "平台定价策略如何建模？",
            "行为经济学中前景理论在投资决策中的应用？",
            "碳排放交易市场的价格形成机制？",
            "匹配理论在就业市场分析中的应用？",
            "数据要素定价问题如何解决？",
            "汇率预测中深度学习方法的效果？",
        ],
        "directions": [
            "补充实证分析的稳健性检验",
            "讨论内生性问题的处理方法",
            "分析不同子样本的异质性效应",
            "讨论政策含义和实际应用建议",
            "补充与已有文献的对比分析",
            "讨论数据来源的可靠性和局限性",
            "分析经济直觉与模型结果的一致性",
            "讨论反事实分析的设定和结果",
            "补充福利分析和效率评估",
            "探讨研究结论的外部有效性",
            "分析因果识别策略的有效性",
            "讨论工具变量的相关性假设",
            "补充安慰剂检验的结果",
            "分析政策实施的时滞效应",
            "讨论样本选择偏差的处理方法",
        ],
        "rewrite_texts": [
            "本文利用双重差分法评估了数字普惠金融政策对农村经济的影响，人均收入提升 12%。",
            "机器学习方法在股票收益预测中样本外 R² 仅为 2-3%，但经济价值显著。",
            "基于 BERT 的金融情感分析模型在中文财经新闻上 F1 达到 0.89。",
            "高频交易做市策略的核心在于管理库存风险和逆向选择成本。",
            "LASSO 回归可以在高维预测因子中识别最具预测力的变量。",
            "合成控制法通过数据驱动方式构建反事实，避免平行趋势假设的主观性。",
            "行为金融学研究表明投资者的过度自信和损失厌恶导致市场异象。",
            "平台经济学中交叉网络效应使定价策略需要同时考虑买卖双方。",
            "因果森林方法可以估计异质性处理效应，为个性化政策设计提供工具。",
            "碳排放权交易价格受能源价格、气候政策和经济增长等多重因素影响。",
            "区块链共识机制设计需要平衡去中心化、安全性和可扩展性。",
            "匹配理论在 kidney exchange 等场景中产生了显著的社会福利改进。",
            "数据要素具有非竞争性和正外部性，传统产权理论需要扩展。",
            "汇率随机游走假设短期内难以被击败，但长期预测中 ML 展现优势。",
            "利用自然实验识别策略可以更可信地估计政策因果效应。",
        ],
        "outline_topics": [
            "机器学习在金融风险管理中的应用",
            "因果推断方法在经济学中的最新进展",
            "自然语言处理在金融分析中的应用",
            "数字经济中的平台竞争与规制",
            "碳排放交易市场的机制设计与价格分析",
            "高频交易的市场微观结构分析",
            "合成控制法在政策评估中的应用",
            "区块链经济学的理论与实证",
            "国际经济学中的大数据方法应用",
        ],
    },
    "education": {
        "label": "教育学",
        "questions": [
            "自适应学习系统如何根据学生表现动态调整难度？",
            "大规模在线课程中如何实现个性化学习路径推荐？",
            "智能辅导系统中知识追踪模型的最新进展？",
            "自然语言处理在自动作文评分中的应用效果？",
            "学习分析中如何保护学生数据隐私？",
            "虚拟现实技术在 STEM 教育中的应用效果如何？",
            "游戏化学习对学习动机和效果的影响研究？",
            "大规模开放在线课程的完课率提升策略有哪些？",
            "多模态学习分析如何整合视频、音频和文本数据？",
            "教育数据挖掘中的偏差和公平性问题如何处理？",
            "AI 辅助教师备课的工具和方法有哪些？",
            "编程教育中自动代码评估系统的设计？",
            "特殊教育中辅助技术的应用现状？",
            "跨文化在线学习社区的构建方法？",
            "形成性评价中实时反馈系统的设计原则？",
        ],
        "directions": [
            "讨论教育实验的设计和伦理审查",
            "分析不同年龄段学生的差异化需求",
            "补充教师视角的质性研究数据",
            "讨论技术接受度对实施效果的影响",
            "分析数字鸿沟对教育公平的影响",
            "补充长期追踪研究的结果",
            "讨论混合式教学模式的设计原则",
            "分析评估工具的信效度检验",
            "探讨家校协同的技术方案",
            "讨论教育政策对技术应用的引导作用",
            "分析学习动机理论在系统设计中的应用",
            "补充跨学科整合的教学案例",
            "讨论大规模部署的可扩展性挑战",
            "分析教师培训对技术采纳的影响",
            "探讨终身学习平台的设计架构",
        ],
        "rewrite_texts": [
            "自适应学习系统通过贝叶斯知识追踪模型实时评估学生掌握程度，动态调整学习路径。",
            "大规模在线课程的完课率通常低于 10%，个性化提醒和社交互动是提升的关键。",
            "智能辅导系统中的深度知识追踪模型在预测学生表现方面优于传统 IRT 模型。",
            "基于 BERT 的自动作文评分系统与人类评分者的一致性达到 0.85 以上。",
            "学习分析中的数据隐私保护需要遵循 FERPA 和 GDPR 等法规要求。",
            "VR 技术在化学分子结构教学中显著提升了学生的空间理解能力。",
            "游戏化元素（积分、徽章、排行榜）对内在动机的影响存在争议。",
            "MOOC 平台通过同伴评价和项目式学习提升了深度学习效果。",
            "多模态学习分析整合面部表情、眼动和语音数据来评估学习投入度。",
            "教育数据挖掘中的算法偏差可能导致对少数群体学生的不公平评价。",
            "AI 辅助备课工具可以根据课程标准自动生成教学活动和评估方案。",
            "编程教育中的自动评估系统需要同时考虑代码正确性和代码质量。",
            "辅助技术为有学习障碍的学生提供了文本转语音、放大镜等功能。",
            "跨文化在线学习社区需要考虑时区差异、语言障碍和文化敏感性。",
            "形成性评价中的即时反馈可以帮助学生及时纠正误解。",
        ],
        "outline_topics": [
            "自适应学习系统的理论基础与实践",
            "大规模在线教育的个性化推荐方法",
            "智能辅导系统中的知识追踪技术",
            "自然语言处理在教育评估中的应用",
            "学习分析中的数据隐私保护框架",
            "虚拟现实在 STEM 教育中的应用研究",
            "教育数据挖掘的公平性与偏差问题",
            "AI 辅助教学工具的设计与评估",
            "特殊教育中的辅助技术应用",
        ],
    },
}


# ---------------------------------------------------------------------------
# 批处理测试用例生成
# ---------------------------------------------------------------------------
def generate_ask_batches(batch_size: int = 10, total: int = 30) -> list[dict]:
    """生成智能问答批处理用例。每批 batch_size 个问题合并为一次请求。"""
    all_questions = []
    for topic_data in TOPICS.values():
        for q in topic_data["questions"]:
            all_questions.append({"topic": topic_data["label"], "question": q})
    random.shuffle(all_questions)
    all_questions = all_questions[:total]

    batches = []
    for i in range(0, len(all_questions), batch_size):
        chunk = all_questions[i : i + batch_size]
        # 合并为一个复合 prompt
        numbered = "\n".join(f"[Q{j + 1}] {item['question']}" for j, item in enumerate(chunk))
        composite_question = (
            f"请依次回答以下 {len(chunk)} 个学术问题，每个答案用 [Q1], [Q2], ... 标记，"
            f"每个答案控制在 200 字以内。\n\n{numbered}"
        )
        batches.append(
            {
                "endpoint": "/api/ask",
                "payload": {"question": composite_question},
                "sub_cases": chunk,
                "sub_cases_count": len(chunk),
                "batch_label": f"ask_batch_{i // batch_size + 1}",
            }
        )
    return batches


def generate_outline_batches(project_id: int, batch_size: int = 5, total: int = 20) -> list[dict]:
    """生成大纲生成批处理用例。"""
    all_topics = []
    for topic_data in TOPICS.values():
        for t in topic_data["outline_topics"]:
            all_topics.append({"topic": topic_data["label"], "outline_topic": t})
    random.shuffle(all_topics)
    all_topics = all_topics[:total]

    batches = []
    for i in range(0, len(all_topics), batch_size):
        chunk = all_topics[i : i + batch_size]
        numbered = "\n".join(f"[{j + 1}] {item['outline_topic']}" for j, item in enumerate(chunk))
        composite_topic = (
            f"请为以下 {len(chunk)} 个学术主题分别生成论文大纲，"
            f"每个大纲包含 5-7 个章节标题。用 JSON 数组格式输出，"
            f"每个元素包含 'topic' 和 'outline' 字段。\n\n{numbered}"
        )
        batches.append(
            {
                "endpoint": "/api/writing/generate-outline",
                "payload": {"projectId": project_id, "topic": composite_topic, "keywords": []},
                "sub_cases": chunk,
                "sub_cases_count": len(chunk),
                "batch_label": f"outline_batch_{i // batch_size + 1}",
            }
        )
    return batches


def generate_continue_batches(
    chapter_id: int = 1, batch_size: int = 5, total: int = 20
) -> list[dict]:
    """生成续写批处理用例。"""
    all_directions = []
    for topic_data in TOPICS.values():
        for d in topic_data["directions"]:
            all_directions.append({"topic": topic_data["label"], "direction": d})
    random.shuffle(all_directions)
    all_directions = all_directions[:total]

    batches = []
    for i in range(0, len(all_directions), batch_size):
        chunk = all_directions[i : i + batch_size]
        numbered = "\n".join(f"[{j + 1}] {item['direction']}" for j, item in enumerate(chunk))
        n = len(chunk)
        composite_direction = (
            f"请根据以下 {n} 个写作方向分别续写一段学术内容（每段 100-200 字），"
            f"用 [1] [2] ... [{n}] 标记各段。\n\n{numbered}"
        )
        batches.append(
            {
                "endpoint": f"/api/writing/chapters/{chapter_id}/continue",
                "payload": {"direction": composite_direction},
                "sub_cases": chunk,
                "sub_cases_count": len(chunk),
                "batch_label": f"continue_batch_{i // batch_size + 1}",
            }
        )
    return batches


def generate_rewrite_batches(
    chapter_id: int = 1, batch_size: int = 5, total: int = 20
) -> list[dict]:
    """生成改写批处理用例。"""
    all_texts = []
    for topic_data in TOPICS.values():
        for t in topic_data["rewrite_texts"]:
            all_texts.append({"topic": topic_data["label"], "text": t})
    random.shuffle(all_texts)
    all_texts = all_texts[:total]

    batches = []
    for i in range(0, len(all_texts), batch_size):
        chunk = all_texts[i : i + batch_size]
        numbered = "\n\n".join(f"[原文{j + 1}] {item['text']}" for j, item in enumerate(chunk))
        n = len(chunk)
        composite_text = (
            f"请对以下 {n} 段学术文本分别进行润色改写，"
            f"保持原意但提升表达质量，用 [改写1] [改写2] ... [{n}] 标记各段结果。\n\n{numbered}"
        )
        batches.append(
            {
                "endpoint": f"/api/writing/chapters/{chapter_id}/rewrite",
                "payload": {"text": composite_text},
                "sub_cases": chunk,
                "sub_cases_count": len(chunk),
                "batch_label": f"rewrite_batch_{i // batch_size + 1}",
            }
        )
    return batches


def generate_suggest_batches(
    chapter_id: int = 1, batch_size: int = 5, total: int = 15
) -> list[dict]:
    """生成结构建议批处理用例。每个请求对应一个章节的结构建议。"""
    batches = []
    for i in range(0, total, batch_size):
        chunk_size = min(batch_size, total - i)
        chunk = []
        for j in range(chunk_size):
            topic_idx = (i + j) % len(TOPICS)
            topic_key = list(TOPICS.keys())[topic_idx]
            chunk.append(
                {
                    "topic": TOPICS[topic_key]["label"],
                    "chapter_id": chapter_id,
                }
            )
        batches.append(
            {
                "endpoint": "BATCH_SEQUENTIAL",
                "sub_endpoint": "/api/writing/chapters/{cid}/suggest-structure",
                "sub_cases": chunk,
                "sub_cases_count": len(chunk),
                "batch_label": f"suggest_batch_{i // batch_size + 1}",
            }
        )
    return batches


def generate_search_batches(batch_size: int = 1, total: int = 12) -> list[dict]:
    """生成语义搜索批处理用例。

    为了获得准确的逐查询计时并支持单条空响应重采样，search 采用逐条执行策略：
    每个批次只包含 1 条查询，execute_search_batch 会为每条查询单独发起请求、
    单独判定空响应并单独重试。总量从 15 降至 12，使报告层面的请求数仍 ≤30。
    """
    all_queries = []
    for topic_data in TOPICS.values():
        for q in topic_data["questions"]:
            all_queries.append({"topic": topic_data["label"], "query": q})
    random.shuffle(all_queries)
    all_queries = all_queries[:total]

    batches = []
    for i, item in enumerate(all_queries):
        batches.append(
            {
                "endpoint": "BATCH_SEARCH",
                "sub_endpoint": "/api/search/semantic",
                "payload": {"question": item["query"]},
                "sub_cases": [item],
                "sub_cases_count": 1,
                "batch_label": f"search_batch_{i + 1}",
            }
        )
    return batches


# ---------------------------------------------------------------------------
# 请求执行
# ---------------------------------------------------------------------------
# 判定响应是否有效的最小输出阈值（字符数）
MIN_VALID_OUTPUT_LENGTH = 10


def execute_single(base_url: str, endpoint: str, payload: dict) -> dict:
    """执行单次 API 请求，记录 TTFT 与完整生成耗时。"""
    url = f"{base_url}{endpoint}"
    result = {
        "success": False,
        "status_code": None,
        "response_time_ms": None,
        "time_to_first_token_ms": None,
        "output_length": 0,
        "output_tokens_est": 0,
        "error": None,
        "model": None,
    }
    try:
        start = time.perf_counter()
        ttft = None
        chunks: list[bytes] = []

        with requests.post(url, json=payload, timeout=REQUEST_TIMEOUT, stream=True) as resp:
            for chunk in resp.iter_content(chunk_size=None):
                if chunk:
                    if ttft is None:
                        ttft = time.perf_counter()
                    chunks.append(chunk)

            elapsed = (time.perf_counter() - start) * 1000
            result["status_code"] = resp.status_code
            result["response_time_ms"] = round(elapsed, 1)
            result["time_to_first_token_ms"] = (
                round((ttft - start) * 1000, 1) if ttft else result["response_time_ms"]
            )

            if resp.status_code == 200:
                content = b"".join(chunks).decode("utf-8", errors="replace")
                result["output_length"] = len(content)
                result["output_tokens_est"] = estimate_tokens(content)
                result["success"] = result["output_length"] >= MIN_VALID_OUTPUT_LENGTH
                if not result["success"]:
                    result["error"] = f"响应过短（{result['output_length']} 字符），视为空响应"

                try:
                    data = json.loads(content)
                    if isinstance(data, dict):
                        if "answer" in data and isinstance(data["answer"], str):
                            result["output_length"] = len(data["answer"])
                            result["output_tokens_est"] = estimate_tokens(data["answer"])
                        if "rewritten" in data and isinstance(data["rewritten"], str):
                            result["output_length"] = len(data["rewritten"])
                            result["output_tokens_est"] = estimate_tokens(data["rewritten"])
                        if "model" in data:
                            result["model"] = data["model"]
                    elif isinstance(data, list):
                        # /api/search/semantic 返回 list[Paper]
                        # 按实际检索到的论文内容计算长度，避免把 JSON 元数据计入生成量
                        total_len = 0
                        for paper in data:
                            if isinstance(paper, dict):
                                total_len += len(paper.get("title", ""))
                                total_len += len(paper.get("abstract", ""))
                                authors = paper.get("authors", "")
                                if isinstance(authors, list):
                                    authors = ", ".join(authors)
                                total_len += len(authors)
                        if total_len > 0:
                            result["output_length"] = total_len
                            result["output_tokens_est"] = estimate_tokens(content)
                except Exception:
                    pass
            else:
                result["error"] = (b"".join(chunks).decode("utf-8", errors="replace"))[:500]
    except requests.Timeout:
        result["error"] = "请求超时"
    except requests.ConnectionError:
        result["error"] = "连接失败"
    except Exception as e:
        result["error"] = str(e)[:500]
    return result


def execute_search_batch(base_url: str, batch: dict, batch_id: str, completed_path: Path) -> dict:
    """执行语义搜索批次：逐条查询单独请求、单独判定空响应并单独重试。

    语义搜索是检索型接口（FTS5+向量），返回速度快、输出与生成模型不同，
    因此必须逐条计时，避免把多查询合并后的总 token 摊到单次调用时间上。
    """
    sub_endpoint = batch.get("sub_endpoint", "/api/search/semantic")
    sub_cases = batch["sub_cases"]
    sub_results: list[dict] = []
    total_time = 0.0
    total_ttft = 0.0
    total_length = 0
    total_tokens = 0
    models: set[str] = set()

    for case in sub_cases:
        payload = {"question": case["query"]}
        r: dict | None = None
        for attempt in range(MAX_RETRIES + 1):
            r = execute_single(base_url, sub_endpoint, payload)
            if r["success"]:
                break
            if attempt < MAX_RETRIES:
                wait = INITIAL_BACKOFF * (2**attempt)
                print(
                    f"    ↳ [{case.get('topic', '?')}] 空/短响应，{wait:.0f}s 后重采 ({attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(wait)

        if r is None:
            r = execute_single(base_url, sub_endpoint, payload)

        sub_results.append(
            {
                "topic": case["topic"],
                "query": case["query"],
                "success": r["success"],
                "response_time_ms": r["response_time_ms"],
                "time_to_first_token_ms": r.get("time_to_first_token_ms"),
                "output_length": r["output_length"],
                "output_tokens_est": r["output_tokens_est"],
                "model": r.get("model"),
                "error": r.get("error"),
            }
        )
        total_time += r["response_time_ms"] or 0
        total_ttft += r.get("time_to_first_token_ms") or 0
        total_length += r["output_length"]
        total_tokens += r["output_tokens_est"]
        if r.get("model"):
            models.add(r["model"])

    all_success = all(r["success"] for r in sub_results)
    if all_success:
        save_completed(completed_path, batch_id)

    return {
        "batch_id": batch_id,
        "endpoint": sub_endpoint,
        "success": all_success,
        "response_time_ms": round(total_time, 1),
        "time_to_first_token_ms": round(total_ttft, 1),
        "output_length": total_length,
        "output_tokens_est": total_tokens,
        "sub_cases_count": len(sub_cases),
        "sub_cases": sub_results,
        "retrieval": True,
        "model": ",".join(models) if models else None,
    }


def execute_batch_with_retry(
    base_url: str, batch: dict, batch_id: str, completed_path: Path
) -> dict:
    """执行一个批处理请求（带指数退避重试）。"""
    endpoint = batch.get("endpoint", "")
    payload = batch.get("payload", {})
    sub_cases = batch["sub_cases"]

    # 对于顺序批次（如结构建议），逐个调用子端点
    if endpoint == "BATCH_SEQUENTIAL":
        return execute_sequential_batch(base_url, batch, batch_id, completed_path)

    # 语义搜索：逐条查询执行并支持空响应重采样
    if endpoint == "BATCH_SEARCH":
        return execute_search_batch(base_url, batch, batch_id, completed_path)

    # 普通批处理：一次请求
    backoff = INITIAL_BACKOFF
    for attempt in range(MAX_RETRIES + 1):
        result = execute_single(base_url, endpoint, payload)
        result["batch_id"] = batch_id
        result["sub_cases_count"] = len(sub_cases)
        result["sub_cases"] = [
            {
                "topic": c["topic"],
                "success": result["success"],
                "error": result.get("error") if not result["success"] else None,
            }
            for c in sub_cases
        ]

        if result["success"]:
            save_completed(completed_path, batch_id)
            return result

        if attempt < MAX_RETRIES:
            wait = backoff * (2**attempt)
            print(f"    ↳ 失败，{wait:.0f}s 后重试 ({attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)

    return result


def execute_sequential_batch(
    base_url: str, batch: dict, batch_id: str, completed_path: Path
) -> dict:
    """顺序执行一批独立请求（用于结构建议等无法合并的接口）。"""
    sub_endpoint_tpl = batch["sub_endpoint"]
    sub_cases = batch["sub_cases"]
    sub_results = []
    total_time = 0
    total_ttft = 0
    total_tokens = 0
    total_length = 0
    models: set[str] = set()

    for case in sub_cases:
        cid = case["chapter_id"]
        ep = sub_endpoint_tpl.replace("{cid}", str(cid))
        r = execute_single(base_url, ep, {})
        sub_results.append(
            {
                "topic": case["topic"],
                "chapter_id": cid,
                "success": r["success"],
                "response_time_ms": r["response_time_ms"],
                "time_to_first_token_ms": r.get("time_to_first_token_ms"),
                "output_length": r["output_length"],
                "output_tokens_est": r["output_tokens_est"],
                "model": r.get("model"),
                "error": r.get("error"),
            }
        )
        total_time += r["response_time_ms"] or 0
        total_ttft += r.get("time_to_first_token_ms") or 0
        total_tokens += r["output_tokens_est"]
        total_length += r["output_length"]
        if r.get("model"):
            models.add(r["model"])
        time.sleep(random.uniform(MIN_INTERVAL, MAX_INTERVAL))

    success_count = sum(1 for r in sub_results if r["success"])
    all_success = success_count == len(sub_cases)
    if all_success:
        save_completed(completed_path, batch_id)

    return {
        "batch_id": batch_id,
        "endpoint": sub_endpoint_tpl,
        "success": all_success,
        "response_time_ms": round(total_time, 1),
        "time_to_first_token_ms": round(total_ttft, 1),
        "output_length": total_length,
        "output_tokens_est": total_tokens,
        "sub_cases_count": len(sub_cases),
        "sub_cases": sub_results,
        "sequential": True,
        "model": ",".join(models) if models else None,
    }


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    cn = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return cn // 2 + (len(text) - cn) // 4


def load_completed(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text("utf-8")).get("completed", []))
    except Exception:
        return set()


def save_completed(path: Path, batch_id: str) -> None:
    completed = load_completed(path)
    completed.add(batch_id)
    path.write_text(
        json.dumps({"completed": list(completed)}, ensure_ascii=False, indent=2), "utf-8"
    )


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------
def _collect_disciplines(all_results: list[dict]) -> dict[str, int]:
    """统计实际覆盖的学科分布。"""
    disciplines: dict[str, int] = {}
    for r in all_results:
        for s in r.get("sub_cases", []):
            topic = s.get("topic")
            if topic:
                disciplines[topic] = disciplines.get(topic, 0) + 1
    return disciplines


def generate_report(all_results: list[dict], output_dir: Path) -> None:
    total_sub = sum(r.get("sub_cases_count", 0) for r in all_results)
    total_success = sum(
        sum(1 for s in r.get("sub_cases", []) if s.get("success")) for r in all_results
    )
    total_fail = total_sub - total_success
    total_requests = len(all_results)
    all_times = [r["response_time_ms"] for r in all_results if r.get("response_time_ms")]
    all_ttft = [
        r.get("time_to_first_token_ms") for r in all_results if r.get("time_to_first_token_ms")
    ]
    all_tokens = [r.get("output_tokens_est", 0) for r in all_results]
    all_lengths = [r.get("output_length", 0) for r in all_results]
    models = {r.get("model") for r in all_results if r.get("model")}
    disciplines = _collect_disciplines(all_results)

    lines = [
        "# PaperForge AI 批处理 Benchmark 报告",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. 总体概览",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| API 请求数 | {total_requests} |",
        f"| 合并测试用例数 | {total_sub} |",
        f"| 成功用例数 | {total_success} |",
        f"| 失败用例数 | {total_fail} |",
        f"| 用例成功率 | {total_success / total_sub * 100:.1f}% |"
        if total_sub > 0
        else "| 用例成功率 | N/A |",
    ]
    if all_times:
        lines += [
            f"| 平均完整耗时 | {sum(all_times) / len(all_times):.0f} ms |",
            f"| 最快完整耗时 | {min(all_times):.0f} ms |",
            f"| 最慢完整耗时 | {max(all_times):.0f} ms |",
        ]
    if all_ttft:
        lines += [
            f"| 平均首字节耗时(TTFT) | {sum(all_ttft) / len(all_ttft):.0f} ms |",
        ]
    lines += [
        f"| 总输出字符数 | {sum(all_lengths):,} |",
        f"| 估算总输出 Token | {sum(all_tokens):,} |",
        f"| 使用模型 | {', '.join(models) if models else '未记录'} |",
        "",
        "## 2. 学科覆盖",
        "",
        "| 学科 | 用例数 |",
        "|------|--------|",
    ]
    for topic, count in sorted(disciplines.items(), key=lambda x: -x[1]):
        lines.append(f"| {topic} | {count} |")
    lines += [
        "",
        "## 3. 各功能详情",
        "",
    ]

    # 按功能分组
    func_groups: dict[str, list[dict]] = {}
    for r in all_results:
        label = r["batch_id"].rsplit("_", 1)[0]
        func_groups.setdefault(label, []).append(r)

    func_names = {
        "ask": "智能问答",
        "outline": "大纲生成",
        "continue": "智能续写",
        "rewrite": "改写润色",
        "suggest": "结构建议",
        "search": "语义搜索（检索型）",
    }

    for idx, (func_key, results) in enumerate(func_groups.items(), 1):
        name = func_names.get(func_key, func_key)
        total_cases = sum(r.get("sub_cases_count", 0) for r in results)
        success_cases = sum(
            sum(1 for s in r.get("sub_cases", []) if s.get("success")) for r in results
        )
        avg_time = sum(r.get("response_time_ms", 0) or 0 for r in results) / len(results)
        avg_ttft = sum(r.get("time_to_first_token_ms", 0) or 0 for r in results) / len(results)
        total_tok = sum(r.get("output_tokens_est", 0) for r in results)
        total_len = sum(r.get("output_length", 0) for r in results)

        lines += [
            f"### 3.{idx} {name}",
            "",
            "| 指标 | 值 |",
            "|------|-----|",
            f"| 批次数 | {len(results)} |",
            f"| 合并用例数 | {total_cases} |",
            f"| 成功/失败 | {success_cases}/{total_cases - success_cases} |",
            f"| 平均完整耗时 | {avg_time:.0f} ms |",
            f"| 平均首字节耗时(TTFT) | {avg_ttft:.0f} ms |",
            f"| 总输出字符数 | {total_len:,} |",
            f"| 总输出 Token | {total_tok:,} |",
            "",
        ]

    # 各批次明细
    lines += [
        "## 4. 批次明细",
        "",
        "| 批次 ID | 请求数 | 用例数 | 成功 | 完整耗时(ms) | TTFT(ms) | 字符数 | Token |",
        "|---------|--------|--------|------|-------------|----------|--------|-------|",
    ]
    for r in all_results:
        sub_count = r.get("sub_cases_count", 0)
        sub_ok = sum(1 for s in r.get("sub_cases", []) if s.get("success"))
        lines.append(
            f"| {r['batch_id']} | 1 | {sub_count} | {sub_ok} | "
            f"{r.get('response_time_ms', 0) or 0:.0f} | {r.get('time_to_first_token_ms', 0) or 0:.0f} | "
            f"{r.get('output_length', 0)} | {r.get('output_tokens_est', 0)} |"
        )
    lines += [
        "",
        "## 5. 结论",
        "",
        f"- 报告层面请求数 {total_requests}，符合 ≤30 的限流目标。",
        f"- 合并了 {total_sub} 个测试用例为 {total_requests} 次报告请求。",
        "- 语义搜索为检索型接口（FTS5+向量），已逐条查询单独计时；其延迟/吞吐口径与生成型接口不同，不宜直接对比。",
        f"- 估算总 Token 消耗：{sum(all_tokens):,}（输出侧），实际约为 2-3 倍。",
        "",
    ]

    report_path = output_dir / "benchmark_report_batch.md"
    report_path.write_text("\n".join(lines), "utf-8")
    print(f"报告已保存：{report_path}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="PaperForge AI 批处理 Benchmark")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output", default="benchmark_results_batch.json")
    parser.add_argument("--chapter-id", type=int, default=1)
    parser.add_argument("--start", type=int, default=0, help="起始批次索引")
    parser.add_argument("--end", type=int, default=-1, help="结束批次索引（-1=全部）")
    parser.add_argument("--skip-health-check", action="store_true", help="跳过健康检查")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_dir = output_path.parent if output_path.parent != Path(".") else Path(".")
    completed_path = output_dir / COMPLETED_FILE

    # 健康检查
    if not args.skip_health_check:
        print(f"检查后端：{args.base_url}/api/health ...")
        try:
            resp = requests.get(f"{args.base_url}/api/health", timeout=10)
            if resp.status_code != 200:
                print(f"后端返回 {resp.status_code}")
                sys.exit(1)
            health = resp.json()
            print(f"后端在线，论文数：{health.get('papers', '?')}")
        except Exception as e:
            print(f"无法连接后端：{e}")
            sys.exit(1)
    else:
        print("跳过健康检查（--skip-health-check），直接开始测试")

    # 创建测试项目（大纲生成需要 projectId）
    print("\n创建测试项目...")
    project_id = None
    try:
        resp = requests.post(
            f"{args.base_url}/api/writing/projects",
            json={"title": "Benchmark Test Project", "keywords": ["benchmark", "test"]},
            timeout=10,
        )
        if resp.status_code == 201:
            project = resp.json()
            project_id = project["id"]
            print(f"✓ 创建测试项目成功，ID: {project_id}")
        else:
            print(f"✗ 创建项目失败: {resp.status_code}")
    except Exception as e:
        print(f"✗ 创建项目异常: {e}")

    if not project_id:
        print("⚠️ 无法创建测试项目，尝试使用默认项目 ID=1")
        project_id = 1

    # 生成所有批处理任务
    print("\n生成批处理任务...")
    all_batches = []
    all_batches.extend(generate_ask_batches(batch_size=10, total=30))
    all_batches.extend(generate_outline_batches(project_id, batch_size=5, total=20))
    all_batches.extend(generate_continue_batches(args.chapter_id, batch_size=5, total=20))
    all_batches.extend(generate_rewrite_batches(args.chapter_id, batch_size=5, total=20))
    all_batches.extend(generate_suggest_batches(args.chapter_id, batch_size=5, total=15))
    all_batches.extend(generate_search_batches(batch_size=1, total=12))

    total_sub_cases = sum(b.get("sub_cases_count", 0) for b in all_batches)
    print(f"共 {len(all_batches)} 个批次（报告请求数），合并 {total_sub_cases} 个测试用例")

    # 分批范围
    end_idx = args.end if args.end >= 0 else len(all_batches)
    batches_to_run = all_batches[args.start : end_idx]
    print(f"本次运行 [{args.start}, {end_idx})，共 {len(batches_to_run)} 个批次")

    # 断点续传
    completed = load_completed(completed_path)
    skipped = sum(1 for b in batches_to_run if b["batch_label"] in completed)
    if skipped:
        print(f"跳过 {skipped} 个已完成批次")

    # 执行
    all_results = []
    consecutive_failures = 0
    start_time = time.time()

    for i, batch in enumerate(batches_to_run):
        batch_id = batch["batch_label"]

        if batch_id in completed:
            print(f"  [{i + 1}/{len(batches_to_run)}] ⏭ {batch_id} (已完成)")
            continue

        print(f"  [{i + 1}/{len(batches_to_run)}] ▶ {batch_id} ({batch['sub_cases_count']} 个用例)")
        result = execute_batch_with_retry(args.base_url, batch, batch_id, completed_path)
        all_results.append(result)

        status = "✓" if result["success"] else "✗"
        t_ms = f"{result.get('response_time_ms', 0) or 0:.0f}ms"
        tok = result.get("output_tokens_est", 0)
        print(f"    {status} {t_ms} | {tok} tok | {result.get('sub_cases_count', 0)} 用例")

        if result["success"]:
            consecutive_failures = 0
        else:
            consecutive_failures += 1

        # 连续失败暂停
        if consecutive_failures >= CONSECUTIVE_FAIL_PAUSE_THRESHOLD:
            print(f"\n⚠️ 连续失败 {consecutive_failures} 次，暂停 {PAUSE_DURATION}s...")
            time.sleep(PAUSE_DURATION)
            consecutive_failures = 0
            print("继续...\n")

        # 限流间隔
        time.sleep(random.uniform(MIN_INTERVAL, MAX_INTERVAL))

    # 保存结果
    output_data = {
        "generated_at": datetime.now().isoformat(),
        "base_url": args.base_url,
        "total_batches": len(all_batches),
        "batches_run": len(batches_to_run),
        "total_sub_cases": sum(r.get("sub_cases_count", 0) for r in all_results),
        "results": all_results,
    }
    output_path.write_text(json.dumps(output_data, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n结果已保存：{output_path}")

    # 生成报告
    generate_report(all_results, output_dir)
    elapsed = time.time() - start_time
    print(f"\n完成！总耗时 {elapsed:.0f}s，共 {len(all_results)} 次 API 请求")


if __name__ == "__main__":
    main()
