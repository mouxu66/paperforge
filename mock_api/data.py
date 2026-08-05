"""PaperForge mock 种子数据 — 基于真实 arXiv 论文的示例条目。

仅提供原始字典列表，供 crud.seed_if_empty 写入 SQLite。
字段沿用 camelCase（与历史数据一致），由 crud 负责转换为 ORM snake_case 列。
"""

from __future__ import annotations

# 一组覆盖各分类的示例论文（数据为演示用，非精确引用数）
SEED_PAPERS: list[dict] = [
    {
        "id": "2106.09685",
        "title": "LoRA: Low-Rank Adaptation of Large Language Models",
        "authors": ["Edward J. Hu", "Yelong Shen", "Phillip Wallis", "Zeyuan Allen-Zhu"],
        "year": 2021,
        "abstract": (
            "自然语言处理的一项重要技术是自然语言理解。我们提出低秩适应（LoRA），它冻结预训练模型权重，"
            "并将可训练的秩分解矩阵注入到 Transformer 架构的每一层，大幅减少下游任务的可训练参数数量。"
            "与 GPT-3 175B 的微调相比，LoRA 可将可训练参数减少 10000 倍、GPU 内存需求减少 3 倍，"
            "同时在 RoBERTa、DeBERTa、GPT-2 和 GPT-3 上表现不逊于或优于微调。"
        ),
        "category": "lora",
        "tags": ["LoRA", "PEFT", "Transformer"],
        "citations": 12800,
        "chunkCount": 142,
        "indexSize": 3_840_000,
        "source": "arxiv",
    },
    {
        "id": "2305.14314",
        "title": "QLoRA: Efficient Finetuning of Quantized LLMs",
        "authors": ["Tim Dettmers", "Artidoro Pagnoni", "Ari Holtzman", "Luke Zettlemoyer"],
        "year": 2023,
        "abstract": (
            "我们提出 QLoRA，一种高效的微调方法，可在保持完整的 16 位微调任务性能的同时，将内存使用降低到足以"
            "在单张 48GB GPU 上微调 65B 参数模型。QLoRA 通过 4-bit 量化预训练模型，并把梯度反向传播到"
            "低秩适配器（LoRA）中。我们引入了 NF4 量化与双重量化以减少平均内存占用。"
        ),
        "category": "quant",
        "tags": ["QLoRA", "4-bit", "量化", "PEFT"],
        "citations": 4200,
        "chunkCount": 168,
        "indexSize": 4_510_000,
        "source": "arxiv",
    },
    {
        "id": "2402.03300",
        "title": "DeepSeekMath: Pushing the Limits of Mathematical Reasoning in Open Language Models",
        "authors": ["Zhihong Shao", "Pei Ju Wang", "Qihao Zhu", "Runxin Xu"],
        "year": 2024,
        "abstract": (
            "数学推理对语言模型是一项重大挑战。本文介绍 DeepSeekMath 7B，在 DeepSeek-Coder-Base-v1.5 7B 基础上"
            "继续预训练，使用来自 Common Crawl 的 1200 亿与数学相关的 token，以及 350 亿自然语言与代码 token。"
            "我们提出群组相对策略优化（GRPO），是 PPO 的一种变体，能显著提升数学推理能力。"
        ),
        "category": "rl",
        "tags": ["GRPO", "强化学习", "数学推理", "DeepSeek"],
        "citations": 980,
        "chunkCount": 210,
        "indexSize": 5_980_000,
        "source": "arxiv",
    },
    {
        "id": "2402.09353",
        "title": "MADoRA: A Fine-tuning Method for Multi-Aspect Data",
        "authors": ["Yuxuan Hu", "Xudong Liu", "Jingjing Wang"],
        "year": 2024,
        "abstract": (
            "我们提出 MADoRA，一种面向多方面数据的参数高效微调方法，通过对 LoRA 权重增量进行多方面分解，"
            "在不同数据子集上学习互补的低秩适配器，并在推理时按方面动态组合。实验表明该方法在常识推理、"
            "图像描述与视频理解任务上均优于标准 LoRA。"
        ),
        "category": "lora",
        "tags": ["LoRA", "多方面", "PEFT"],
        "citations": 76,
        "chunkCount": 134,
        "indexSize": 3_620_000,
        "source": "arxiv",
    },
    {
        "id": "2402.12065",
        "title": "POQ: Parameterized Orthogonal Quantization for Efficient LLM Inference",
        "authors": ["Changhun Lee", "Seung-Woo Ko", "Jinhae Park"],
        "year": 2024,
        "abstract": (
            "我们提出参数化正交量化（POQ），通过正交变换降低权重矩阵的量化误差，从而在超低比特量化下"
            "保持大语言模型的精度。POQ 在 2-bit 与 3-bit 设置下显著优于现有方法，并可与 LoRA 结合实现"
            "量化感知微调。"
        ),
        "category": "quant",
        "tags": ["量化", "正交变换", "推理加速"],
        "citations": 54,
        "chunkCount": 118,
        "indexSize": 3_180_000,
        "source": "arxiv",
    },
    {
        "id": "2403.14572",
        "title": "Style Content Blended LoRA for Artistic Image Generation",
        "authors": ["Maria Chen", "Li Wei", "Tomás Ribeiro"],
        "year": 2024,
        "abstract": (
            "风格迁移在艺术创作中应用广泛。本文提出一种风格-内容混合的 LoRA 训练方案，将风格信息与内容"
            "信息解耦并注入扩散模型的不同注意力层，实现可控的艺术风格迁移。在多组风格基准上取得了更优的"
            "风格保真度与内容一致性。"
        ),
        "category": "cv",
        "tags": ["风格迁移", "扩散模型", "LoRA"],
        "citations": 41,
        "chunkCount": 96,
        "indexSize": 2_590_000,
        "source": "arxiv",
    },
    {
        "id": "2201.00978",
        "title": "TNT: Transformer-in-Transformer for Image Recognition",
        "authors": ["Kai Han", "Yunhe Wang", "Jianyuan Guo"],
        "year": 2022,
        "abstract": (
            "视觉 Transformer 通常将图像划分为 patch 并展平为序列。我们提出 Transformer-in-Transformer（TNT），"
            "在 outer Transformer 建模 patch 间关系的同时，用 inner Transformer 建模 patch 内部像素间关系，"
            "提升了图像识别的表征能力。在 ImageNet 上取得了优于 ViT 的精度。"
        ),
        "category": "cv",
        "tags": ["ViT", "图像识别", "Transformer"],
        "citations": 620,
        "chunkCount": 88,
        "indexSize": 2_370_000,
        "source": "arxiv",
    },
    {
        "id": "2110.06500",
        "title": "GPT-experiments: Probing In-Context Learning in Large Language Models",
        "authors": ["Susan Zhang", "Stephen Roller", "Naman Goyal"],
        "year": 2021,
        "abstract": (
            "我们系统研究了大型语言模型在上下文学习（in-context learning）中的行为，分析模型规模、"
            "示例数量与示例顺序对任务表现的影响。结果表明，超过一定规模后模型展现出少样本学习能力，"
            "且对示例顺序敏感。"
        ),
        "category": "llm",
        "tags": ["上下文学习", "少样本", "GPT"],
        "citations": 510,
        "chunkCount": 102,
        "indexSize": 2_750_000,
        "source": "arxiv",
    },
    {
        "id": "2002.08208",
        "title": "LoRa Physical Layer: GNU Radio Implementation and BER Analysis",
        "authors": ["Alexandru Lavric", "Valentin Popa"],
        "year": 2020,
        "abstract": (
            "LoRa 是一种低功耗广域网（LPWAN）调制技术。本文基于 GNU Radio 实现 LoRa 物理层，"
            "并在加性高斯白噪声信道下分析误码率（BER）性能。实验评估了载波频率偏移与编码率对 BER 的影响，"
            "为 LoRa 系统部署提供参考。"
        ),
        "category": "comm",
        "tags": ["LoRa", "物理层", "GNU Radio", "BER"],
        "citations": 145,
        "chunkCount": 64,
        "indexSize": 1_720_000,
        "source": "ieee",
    },
    {
        "id": "2402.14679",
        "title": "Efficient Adapter Tuning for Multimodal Foundation Models",
        "authors": ["Dongsheng Luo", "Wei Yuan", "Hao Chen"],
        "year": 2024,
        "abstract": (
            "多模态基础模型的微调成本高昂。我们提出一种高效适配器方案，在视觉与语言编码器中插入轻量适配模块，"
            "仅训练不到 1% 的参数即可达到全量微调性能。在图文检索与视觉问答任务上验证了有效性。"
        ),
        "category": "lora",
        "tags": ["适配器", "多模态", "PEFT"],
        "citations": 33,
        "chunkCount": 110,
        "indexSize": 2_960_000,
        "source": "arxiv",
    },
    {
        "id": "1706.03762",
        "title": "Attention Is All You Need",
        "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar", "Jakob Uszkoreit"],
        "year": 2017,
        "abstract": (
            "主流的序列转换模型基于复杂的循环或卷积神经网络，其中编码器与解码器通过注意力机制连接。"
            "我们提出一种全新的简单网络架构 Transformer，仅依赖注意力机制，完全摒弃循环与卷积。"
            "在机器翻译任务上 Transformer 显著优于现有模型，并支持更高效的并行训练。"
        ),
        "category": "llm",
        "tags": ["Transformer", "注意力", "seq2seq"],
        "citations": 95000,
        "chunkCount": 76,
        "indexSize": 2_050_000,
        "source": "arxiv",
    },
    {
        "id": "2005.14165",
        "title": "Language Models are Few-Shot Learners (GPT-3)",
        "authors": ["Tom Brown", "Benjamin Mann", "Nick Ryder", "Melanie Subbiah"],
        "year": 2020,
        "abstract": (
            "我们训练了 GPT-3，一个拥有 1750 亿参数的自回归语言模型，比以往任何非稀疏语言模型大 10 倍。"
            "在少样本设置下，GPT-3 在翻译、问答、完形填空等任务上表现出色，无需梯度更新或微调。"
        ),
        "category": "llm",
        "tags": ["GPT-3", "少样本", "大模型"],
        "citations": 28000,
        "chunkCount": 198,
        "indexSize": 5_320_000,
        "source": "arxiv",
    },
    {
        "id": "2203.02155",
        "title": "Training language models to follow instructions with human feedback (InstructGPT)",
        "authors": ["Long Ouyang", "Jeffrey Wu", "Xu Jiang", "Diogo Almeida"],
        "year": 2022,
        "abstract": (
            "我们提出基于人类反馈的强化学习（RLHF）对语言模型进行对齐，使 GPT-3 更好地遵循用户指令。"
            "标注者对模型输出进行排序，训练奖励模型并用 PPO 优化策略。由此得到的 InstructGPT 在指令遵循上"
            "显著优于 GPT-3。"
        ),
        "category": "rl",
        "tags": ["RLHF", "对齐", "PPO", "InstructGPT"],
        "citations": 6800,
        "chunkCount": 156,
        "indexSize": 4_200_000,
        "source": "arxiv",
    },
    {
        "id": "2303.08774",
        "title": "GPT-4 Technical Report",
        "authors": ["OpenAI"],
        "year": 2023,
        "abstract": (
            "GPT-4 是一个大规模多模态模型，接受图像与文本输入并输出文本。它在多项学术基准测试上达到人类水平，"
            "包括通过模拟律师资格考试（得分位列前 10%）。本文介绍 GPT-4 的能力、局限与对齐方法。"
        ),
        "category": "llm",
        "tags": ["GPT-4", "多模态", "对齐"],
        "citations": 5200,
        "chunkCount": 220,
        "indexSize": 5_910_000,
        "source": "arxiv",
    },
    {
        "id": "2304.07196",
        "title": "PubMedQA: A Dataset for Biomedical Research Question Answering",
        "authors": ["Qiao Jin", "Bhuwan Dhingra", "Zhengping Liu", "William Cohen"],
        "year": 2023,
        "abstract": (
            "我们构建 PubMedQA，一个用于生物医学研究问答的数据集，包含 1000 个专家标注与 211k 个"
            "人工生成的问答对。该数据集支持对长文本推理与生物医学领域问答系统进行评估。"
        ),
        "category": "llm",
        "tags": ["PubMed", "问答", "生物医学"],
        "citations": 410,
        "chunkCount": 84,
        "indexSize": 2_260_000,
        "source": "pubmed",
    },
    {
        "id": "2210.03629",
        "title": "RLHF + LoRA: Efficient Alignment of Large Language Models",
        "authors": ["Yuxiang Wei", "Zhe Wang", "Yi Lu"],
        "year": 2022,
        "abstract": (
            "RLHF 对齐大模型通常需要训练与奖励模型同等规模的策略模型，成本高昂。我们提出将 LoRA 适配器"
            "应用于 RLHF 流程，仅训练少量参数即可完成对齐，显著降低显存与时间开销。"
        ),
        "category": "rl",
        "tags": ["RLHF", "LoRA", "对齐", "PEFT"],
        "citations": 290,
        "chunkCount": 92,
        "indexSize": 2_480_000,
        "source": "arxiv",
    },
    {
        "id": "2101.09588",
        "title": "A Survey on LoRaWAN for Smart City Applications",
        "authors": ["Jelena Marašević", "Clayton Williamson"],
        "year": 2021,
        "abstract": (
            "LoRaWAN 是智慧城市物联网部署的主流低功耗广域网协议。本文综述 LoRaWAN 的网络架构、"
            "MAC 层机制、可扩展性与安全性，并讨论其在智能停车、环境监测与智能照明中的应用案例。"
        ),
        "category": "comm",
        "tags": ["LoRaWAN", "物联网", "智慧城市"],
        "citations": 215,
        "chunkCount": 70,
        "indexSize": 1_880_000,
        "source": "ieee",
    },
    {
        "id": "2010.11929",
        "title": "An Image is Worth 16x16 Words: Transformers for Image Recognition at Scale (ViT)",
        "authors": ["Alexey Dosovitskiy", "Lucas Beyer", "Alexander Kolesnikov"],
        "year": 2020,
        "abstract": (
            "尽管 Transformer 架构已成为自然语言处理的标准，其在计算机视觉中的应用仍有限。我们提出 Vision "
            "Transformer（ViT），将图像切分为固定大小的 patch 序列直接输入标准 Transformer 编码器。"
            "在大规模数据预训练后，ViT 在 ImageNet 上达到或超越 SOTA 卷积网络。"
        ),
        "category": "cv",
        "tags": ["ViT", "图像识别", "Transformer"],
        "citations": 22000,
        "chunkCount": 90,
        "indexSize": 2_420_000,
        "source": "arxiv",
    },
    {
        "id": "2309.15217",
        "title": "AWQ: Activation-aware Weight Quantization for LLM Compression",
        "authors": ["Lin Ji", "Jiaming Tang", "Haotian Qin"],
        "year": 2023,
        "abstract": (
            "我们提出 AWQ，一种激活感知的权重量化方法，通过保护对模型性能关键的 1% 权重通道，"
            "在 4-bit 量化下几乎无损地压缩大语言模型。AWQ 无需反向传播，泛化性强，并支持高效的"
            "自定义 CUDA kernel 加速推理。"
        ),
        "category": "quant",
        "tags": ["量化", "AWQ", "推理加速"],
        "citations": 880,
        "chunkCount": 124,
        "indexSize": 3_340_000,
        "source": "arxiv",
    },
    {
        "id": "2305.11206",
        "title": "Survey of Parameter-Efficient Fine-Tuning Methods for Large Models",
        "authors": ["Ruohao Guo", "Wei Hu", "Yifan Zhang"],
        "year": 2023,
        "abstract": (
            "参数高效微调（PEFT）旨在以极少量可训练参数适配大模型。本文系统综述适配器、前缀微调、"
            "LoRA 及其变体，比较其在参数量、内存占用与下游性能上的权衡，并讨论多任务与持续学习场景下的应用。"
        ),
        "category": "lora",
        "tags": ["PEFT", "综述", "适配器", "LoRA"],
        "citations": 540,
        "chunkCount": 160,
        "indexSize": 4_310_000,
        "source": "springer",
    },
    {
        "id": "2106.04561",
        "title": "Prefix-Tuning: Optimizing Continuous Prompts for Generation",
        "authors": ["Xiang Lisa Li", "Percy Liang"],
        "year": 2021,
        "abstract": (
            "微调大模型需存储并更新全部参数。我们提出 prefix-tuning，在每一层前缀一组可训练的连续 prompt 向量，"
            "冻结模型主体。在 GPT-2 与 BART 上，prefix-tuning 以 0.1% 的参数量达到接近全量微调的性能。"
        ),
        "category": "lora",
        "tags": ["prefix-tuning", "PEFT", "prompt"],
        "citations": 1900,
        "chunkCount": 78,
        "indexSize": 2_100_000,
        "source": "arxiv",
    },
    {
        "id": "1909.11942",
        "title": "ALBERT: A Lite BERT for Self-supervised Learning of Language Representations",
        "authors": ["Zhenzhong Lan", "Mingda Chen", "Sebastian Goodman"],
        "year": 2019,
        "abstract": (
            "为降低 BERT 的参数量与内存占用，我们提出 ALBERT，通过参数共享与因式分解嵌入参数化减少参数规模。"
            "ALBERT 在 GLUE、SQuAD 等基准上取得更好性能，同时显著降低参数量。"
        ),
        "category": "llm",
        "tags": ["BERT", "参数共享", "预训练"],
        "citations": 4200,
        "chunkCount": 82,
        "indexSize": 2_210_000,
        "source": "arxiv",
    },
    {
        "id": "2308.10792",
        "title": "Deep Reinforcement Learning for Wireless Resource Allocation",
        "authors": ["Hao Ye", "Geoffrey Ye Li"],
        "year": 2023,
        "abstract": (
            "无线网络资源分配问题通常建模为优化问题，传统方法难以应对动态环境。本文综述深度强化学习在"
            "功率控制、信道接入与波束赋形中的应用，并讨论多智能体 RL 在大规模网络中的可扩展性。"
        ),
        "category": "comm",
        "tags": ["强化学习", "无线网络", "资源分配"],
        "citations": 128,
        "chunkCount": 66,
        "indexSize": 1_780_000,
        "source": "ieee",
    },
    {
        "id": "2304.14178",
        "title": "Springer Survey: Foundation Models for Scientific Discovery",
        "authors": ["Anjali Gupta", "Markus Weber"],
        "year": 2023,
        "abstract": (
            "基础模型正在改变科学研究范式。本文综述基础模型在材料科学、生物医学与化学合成中的应用，"
            "讨论数据驱动发现、不确定性量化与可解释性挑战，并展望自动化实验闭环。"
        ),
        "category": "llm",
        "tags": ["基础模型", "科学发现", "综述"],
        "citations": 96,
        "chunkCount": 144,
        "indexSize": 3_880_000,
        "source": "springer",
    },
]


