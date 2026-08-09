"""CS Paper Experiment Auditor —— 论文实验审计模块（P0）。

按《CS Paper Experiment Auditor — 增强落地指南》实现 10 种 Finding 检测：

- P0-1 正文-表格-图注数字一致性（NUMERIC_MISMATCH）
- P0-2 指标数学自洽性 P/R/F1/Acc/CM（METRIC_INCONSISTENCY）
- P0-3 Ablation 结论检查（ABLATION_UNSUPPORTED）
- P0-4 图表坐标轴审计（CHART_AXIS_RISK）
- P0-5 实验信息完整性（MISSING_REPRO_INFO）
- P0-6 Baseline 公平性初筛（BASELINE_UNFAIR）
- P0-7 数据泄漏初筛（DATA_LEAKAGE_CANDIDATE）
- P0-8 标准差/显著性缺失（STD_OR_SIGNIFICANCE_MISSING）
- P0-9 曲线复用候选（FIGURE_REUSE_CANDIDATE）
- P0-10 审计报告生成（report.py）

设计原则（与 integrity_report.py 一致）：
- **确定性优先**：能用公式/规则判定的绝不让 LLM 猜；LLM 仅用于语义抽取。
- **fail-open**：单项检测异常不拖垮整体，跳过原因记入 checks_run。
- **每个 Finding 附 normal_explanation**：给出良性解释，避免把审计做成
  「定罪工具」；needs_human_review 标记交人工终审。
"""
