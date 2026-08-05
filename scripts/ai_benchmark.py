"""AI 功能批量测试脚本 —— 为 PaperForge 6 个 AI 功能各生成 50 个测试用例，批量调用并采集性能数据。

使用方式：
    # 运行全部用例
    python scripts/ai_benchmark.py [--base-url http://127.0.0.1:8770] [--output benchmark_results.json]

    # 分批运行（支持断点续传）
    python scripts/ai_benchmark.py --start 0 --end 50
    python scripts/ai_benchmark.py --start 50 --end 100

前置条件：
    - 后端服务已启动（uvicorn mock_api.main:app --port 8770）
    - 已配置至少一个 LLM 模型（通过 /api/models 接口）
    - 数据库中至少有少量论文（用于 RAG 检索）

限流策略：
    - 请求间隔：1-3 秒随机
    - 失败重试：指数退避（初始 2 秒，最多 5 次重试）
    - 连续失败 10 次自动暂停 5 分钟

断点续传：
    - 已完成的用例保存在 completed.json
    - 中断后重新运行会跳过已完成部分

输出：
    - benchmark_results.json：每个请求的响应时间、token 消耗、输出长度、是否成功
    - benchmark_report.md：汇总统计和每个功能的性能分析
    - completed.json：断点续传状态文件
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
REQUEST_TIMEOUT = 120  # 秒（大纲生成等可能耗时较长）

# 限流配置
MIN_REQUEST_INTERVAL = 1  # 最小请求间隔（秒）
MAX_REQUEST_INTERVAL = 3  # 最大请求间隔（秒）
MAX_RETRIES = 5  # 最大重试次数
INITIAL_BACKOFF = 2  # 初始退避时间（秒）
CONSECUTIVE_FAIL_PAUSE = 10  # 连续失败次数触发暂停
PAUSE_DURATION = 300  # 暂停时长（秒，5分钟）

# 断点续传文件
COMPLETED_FILE = "completed.json"

# ---------------------------------------------------------------------------
# 测试主题覆盖（每个主题至少 10 个用例，共 50×6=300）
# ---------------------------------------------------------------------------
TOPICS = {
    "cs": {
        "label": "计算机科学",
        "questions": [
            "Transformer 架构中自注意力机制的时间复杂度是多少？如何优化？",
            "LoRA 微调相比全参数微调有哪些优势和局限性？",
            "GPT 系列模型从 GPT-1 到 GPT-4 的架构演变有哪些关键变化？",
            "联邦学习中的拜占庭容错问题如何解决？",
            "知识蒸馏和模型剪枝在实际部署中的效果差异是什么？",
            "强化学习在推荐系统中的应用有哪些最新进展？",
            "图神经网络在社交网络分析中的优势和挑战是什么？",
            "对比学习在无监督表征学习中的核心思想是什么？",
            "分布式训练中的梯度同步策略有哪些？各有什么优劣？",
            "代码生成大模型的评估指标有哪些？如何衡量生成代码的质量？",
            "RAG（检索增强生成）架构相比纯生成模型有什么优势？",
            "多模态大模型如何处理图像和文本的跨模态对齐？",
            "扩散模型在图像生成领域相比 GAN 的优势是什么？",
            "RLHF（基于人类反馈的强化学习）的训练流程是怎样的？",
            "神经架构搜索（NAS）在资源受限场景下如何高效搜索？",
        ],
        "writing_directions": [
            "接下来讨论实验设置与评估指标",
            "补充最新的基准测试结果对比",
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
            "我们收集了来自多个领域的 10 万条数据样本，经过预处理和标注后用于模型训练和评估。",
            "该模型采用了层次化的特征提取策略，能够同时捕获局部和全局的语义信息。",
            "与传统方法相比，本文提出的技术路线在推理速度上提升了 3 倍，同时保持了相当的精度。",
            "通过引入对比学习目标，模型在无标签数据上的表征质量得到了显著改善。",
            "消融实验验证了每个模块对最终性能的贡献，其中注意力模块的贡献最为显著。",
            "我们在 GPU 集群上使用分布式数据并行策略完成了全部训练，总耗时约 48 小时。",
            "模型的泛化能力通过跨域迁移实验得到了验证，在目标域上的性能下降不超过 5%。",
            "本文的创新点在于将因果推断的思想引入了推荐系统的去偏过程中。",
            "知识图谱的引入为模型提供了丰富的外部语义信息，有效缓解了数据稀疏问题。",
            "我们设计了一种自适应的学习率调度策略，能够根据训练动态自动调整学习步长。",
            "实验覆盖了从 1B 到 70B 不同规模的模型，系统性地分析了规模效应。",
            "本文提出的框架支持增量学习，可以在不重新训练的情况下持续吸收新知识。",
            "安全性评估表明，经过对齐训练的模型在有害内容生成方面的风险显著降低。",
        ],
        "outline_topics": [
            "基于 Transformer 的大规模语言模型预训练技术综述",
            "低秩适应方法在大语言模型高效微调中的应用",
            "图神经网络在药物发现中的应用与挑战",
            "联邦学习中的隐私保护与通信优化",
            "多模态大模型的架构设计与训练策略",
            "扩散模型在文本到图像生成中的最新进展",
            "强化学习从人类反馈（RLHF）的理论与实践",
            "检索增强生成（RAG）系统的优化策略研究",
            "神经架构搜索的高效算法与应用",
            "大语言模型的幻觉问题与缓解方法",
            "自监督学习在计算机视觉中的应用综述",
            "代码大模型的能力评估与安全风险分析",
            "知识蒸馏技术的最新进展与应用",
            "分布式大模型训练的系统优化研究",
            "AI Agent 的架构设计与能力评估框架",
        ],
    },
    "biomedical": {
        "label": "生物医学",
        "questions": [
            "AlphaFold 2 的 Evoformer 模块是如何处理氨基酸序列的共进化信息的？",
            "单细胞 RNA 测序数据降维分析中，UMAP 相比 t-SNE 有哪些优势？",
            "CRISPR-Cas9 基因编辑的脱靶效应如何通过计算方法预测和减少？",
            "药物-靶点相互作用预测中，图神经网络方法相比传统方法有何优势？",
            "蛋白质语言模型（如 ESM-2）在蛋白质功能预测中的应用前景如何？",
            "医学影像中的域适应问题如何解决？不同设备间的差异如何消除？",
            "电子健康记录（EHR）的时序建模有哪些主流方法？",
            "基因组变异致病性预测的深度学习方法有哪些？",
            "蛋白质结构预测中，如何处理多构象蛋白质的柔性问题？",
            "病理图像分析中，弱监督学习方法的效果如何？",
            "脑机接口中神经信号解码的最新深度学习方法是什么？",
            "药物重定位（Drug Repurposing）的网络药理学方法有哪些？",
            "免疫组库（Immune Repertoire）分析中的序列聚类算法有哪些？",
            "合成生物学中基因线路设计的自动化方法有哪些进展？",
            "临床试验设计中如何利用贝叶斯自适应方法提高效率？",
        ],
        "writing_directions": [
            "讨论模型在临床数据上的验证结果",
            "分析基因组数据的预处理流程",
            "补充生物信息学工具的对比分析",
            "讨论伦理审查和数据隐私保护措施",
            "分析样本量对统计功效的影响",
            "展望精准医疗中的应用前景",
            "补充多组学数据整合的方法",
            "讨论模型的可解释性在临床中的重要性",
            "分析跨种族/跨人群的泛化能力",
            "探讨数据标注的质量控制流程",
        ],
        "rewrite_texts": [
            "本研究基于 5000 例临床样本，开发了一种新型的疾病风险预测模型，在前瞻性验证中 AUC 达到 0.92。",
            "蛋白质结构预测的准确性直接影响药物设计的成功率，因此需要更高精度的预测方法。",
            "通过对单细胞转录组数据的聚类分析，我们识别出了 12 种 previously unknown 的细胞亚型。",
            "该方法利用注意力机制自动学习基因间的调控关系，避免了人工特征工程的局限性。",
            "临床试验数据显示，该药物在 III 期试验中的主要终点达成率为 78%，显著优于安慰剂组。",
            "我们构建了一个包含 200 万条药物-靶点相互作用的知识图谱，用于药物重定位研究。",
            "影像组学特征与基因组数据的整合分析揭示了肿瘤微环境的异质性。",
            "基于贝叶斯框架的因果推断方法能够从观察性数据中识别潜在的治疗效果。",
            "免疫组库多样性分析表明，COVID-19 康复患者的 T 细胞受体 repertoire 发生了显著变化。",
            "深度学习模型在病理图像分割任务上的 Dice 系数达到了 0.95，超过了资深病理医师的水平。",
            "多组学数据整合分析揭示了疾病发展的分子机制，为精准治疗提供了新的靶点。",
            "纵向电子健康记录的建模需要处理不规则采样和大量缺失值的挑战。",
            "脑电信号的时频分析结合深度学习可以有效识别不同认知状态。",
            "基因编辑的 off-target 预测模型需要在全基因组范围内进行评估。",
            "药物相互作用的预测对于多重用药患者的安全性至关重要。",
        ],
        "outline_topics": [
            "深度学习在医学影像诊断中的应用与挑战",
            "蛋白质语言模型的功能预测能力评估",
            "单细胞多组学数据整合分析方法综述",
            "AI 辅助药物发现的计算方法与实践",
            "电子健康记录的深度学习建模方法",
            "基因组变异致病性预测的机器学习方法",
            "脑机接口中的神经信号解码技术",
            "病理图像分析的弱监督学习方法",
            "临床试验设计中的自适应方法研究",
            "精准医疗中的多模态数据融合",
            "免疫组库分析的计算方法与应用",
            "合成生物学中的自动化设计方法",
            "医学自然语言处理的最新进展",
            "可解释 AI 在临床决策支持中的应用",
            "联邦学习在多中心医疗数据中的应用",
        ],
    },
    "math": {
        "label": "数学",
        "questions": [
            "最优传输理论（Optimal Transport）在机器学习中有哪些应用？",
            "Transformer 中注意力矩阵的低秩近似有哪些数学方法？",
            "梯度下降法在非凸优化中的收敛性如何保证？",
            "稀疏正则化的数学原理是什么？L1 和 L2 正则化的几何解释有何不同？",
            "贝叶斯优化中的采集函数（Acquisition Function）有哪些设计准则？",
            "随机微分方程在金融建模中的应用有哪些？",
            "图拉普拉斯算子的谱性质如何影响图神经网络的表达能力？",
            "扩散模型中的 score matching 的数学推导是怎样的？",
            "张量分解在推荐系统中的应用有哪些数学基础？",
            "信息论中的互信息在特征选择中如何应用？",
            "核方法在高维空间中的计算效率如何优化？",
            "变分推断中 ELBO 的推导及其松弛策略有哪些？",
            "马尔可夫链蒙特卡洛（MCMC）方法的收敛诊断有哪些？",
            "凸优化中的对偶理论在支持向量机中如何应用？",
            "流形学习的数学基础是什么？局部线性嵌入的原理是什么？",
        ],
        "writing_directions": [
            "补充定理的严格数学证明",
            "讨论算法的计算复杂度分析",
            "分析收敛速率的理论上界",
            "补充数值实验验证理论结果",
            "讨论参数选择对结果稳定性的影响",
            "与现有方法的理论对比分析",
            "分析高维情况下的 curse of dimensionality",
            "讨论近似算法的误差界",
            "补充几何直觉的图示说明",
            "探讨方法在大规模数据上的可扩展性",
        ],
        "rewrite_texts": [
            "本文证明了在 Lipschitz 连续条件下，梯度下降法的收敛速率为 O(1/T)，其中 T 为迭代次数。",
            "通过引入 Moreau 包络，我们将非光滑优化问题转化为光滑问题，从而可以应用加速梯度方法。",
            "定理 3.2 表明，在强凸假设下，随机梯度方差缩减方法可以达到线性收敛速率。",
            "我们利用矩阵 Bernstein 不等式证明了随机矩阵和的谱范数集中性质。",
            "最优传输距离（Wasserstein 距离）在概率分布之间的比较中具有良好的几何性质。",
            "变分自编码器的 ELBO 可以分解为重构误差和 KL 散度两项，分别对应模型的拟合能力和正则化。",
            "核方法通过隐式映射将数据嵌入到高维再生核希尔伯特空间中，从而实现非线性分类。",
            "扩散模型的去噪过程可以看作是对数概率密度梯度的 Langevin 动力学采样。",
            "我们利用 concentration of measure 理论分析了高维空间中随机投影的保距性质。",
            "对偶理论将原始优化问题转化为对偶问题，在某些条件下可以显著降低计算复杂度。",
            "张量网络分解为高维数据提供了紧凑的低秩表示，广泛应用于量子计算和机器学习。",
            "最优传输中的 Kantorovich 对偶为计算 Wasserstein 距离提供了高效的数值方法。",
            "随机优化中的方差缩减技术（如 SVRG）有效降低了梯度估计的方差。",
            "信息瓶颈理论为深度学习的泛化能力提供了一种信息论的解释框架。",
            "几何深度学习利用对称性和不变性来设计等变神经网络架构。",
        ],
        "outline_topics": [
            "最优传输理论在机器学习中的应用综述",
            "深度学习优化方法的数学基础",
            "扩散模型的数学理论与分析",
            "稀疏表示与压缩感知的理论进展",
            "图神经网络的表达能力与图论",
            "贝叶斯深度学习的不确定性量化",
            "随机优化方法的收敛性分析",
            "核方法与再生核希尔伯特空间",
            "变分推断的理论与算法进展",
            "张量分解方法及其应用",
            "非凸优化中的 landscape 分析",
            "高维统计推断的方法与理论",
            "强化学习的数学理论基础",
            "微分方程与神经网络的融合",
            "信息论在机器学习中的应用",
        ],
    },
    "physics": {
        "label": "物理学",
        "questions": [
            "量子纠错码（如 Surface Code）的容错阈值是如何计算的？",
            "机器学习在粒子物理实验中的 jet tagging 任务中如何应用？",
            "神经网络量子态（Neural Quantum States）的表达能力如何？",
            "引力波信号检测中的匹配滤波方法的原理是什么？",
            "物理信息神经网络（PINNs）求解偏微分方程的优势和局限性是什么？",
            "等离子体湍流的数值模拟方法有哪些？",
            "量子机器学习中的 variational quantum eigensolver 的原理是什么？",
            "暗物质探测实验中的统计分析方法有哪些？",
            "拓扑绝缘体的表面态如何通过第一性原理计算预测？",
            "宇宙学参数估计中的 MCMC 采样方法如何实现？",
            "量子计算在材料科学中的模拟应用有哪些？",
            "中微子振荡参数的精确测量方法有哪些？",
            "激光等离子体加速中的粒子跟踪模拟方法有哪些？",
            "超导量子比特的退相干机制如何理解和控制？",
            "统计力学中的相变与临界现象如何用重整化群描述？",
        ],
        "writing_directions": [
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
        ],
        "rewrite_texts": [
            "我们利用深度学习方法对 LHC 对撞数据中的希格斯玻色子衰变信号进行了高效识别，信噪比提升了 40%。",
            "量子纠错码的实现需要满足容错阈值条件，当前超导量子比特的错误率已接近这一阈值。",
            "物理信息神经网络通过在损失函数中嵌入物理方程的残差，实现了对偏微分方程的无网格求解。",
            "引力波探测器 LIGO/Virgo 的灵敏度提升使得我们可以探测到更远距离的中子星合并事件。",
            "神经网络量子态为多体量子系统的基态求解提供了新的变分方法，避免了指数级的希尔伯特空间。",
            "利用机器学习进行宇宙学参数估计，相比传统 MCMC 方法可以将计算时间缩短两个数量级。",
            "第一性原理计算预测了新型二维材料的电子结构和拓扑性质，为实验验证提供了理论指导。",
            "统计力学中的 Ising 模型在临界点附近表现出普适的标度行为，与重整化群理论的预言一致。",
            "等离子体湍流的多尺度模拟需要同时处理宏观流体运动和微观粒子动力学。",
            "量子退相干是量子计算面临的主要挑战之一，需要通过量子纠错和退相干自由子空间来解决。",
            "高能物理实验中的触发系统需要在微秒级别做出决策，深度学习推理加速成为关键。",
            "暗物质直接探测实验利用核反冲信号来寻找暗物质粒子，需要极低的本底噪声环境。",
            "中微子质量顺序的确定是当前粒子物理学的重要目标之一。",
            "拓扑量子计算利用非阿贝尔任意子的编织操作来实现容错量子门。",
            "宇宙微波背景辐射的精密测量为宇宙学标准模型提供了精确的参数约束。",
        ],
        "outline_topics": [
            "机器学习在高能物理数据分析中的应用",
            "量子纠错码的理论与实验进展",
            "物理信息神经网络的方法与应用",
            "引力波天文学的数据分析方法",
            "量子计算在材料模拟中的应用",
            "神经网络量子态的理论与算法",
            "暗物质探测的实验方法与数据分析",
            "宇宙学参数的精密测量方法",
            "等离子体物理的数值模拟技术",
            "拓扑物态的第一性原理计算",
            "统计力学中的机器学习方法",
            "激光等离子体加速的模拟与优化",
            "中微子物理的实验与理论进展",
            "超导量子比特的退相干与纠错",
            "高能物理实验中的触发与重建算法",
        ],
    },
    "economics": {
        "label": "经济学",
        "questions": [
            "深度学习在金融时间序列预测中的效果是否真的优于传统计量经济学模型？",
            "因果推断中的工具变量法（IV）和断点回归设计（RDD）各自的适用条件是什么？",
            "自然语言处理在金融情感分析中如何处理反讽和双重否定？",
            "高频交易中的市场微观结构模型有哪些？",
            "机器学习在信用评分中的应用如何处理公平性和歧视问题？",
            "合成控制法（SCM）在政策评估中的优势和局限性是什么？",
            "区块链经济学中的机制设计问题如何分析？",
            "博弈论在拍卖机制设计中的应用有哪些最新进展？",
            "宏观经济预测中混合频率数据（MIDAS）模型的原理是什么？",
            "网络经济学中的平台定价策略如何建模？",
            "行为经济学中的前景理论在投资决策中如何应用？",
            "碳排放交易市场的价格形成机制有哪些经济学模型？",
            "劳动经济学中的匹配理论在就业市场分析中如何应用？",
            "数字经济学中的数据要素定价问题如何解决？",
            "国际经济学中的汇率预测方法有哪些？深度学习方法效果如何？",
        ],
        "writing_directions": [
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
        ],
        "rewrite_texts": [
            "本文利用双重差分法（DID）评估了数字普惠金融政策对农村经济发展的影响，发现政策实施后农村人均收入提升了 12%。",
            "机器学习方法在股票收益预测中的样本外 R² 仅为 2-3%，但这种微弱的预测能力在投资组合构建中可以产生显著的经济价值。",
            "基于 BERT 的金融情感分析模型在中文财经新闻数据集上的 F1 分数达到 0.89，显著优于传统词袋模型。",
            "高频交易做市策略的核心在于管理库存风险和逆向选择成本之间的权衡。",
            "利用 LASSO 回岭回归进行宏观经济变量选择，可以在高维预测因子中识别出最具预测力的少数变量。",
            "合成控制法通过数据驱动的方式构建反事实，避免了传统 DID 中平行趋势假设的主观性。",
            "行为金融学研究表明，投资者的过度自信和损失厌恶是导致市场异象的重要原因。",
            "平台经济学中的交叉网络效应使得定价策略需要同时考虑买方和卖方的参与约束。",
            "因果森林（Causal Forest）方法可以估计异质性处理效应，为个性化政策设计提供了数据驱动的工具。",
            "碳排放权交易的价格受到能源价格、气候政策和经济增长等多重因素的影响。",
            "区块链的共识机制设计需要平衡去中心化、安全性和可扩展性（不可能三角）。",
            "匹配理论在 kidney exchange 和学校选择等实际场景中已产生了显著的社会福利改进。",
            "数字经济时代的数据要素具有非竞争性和正外部性，传统产权理论需要扩展。",
            "汇率的随机游走假设在短期内难以被击败，但机器学习在长期预测中展现出优势。",
            "利用 natural experiment 识别策略可以更可信地估计经济政策的因果效应。",
        ],
        "outline_topics": [
            "机器学习在金融风险管理中的应用",
            "因果推断方法在经济学中的最新进展",
            "自然语言处理在金融分析中的应用",
            "数字经济中的平台竞争与规制",
            "深度学习在宏观经济预测中的应用",
            "行为经济学与机器学习的交叉研究",
            "碳排放交易市场的机制设计与价格分析",
            "高频交易的市场微观结构分析",
            "公平性机器学习在信贷评估中的应用",
            "合成控制法在政策评估中的应用",
            "区块链经济学的理论与实证",
            "数据要素的定价与确权机制",
            "劳动力市场匹配的算法与机制",
            "深度学习在经济预测中的可解释性",
            "国际经济学中的大数据方法应用",
        ],
    },
}


# ---------------------------------------------------------------------------
# 测试用例生成
# ---------------------------------------------------------------------------
def generate_ask_cases() -> list[dict]:
    """生成 50 个 /api/ask 测试用例。"""
    cases = []
    for topic_key, topic_data in TOPICS.items():
        for q in topic_data["questions"]:
            cases.append(
                {
                    "topic": topic_data["label"],
                    "endpoint": "/api/ask",
                    "method": "POST",
                    "payload": {"question": q},
                }
            )
    random.shuffle(cases)
    return cases[:50]


def generate_continue_cases(chapter_id: int = 1) -> list[dict]:
    """生成 50 个 /api/writing/chapters/{id}/continue 测试用例。"""
    cases = []
    for topic_key, topic_data in TOPICS.items():
        for d in topic_data["writing_directions"]:
            cases.append(
                {
                    "topic": topic_data["label"],
                    "endpoint": f"/api/writing/chapters/{chapter_id}/continue",
                    "method": "POST",
                    "payload": {"direction": d},
                }
            )
    random.shuffle(cases)
    return cases[:50]


def generate_rewrite_cases(chapter_id: int = 1) -> list[dict]:
    """生成 50 个 /api/writing/chapters/{id}/rewrite 测试用例。"""
    cases = []
    for topic_key, topic_data in TOPICS.items():
        for t in topic_data["rewrite_texts"]:
            cases.append(
                {
                    "topic": topic_data["label"],
                    "endpoint": f"/api/writing/chapters/{chapter_id}/rewrite",
                    "method": "POST",
                    "payload": {"text": t},
                }
            )
    random.shuffle(cases)
    return cases[:50]


def generate_outline_cases(project_id: int = 1) -> list[dict]:
    """生成 50 个 /api/writing/generate-outline 测试用例。"""
    cases = []
    for topic_key, topic_data in TOPICS.items():
        for topic in topic_data["outline_topics"]:
            kw_list = topic.split("的" if "的" in topic else "与" if "与" in topic else "在")[:3]
            kw_list = [kw.strip() for kw in kw_list if kw.strip()]
            cases.append(
                {
                    "topic": topic_data["label"],
                    "endpoint": "/api/writing/generate-outline",
                    "method": "POST",
                    "payload": {
                        "project_id": project_id,
                        "topic": topic,
                        "keywords": kw_list[:3],
                    },
                }
            )
    random.shuffle(cases)
    return cases[:50]


def generate_suggest_cases(chapter_id: int = 1) -> list[dict]:
    """生成 50 个 /api/writing/chapters/{id}/suggest-structure 测试用例。"""
    # suggest-structure 接口不接受自定义 payload，仅依赖章节上下文
    # 为了增加多样性，我们模拟不同章节 ID
    cases = []
    for i in range(50):
        topic_idx = i % 5
        topic_key = list(TOPICS.keys())[topic_idx]
        cases.append(
            {
                "topic": TOPICS[topic_key]["label"],
                "endpoint": f"/api/writing/chapters/{chapter_id}/suggest-structure",
                "method": "POST",
                "payload": {},
                "chapter_hint": f"chapter_{i + 1}",
            }
        )
    return cases


def generate_semantic_search_cases() -> list[dict]:
    """生成 50 个 /api/search/semantic 测试用例。"""
    queries = []
    for topic_key, topic_data in TOPICS.items():
        for q in topic_data["questions"]:
            queries.append({"topic": topic_data["label"], "query": q})

    # 也加入一些简短的关键词查询
    short_queries = [
        {"topic": "计算机科学", "query": "large language model"},
        {"topic": "计算机科学", "query": "attention mechanism"},
        {"topic": "计算机科学", "query": "LoRA fine-tuning"},
        {"topic": "计算机科学", "query": "retrieval augmented generation"},
        {"topic": "计算机科学", "query": "diffusion model"},
        {"topic": "生物医学", "query": "protein structure prediction"},
        {"topic": "生物医学", "query": "single cell sequencing"},
        {"topic": "生物医学", "query": "CRISPR gene editing"},
        {"topic": "生物医学", "query": "medical image segmentation"},
        {"topic": "生物医学", "query": "drug discovery"},
        {"topic": "数学", "query": "optimal transport"},
        {"topic": "数学", "query": "variational inference"},
        {"topic": "数学", "query": "gradient descent convergence"},
        {"topic": "物理", "query": "quantum error correction"},
        {"topic": "物理", "query": "gravitational wave detection"},
        {"topic": "经济", "query": "causal inference economics"},
        {"topic": "经济", "query": "financial sentiment analysis"},
        {"topic": "经济", "query": "mechanism design auction"},
    ]
    queries.extend(short_queries)
    random.shuffle(queries)

    cases = []
    for item in queries[:50]:
        cases.append(
            {
                "topic": item["topic"],
                "endpoint": "/api/search/semantic",
                "method": "POST",
                "payload": {"question": item["query"]},
            }
        )
    return cases


# ---------------------------------------------------------------------------
# 请求执行
# ---------------------------------------------------------------------------
def execute_request(base_url: str, case: dict) -> dict:
    """执行单个测试用例，返回带性能数据的结果。"""
    url = f"{base_url}{case['endpoint']}"
    payload = case.get("payload", {})

    result = {
        "topic": case["topic"],
        "endpoint": case["endpoint"],
        "method": case["method"],
        "payload_summary": _summarize_payload(payload),
        "success": False,
        "status_code": None,
        "response_time_ms": None,
        "output_length": 0,
        "output_tokens_est": 0,
        "error": None,
    }

    try:
        start = time.perf_counter()
        # 流式接口用 stream=True 读完再统计
        resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT, stream=True)
        elapsed = (time.perf_counter() - start) * 1000  # ms

        result["status_code"] = resp.status_code
        result["response_time_ms"] = round(elapsed, 1)

        if resp.status_code == 200:
            # 读取全部响应内容
            content = resp.text
            result["output_length"] = len(content)
            result["output_tokens_est"] = _estimate_tokens(content)
            result["success"] = True

            # 尝试解析 JSON 获取更多信息
            try:
                data = resp.json()
                if isinstance(data, dict):
                    # 某些接口返回 model 字段
                    if "model" in data:
                        result["model"] = data["model"]
                    # AskResponse 有 answer 字段
                    if "answer" in data:
                        result["output_length"] = len(data["answer"])
                        result["output_tokens_est"] = _estimate_tokens(data["answer"])
                    # rewrite 返回 rewritten 字段
                    if "rewritten" in data:
                        result["output_length"] = len(data["rewritten"])
                        result["output_tokens_est"] = _estimate_tokens(data["rewritten"])
            except Exception:
                pass
        else:
            result["error"] = resp.text[:500]

    except requests.Timeout:
        result["error"] = "请求超时"
    except requests.ConnectionError:
        result["error"] = "连接失败（后端服务未启动？）"
    except Exception as e:
        result["error"] = str(e)[:500]

    return result


def _summarize_payload(payload: dict) -> str:
    """生成 payload 的简短摘要。"""
    parts = []
    for k, v in payload.items():
        s = str(v)
        if len(s) > 80:
            s = s[:77] + "..."
        parts.append(f"{k}={s}")
    return "; ".join(parts)


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数：中文约 2 字符/token，英文约 4 字符/token。"""
    if not text:
        return 0
    cn_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - cn_chars
    return cn_chars // 2 + other_chars // 4