# ===========================================================================
# 写作模板 —— 预设大纲结构，新建项目时可选择自动生成章节
# ===========================================================================
TEMPLATES: list[dict] = [
    {
        "id": "cs_paper",
        "name": "计算机科学论文",
        "description": "标准计算机科学会议/期刊论文结构，适合 AI/ML/系统方向",
        "chapters": [
            {"title": "引言", "children": []},
            {"title": "相关工作", "children": []},
            {
                "title": "方法",
                "children": [
                    {"title": "问题定义"},
                    {"title": "模型架构"},
                    {"title": "训练细节"},
                ],
            },
            {
                "title": "实验",
                "children": [
                    {"title": "数据集"},
                    {"title": "基线方法"},
                    {"title": "结果分析"},
                ],
            },
            {"title": "结论", "children": []},
        ],
    },
    {
        "id": "bio_medical",
        "name": "生物医学论文",
        "description": "IMRaD 结构，适合临床/基础医学研究",
        "chapters": [
            {"title": "引言", "children": []},
            {
                "title": "材料与方法",
                "children": [
                    {"title": "研究对象"},
                    {"title": "实验设计"},
                    {"title": "统计方法"},
                ],
            },
            {
                "title": "结果",
                "children": [
                    {"title": "基线特征"},
                    {"title": "主要终点"},
                    {"title": "次要终点"},
                ],
            },
            {"title": "讨论", "children": []},
            {"title": "结论", "children": []},
        ],
    },
    {
        "id": "survey",
        "name": "综述论文",
        "description": "系统性综述结构，涵盖分类、对比与未来方向",
        "chapters": [
            {"title": "引言", "children": []},
            {"title": "背景与定义", "children": []},
            {
                "title": "分类体系",
                "children": [
                    {"title": "基于方法"},
                    {"title": "基于应用"},
                ],
            },
            {"title": "代表性工作", "children": []},
            {
                "title": "对比分析",
                "children": [
                    {"title": "性能对比"},
                    {"title": "效率对比"},
                ],
            },
            {"title": "挑战与未来方向", "children": []},
            {"title": "结论", "children": []},
        ],
    },
    {
        "id": "thesis",
        "name": "学位论文",
        "description": "完整的硕博学位论文结构，包含绪论至展望",
        "chapters": [
            {
                "title": "第一章 绪论",
                "children": [
                    {"title": "研究背景"},
                    {"title": "研究现状"},
                    {"title": "本文工作"},
                ],
            },
            {
                "title": "第二章 理论基础",
                "children": [
                    {"title": "基本概念"},
                    {"title": "相关理论"},
                ],
            },
            {
                "title": "第三章 核心方法",
                "children": [
                    {"title": "模型设计"},
                    {"title": "算法实现"},
                ],
            },
            {
                "title": "第四章 实验与分析",
                "children": [
                    {"title": "实验设置"},
                    {"title": "结果讨论"},
                ],
            },
            {"title": "第五章 总结与展望", "children": []},
        ],
    },
]