# ---------------------------------------------------------------------------
# 断点续传
# ---------------------------------------------------------------------------
def load_completed_cases(completed_path: Path) -> set[str]:
    """加载已完成的用例 ID 集合。"""
    if not completed_path.exists():
        return set()
    try:
        with open(completed_path, encoding="utf-8") as f:
            data = json.load(f)
            return set(data.get("completed", []))
    except Exception:
        return set()


def save_completed_case(completed_path: Path, case_id: str) -> None:
    """保存已完成的用例 ID。"""
    completed = load_completed_cases(completed_path)
    completed.add(case_id)
    with open(completed_path, "w", encoding="utf-8") as f:
        json.dump({"completed": list(completed)}, f, ensure_ascii=False, indent=2)


def generate_case_id(case: dict, index: int) -> str:
    """生成用例的唯一 ID。"""
    return f"{case['endpoint']}_{index}"


# ---------------------------------------------------------------------------
# 带重试的请求执行
# ---------------------------------------------------------------------------
def execute_request_with_retry(
    base_url: str, case: dict, case_id: str, completed_path: Path
) -> dict:
    """执行单个测试用例，支持指数退避重试。"""
    max_retries = MAX_RETRIES
    backoff = INITIAL_BACKOFF

    for attempt in range(max_retries + 1):
        result = execute_request(base_url, case)

        if result["success"]:
            # 成功则保存到断点续传文件
            save_completed_case(completed_path, case_id)
            return result

        # 失败重试
        if attempt < max_retries:
            wait_time = backoff * (2**attempt)  # 指数退避
            print(f"    ↳ 请求失败，{wait_time:.0f}秒后重试 ({attempt + 1}/{max_retries})")
            time.sleep(wait_time)
        else:
            # 重试次数用完，仍然保存结果（标记为失败）
            save_completed_case(completed_path, case_id)
            return result

    return result  # 不会执行到这里，但保持类型完整


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------
def generate_report(all_results: dict[str, list[dict]], output_dir: Path) -> None:
    """生成 benchmark_report.md。"""
    lines = [
        "# PaperForge AI 功能 Benchmark 报告",
        "",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. 总体概览",
        "",
    ]

    # 总体统计
    total_cases = sum(len(r) for r in all_results.values())
    total_success = sum(1 for r_list in all_results.values() for r in r_list if r["success"])
    total_fail = total_cases - total_success
    all_times = [
        r["response_time_ms"]
        for r_list in all_results.values()
        for r in r_list
        if r["response_time_ms"] is not None
    ]

    lines.append("| 指标 | 值 |")
    lines.append("|------|-----|")
    lines.append(f"| 总测试用例数 | {total_cases} |")
    lines.append(f"| 成功数 | {total_success} |")
    lines.append(f"| 失败数 | {total_fail} |")
    lines.append(f"| 总体成功率 | {total_success / total_cases * 100:.1f}% |")
    if all_times:
        lines.append(f"| 平均响应时间 | {sum(all_times) / len(all_times):.0f} ms |")
        lines.append(f"| 最快响应时间 | {min(all_times):.0f} ms |")
        lines.append(f"| 最慢响应时间 | {max(all_times):.0f} ms |")
        lines.append(f"| P50 响应时间 | {sorted(all_times)[len(all_times) // 2]:.0f} ms |")
        lines.append(f"| P95 响应时间 | {sorted(all_times)[int(len(all_times) * 0.95)]:.0f} ms |")

    total_tokens = sum(r["output_tokens_est"] for r_list in all_results.values() for r in r_list)
    total_chars = sum(r["output_length"] for r_list in all_results.values() for r in r_list)
    lines.append(f"| 总输出字符数 | {total_chars:,} |")
    lines.append(f"| 估算总 Token 数 | {total_tokens:,} |")
    lines.append("")

    # 各功能详情
    lines.append("## 2. 各功能性能分析")
    lines.append("")

    endpoint_names = {
        "/api/ask": "智能问答",
        "/api/writing/chapters/1/continue": "智能续写",
        "/api/writing/chapters/1/rewrite": "改写润色",
        "/api/writing/generate-outline": "大纲生成",
        "/api/writing/chapters/1/suggest-structure": "结构建议",
        "/api/search/semantic": "语义搜索",
    }

    for endpoint_key, results in all_results.items():
        name = endpoint_names.get(endpoint_key, endpoint_key)
        lines.append(
            f"### 2.{list(all_results.keys()).index(endpoint_key) + 1} {name}（`{endpoint_key}`）"
        )
        lines.append("")

        success = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]
        times = [r["response_time_ms"] for r in success if r["response_time_ms"]]
        tokens = [r["output_tokens_est"] for r in success]
        lengths = [r["output_length"] for r in success]

        lines.append("| 指标 | 值 |")
        lines.append("|------|-----|")
        lines.append(f"| 测试用例数 | {len(results)} |")
        lines.append(f"| 成功/失败 | {len(success)}/{len(failed)} |")
        lines.append(f"| 成功率 | {len(success) / len(results) * 100:.1f}% |")

        if times:
            lines.append(f"| 平均响应时间 | {sum(times) / len(times):.0f} ms |")
            lines.append(f"| 最快 | {min(times):.0f} ms |")
            lines.append(f"| 最慢 | {max(times):.0f} ms |")
            sorted_times = sorted(times)
            lines.append(f"| P50 | {sorted_times[len(sorted_times) // 2]:.0f} ms |")
            lines.append(f"| P95 | {sorted_times[int(len(sorted_times) * 0.95)]:.0f} ms |")

        if tokens:
            lines.append(f"| 平均输出 Token | {sum(tokens) / len(tokens):.0f} |")
            lines.append(f"| 总输出 Token | {sum(tokens):,} |")
        if lengths:
            lines.append(f"| 平均输出字符 | {sum(lengths) / len(lengths):.0f} |")
        lines.append("")

        # 按主题分布
        topic_stats: dict[str, dict] = {}
        for r in results:
            t = r["topic"]
            if t not in topic_stats:
                topic_stats[t] = {"count": 0, "success": 0, "times": [], "tokens": []}
            topic_stats[t]["count"] += 1
            if r["success"]:
                topic_stats[t]["success"] += 1
                if r["response_time_ms"]:
                    topic_stats[t]["times"].append(r["response_time_ms"])
                topic_stats[t]["tokens"].append(r["output_tokens_est"])

        lines.append("**按主题分布：**")
        lines.append("")
        lines.append("| 主题 | 用例数 | 成功率 | 平均响应时间 | 平均输出 Token |")
        lines.append("|------|--------|--------|-------------|---------------|")
        for t, s in topic_stats.items():
            avg_time = f"{sum(s['times']) / len(s['times']):.0f} ms" if s["times"] else "N/A"
            avg_tok = f"{sum(s['tokens']) / len(s['tokens']):.0f}" if s["tokens"] else "N/A"
            lines.append(
                f"| {t} | {s['count']} | {s['success'] / s['count'] * 100:.0f}% | {avg_time} | {avg_tok} |"
            )
        lines.append("")

        # 失败详情
        if failed:
            lines.append("**失败用例（前 5 条）：**")
            lines.append("")
            for r in failed[:5]:
                lines.append(
                    f"- `{r['payload_summary'][:80]}` → {r.get('error', '未知错误')[:100]}"
                )
            lines.append("")

    # 按主题汇总
    lines.append("## 3. 按主题汇总")
    lines.append("")
    all_topic_stats: dict[str, dict] = {}
    for results in all_results.values():
        for r in results:
            t = r["topic"]
            if t not in all_topic_stats:
                all_topic_stats[t] = {"count": 0, "success": 0, "times": [], "tokens": []}
            all_topic_stats[t]["count"] += 1
            if r["success"]:
                all_topic_stats[t]["success"] += 1
                if r["response_time_ms"]:
                    all_topic_stats[t]["times"].append(r["response_time_ms"])
                all_topic_stats[t]["tokens"].append(r["output_tokens_est"])

    lines.append("| 主题 | 总用例 | 成功 | 成功率 | 平均响应时间 | 总 Token |")
    lines.append("|------|--------|------|--------|-------------|---------|")
    for t, s in all_topic_stats.items():
        avg_time = f"{sum(s['times']) / len(s['times']):.0f} ms" if s["times"] else "N/A"
        lines.append(
            f"| {t} | {s['count']} | {s['success']} | {s['success'] / s['count'] * 100:.0f}% | {avg_time} | {sum(s['tokens']):,} |"
        )
    lines.append("")

    # 结论
    lines.append("## 4. 结论与建议")
    lines.append("")
    if total_fail > 0:
        lines.append(f"- 共有 {total_fail} 个请求失败，建议检查后端服务和 LLM 模型配置。")
    if all_times:
        p95 = sorted(all_times)[int(len(all_times) * 0.95)]
        if p95 > 10000:
            lines.append(f"- P95 响应时间 {p95:.0f}ms 较慢，建议优化 LLM 调用或增加超时时间。")
        elif p95 > 5000:
            lines.append(
                f"- P95 响应时间 {p95:.0f}ms，属于可接受范围，流式接口可进一步优化用户体验。"
            )
        else:
            lines.append(f"- P95 响应时间 {p95:.0f}ms，性能良好。")
    lines.append(f"- 估算总 Token 消耗：{total_tokens:,} tokens（含输入 + 输出）。")
    lines.append("- 实际 Token 消耗约为估算值的 2-3 倍（输入 Prompt 通常比输出更长）。")
    lines.append("")

    report_path = output_dir / "benchmark_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已保存：{report_path}")


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="PaperForge AI 功能批量测试")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="后端服务地址")
    parser.add_argument("--output", default="benchmark_results.json", help="结果输出文件")
    parser.add_argument("--chapter-id", type=int, default=1, help="用于续写/改写/结构建议的章节 ID")
    parser.add_argument("--project-id", type=int, default=1, help="用于大纲生成的项目 ID")
    parser.add_argument("--skip-stream", action="store_true", help="跳过流式接口测试")
    parser.add_argument("--start", type=int, default=0, help="起始用例索引（从 0 开始）")
    parser.add_argument("--end", type=int, default=-1, help="结束用例索引（-1 表示全部）")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_dir = output_path.parent if output_path.parent != Path(".") else Path(".")
    completed_path = output_dir / COMPLETED_FILE

    # 1. 健康检查
    print(f"正在检查后端服务：{args.base_url}/api/health ...")
    try:
        resp = requests.get(f"{args.base_url}/api/health", timeout=10)
        if resp.status_code != 200:
            print(f"后端返回 {resp.status_code}，请确认服务已启动。")
            sys.exit(1)
        health = resp.json()
        print(f"后端在线，论文数：{health.get('papers', '?')}")
    except Exception as e:
        print(f"无法连接后端：{e}")
        print("请先启动后端：uvicorn mock_api.main:app --port 8770")
        sys.exit(1)

    # 2. 生成测试用例
    print("\n正在生成测试用例...")
    test_suites = {
        "ask": generate_ask_cases(),
        "continue": generate_continue_cases(args.chapter_id),
        "rewrite": generate_rewrite_cases(args.chapter_id),
        "outline": generate_outline_cases(args.project_id),
        "suggest": generate_suggest_cases(args.chapter_id),
        "semantic": generate_semantic_search_cases(),
    }

    # 将所有用例合并到一个列表
    all_cases = []
    for suite_name, cases in test_suites.items():
        for case in cases:
            case["suite"] = suite_name
            all_cases.append(case)

    total_cases = len(all_cases)
    print(f"共生成 {total_cases} 个测试用例（6 个功能 × 50 个用例）")

    # 应用分批范围
    end_index = args.end if args.end >= 0 else total_cases
    start_index = min(args.start, total_cases)
    end_index = min(end_index, total_cases)
    cases_to_run = all_cases[start_index:end_index]
    print(f"本次运行范围：[{start_index}, {end_index})，共 {len(cases_to_run)} 个用例")

    # 3. 断点续传：加载已完成的用例
    completed_cases = load_completed_cases(completed_path)
    if completed_cases:
        skipped = sum(
            1
            for i, case in enumerate(cases_to_run, start=start_index)
            if generate_case_id(case, i) in completed_cases
        )
        print(f"已跳过 {skipped} 个已完成用例")

    # 4. 批量执行
    all_results: dict[str, list[dict]] = {}
    completed_count = 0
    consecutive_failures = 0
    start_time = time.time()

    for i, case in enumerate(cases_to_run, start=start_index):
        case_id = generate_case_id(case, i)

        # 跳过已完成的用例
        if case_id in completed_cases:
            completed_count += 1
            # 仍然记录结果
            suite_name = case["suite"]
            endpoint_label = case["endpoint"]
            if endpoint_label not in all_results:
                all_results[endpoint_label] = []
            all_results[endpoint_label].append(
                {
                    "topic": case["topic"],
                    "endpoint": case["endpoint"],
                    "method": case["method"],
                    "payload_summary": _summarize_payload(case.get("payload", {})),
                    "success": True,
                    "status_code": None,
                    "response_time_ms": None,
                    "output_length": 0,
                    "output_tokens_est": 0,
                    "error": None,
                    "skipped": True,
                }
            )
            continue

        suite_name = case["suite"]
        endpoint_label = case["endpoint"]
        if endpoint_label not in all_results:
            all_results[endpoint_label] = []

        # 执行请求（带重试）
        result = execute_request_with_retry(args.base_url, case, case_id, completed_path)
        all_results[endpoint_label].append(result)
        completed_count += 1

        # 更新连续失败计数
        if result["success"]:
            consecutive_failures = 0
        else:
            consecutive_failures += 1

        # 实时输出进度（每 10 个用例）
        if completed_count % 10 == 0 or completed_count == len(cases_to_run):
            elapsed = time.time() - start_time
            status = "✓" if result["success"] else "✗"
            time_str = (
                f"{result['response_time_ms']:.0f}ms" if result["response_time_ms"] else "N/A"
            )
            print(
                f"  [{completed_count}/{len(cases_to_run)}] {status} {case['topic'][:6]} | {time_str} | {result['output_tokens_est']} tok | {result['payload_summary'][:50]}"
            )
            print(
                f"    ↳ 进度：{completed_count}/{len(cases_to_run)} | 耗时：{elapsed:.0f}秒 | 连续失败：{consecutive_failures}"
            )

        # 检查是否需要暂停（连续失败太多次）
        if consecutive_failures >= CONSECUTIVE_FAIL_PAUSE:
            print(
                f"\n⚠️  连续失败 {consecutive_failures} 次，暂停 {PAUSE_DURATION / 60:.0f} 分钟..."
            )
            time.sleep(PAUSE_DURATION)
            consecutive_failures = 0
            print("继续执行...")

        # 随机限流间隔
        time.sleep(random.uniform(MIN_REQUEST_INTERVAL, MAX_REQUEST_INTERVAL))

    # 5. 保存结果
    output_data = {
        "generated_at": datetime.now().isoformat(),
        "base_url": args.base_url,
        "total_cases": total_cases,
        "range": f"[{start_index}, {end_index})",
        "cases_run": len(cases_to_run),
        "results": all_results,
    }
    output_path.write_text(json.dumps(output_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已保存：{output_path}")

    # 6. 生成报告
    generate_report(all_results, output_dir)
    print("\n测试完成！")


if __name__ == "__main__":
    main()
