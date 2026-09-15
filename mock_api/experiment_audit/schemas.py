"""实验审计 Finding 类型注册表与 Pydantic schema。

FINDING_TYPES 是所有 Finding 类型的唯一事实源：severity 默认值、中文描述、
示例与检测方法说明都从这里取，检测器与报告渲染禁止各自硬编码。
"""

from __future__ import annotations

import itertools
import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ── Finding 类型注册表（指南第 2 节，唯一事实源）──────────────────
FINDING_TYPES: dict[str, dict[str, str]] = {
    "NUMERIC_MISMATCH": {
        "severity": "high",
        "description": "正文、表格、图注中的数字不一致",
        "example": "正文声称提升 4.2%，Table 2 实际提升 1.9 个百分点",
        "check": "cross-reference numbers across sections",
    },
    "METRIC_INCONSISTENCY": {
        "severity": "high",
        "description": "Precision/Recall/F1 数学不自洽",
        "example": "P=82.0, R=85.0, 报告 F1=84.6, 实际 F1=83.47",
        "check": "validate F1 = 2PR/(P+R), check confusion matrix consistency",
    },
    "ABLATION_UNSUPPORTED": {
        "severity": "medium",
        "description": "Ablation 结论不被表格数据支持",
        "example": "声称 'each component contributes', 但单独加入后指标下降",
        "check": "compare ablation rows against claim text",
    },
    "CHART_AXIS_RISK": {
        "severity": "low",
        "description": "图表坐标轴可能造成视觉误导",
        "example": "y 轴从 90 开始而非 0，放大微小差异",
        "check": "detect truncated/broken axes, missing units, scale inconsistency",
    },
    "BASELINE_UNFAIR": {
        "severity": "high",
        "description": "Baseline 比较条件不公平",
        "example": "baseline 用不同分辨率/预训练数据/训练预算",
        "check": "compare training configs, data, budget across methods",
    },
    "MISSING_REPRO_INFO": {
        "severity": "medium",
        "description": "缺少复现所需的关键信息",
        "example": "未报告随机种子、标准差、checkpoint 选择规则",
        "check": "checklist: seed, std, hardware, lr, batch size, epochs, early stopping",
    },
    "DATA_LEAKAGE_CANDIDATE": {
        "severity": "high",
        "description": "训练集和测试集可能存在重叠",
        "example": "train/test 存在相同文件 hash 或高度相似图像",
        "check": "file hash collision, perceptual hash distance, feature collision",
    },
    "UNCERTAINTY_MISSING": {
        "severity": "medium",
        "description": "结果表只报告均值，缺少不确定度（±/标准差/置信区间）；p 值等显著性检验不替代不确定度",
        "example": "Table 3 只有均值，无 ±/标准差，虽有 p 值仍无法判断离散程度",
        "check": "detect missing ±/std/CI on result-table means（p 值/显著性检验不豁免）",
    },
    "SIGNIFICANCE_MISSING": {
        "severity": "medium",
        "description": "报告了指标对比但全文无统计检验（p 值/t-test/显著性检验）",
        "example": "全文比较多个方法，但没有任何 p 值或统计检验说明差异是否显著",
        "check": "detect missing p-values/statistical tests for reported comparisons",
    },
    "CONFIG_MISMATCH": {
        "severity": "medium",
        "description": "论文描述的超参与代码默认值不一致",
        "example": "论文: lr=1e-4, 代码默认: lr=3e-4",
        "check": "compare paper text vs code config files",
    },
    "FIGURE_REUSE_CANDIDATE": {
        "severity": "low",
        "description": "图片或曲线区域与论文内/历史论文高度相似",
        "example": "Figure 3 曲线与 Figure 5 某段高度匹配",
        "check": "pHash recall + SIFT/RANSAC verify + OpenCV visualization",
    },
    "SUSPICIOUS_DATA_PATTERN": {
        "severity": "high",
        "description": "图内数值呈人工编造指纹（等差/重复/末位偏好等）",
        "example": "WT 与 H186R 两组多时间点酶活 6 位小数完全相同，末位数字过度集中于 1",
        "check": "VLM 转写图内数值 → 等差/跨组重复/末位偏好/Benford 统计指纹",
    },
    "REPRODUCTION_BLOCKER": {
        "severity": "high",
        "description": "代码仓库安装/运行失败，无法复现论文结果",
        "example": "pip install 报错、依赖版本冲突、缺少必需数据文件",
        "check": "尝试安装依赖并运行入口脚本，记录失败原因",
    },
    "CITATION_INTEGRITY": {
        "severity": "medium",
        "description": "引用编号不一致或 DOI 疑似编造",
        "example": "文中引用 [25] 但参考文献列表无 25 号；DOI 在 Crossref 查无",
        "check": "引用编号一致性检查 + DOI 真值校验（Crossref API）",
    },
    "CLAIMS_EXTRACTION": {
        "severity": "low",
        "description": "从论文中抽取的实验论断（结构化输入）",
        "example": "Our method achieves 95.2% accuracy, outperforming baseline by 3.1%",
        "check": "LLM 论断抽取 + 规则回退（数值论断 + Figure/Table 引用）",
    },
    "TEXT_DUPLICATION_CANDIDATE": {
        "severity": "high",
        "description": "全文与库内其他论文高度相似（重复发表/论文工厂嫌疑线索）",
        "example": "本文正文与库内另一篇论文的 5-gram Jaccard 相似度达 0.82",
        "check": "word n-gram shingling + Jaccard similarity 库内全文两两比对",
    },
    "SEMANTIC_DUPLICATION_CANDIDATE": {
        "severity": "high",
        "description": "全文与库内论文语义高度相似但措辞不同（换词重写/切香肠线索）",
        "example": "两篇论文词 5-gram Jaccard 仅 0.05，但嵌入向量余弦相似度 0.92",
        "check": "embedding 余弦相似度（语义）+ 词级 Jaccard（表层）双信号，表层低+语义高 → 换词重写",
    },
    "RELABELED_IMAGE_REUSE": {
        "severity": "high",
        "description": "同一张图片/条带像素级复用，但两处标注的目标蛋白/实验不同（改标复用）",
        "example": "同一张 western blot 条带在 A 论文标为 AMPK、在 B 论文标为 GAPDH",
        "check": "跨论文 NCC 条带复用（像素）+ Qwen3-VL 目标蛋白提取（语义）比对",
    },
    "GRIM_INCONSISTENCY": {
        "severity": "medium",
        "description": "报告的小数均值无法由整数样本量推出（GRIM 违例）",
        "example": "mean=4.56, n=30：136/30=4.533→4.53、137/30=4.567→4.57，都不是 4.56",
        "check": "GRIM：均值×样本量必须落在整数 k/n 的舍入区间内",
    },
    "PCURVE_ANOMALY": {
        "severity": "medium",
        "description": "p 值分布异常聚集在显著性阈值附近（p-hacking 线索）",
        "example": "全文 20 个精确 p 值中 8 个落在 0.04~0.05，(0.05,0.1] 区间为 0",
        "check": "p 值抽取 + 勉强显著聚集/重复 p 值/0.05 右侧断崖检测",
    },
    "IMAGE_TAMPERING_CANDIDATE": {
        "severity": "high",
        "description": "单张图内存在复制-粘贴/拼接篡改痕迹（copy-move / 条带克隆）",
        "example": "同一张 western blot 内两条泳道/条带像素级重复，或一块区域被克隆到另一处",
        "check": "SIFT 自匹配（原图/H翻转/V翻转）+ 仿射 RANSAC 几何验证 + 局部簇退化恢复 + 分离/紧凑/IoU 校验（copy-move，平移/旋转/缩放/镜像）；同图条带 NCC 自比对（克隆）",
    },
}

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


class EvidenceSource(BaseModel):
    """一条证据来源（表格/图/正文段落）。"""

    type: str  # table | figure | text
    table_id: str | None = None
    figure_id: str | None = None
    other_figure_id: str | None = None
    page: int | None = None
    snippet: str | None = None
    shared_count: int | None = None
    overlap_pct: float | None = None
    sample_values: list[str] | None = None
    # 证据标注图 URL（读路径由 API 层注入：snippet 为 uploads/figures/<pid>/_audit/*.png
    # 时指向 /api/experiment-audit/evidence-image/<pid>/<filename>，前端 <img> 直用）
    image_url: str | None = None


class Finding(BaseModel):
    """单条审计发现（指南第 2 节标准输出格式）。"""

    finding_id: str = ""
    type: str = Field(description="必须属于 FINDING_TYPES")
    severity: str = "medium"  # high | medium | low
    title: str = ""
    page: int | None = None
    bbox: list[float] | None = None
    claim: str | None = None
    computed: str | None = None
    tolerance: float | None = None
    method: str = ""
    evidence_sources: list[EvidenceSource] = Field(default_factory=list)
    normal_explanation: str = ""
    needs_human_review: bool = False


class AuditRequest(BaseModel):
    """触发审计请求（paper_id 走 path 参数，此处放可选开关）。"""

    checks: list[str] | None = Field(
        default=None, description="限定运行的检测项（None=全部已实现项）"
    )


class AuditResponse(BaseModel):
    """审计结果响应。"""

    audit_id: str
    paper_id: str
    status: str
    source_pdf_hash: str = ""
    findings: list[Finding] = Field(default_factory=list)
    checks_run: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    created_at: str | None = None
    completed_at: str | None = None


class LeakageRequest(BaseModel):
    """P0-7 数据泄漏初筛请求（独立于 PDF，用户提供数据集目录）。"""

    train_dir: str
    test_dir: str
    exact_hash: bool = True
    phash_threshold: int = 10


class CodeAuditRequest(BaseModel):
    """P1-1 代码配置 vs 论文超参比对请求（独立于 PDF，用户提供代码仓库目录）。"""

    paper_id: str
    repo_dir: str


class RelabeledReuseRequest(BaseModel):
    """跨论文改标图片复用检测请求（两篇论文的图做 NCC 像素 + VLM 语义比对）。"""

    paper_a_id: str
    paper_b_id: str
    ncc_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    max_pairs: int = Field(default=20, ge=1, le=100)


def coerce_findings(findings: Any) -> list[dict[str, Any]]:
    """读路径容错：把 DB 里的 findings JSON 校验后转为干净 dict 列表。

    findings 以裸 JSON 存库（experiment_audits.findings），旧代码/手改/异常写入
    可能留下脏数据（severity 越界、evidence_sources 非列表、page 非 int 等），
    直接透传给前端或报告渲染会击穿。此处逐条 ``Finding.model_validate``：
    - 非列表 → 空列表
    - 单条校验失败 → 降级为占位 Finding（severity=low，标需人工复核），
      保留原始文本供追查，不让整体结果因一条脏数据而 500。
    """
    if not isinstance(findings, list):
        return []
    out: list[dict[str, Any]] = []
    for f in findings:
        try:
            out.append(Finding.model_validate(f).model_dump())
        except Exception as e:  # noqa: BLE001 - 读路径容错，任何脏数据都降级不抛
            logger.debug("[audit] Finding schema 校验失败，降级展示: %s", e)
            # 继承原始 finding_id（若有），保住 F-xxx 编号序列；否则报告里出现空编号行
            orig_id = f.get("finding_id") if isinstance(f, dict) else None
            out.append(
                Finding(
                    finding_id=str(orig_id) if orig_id else "",
                    type="UNKNOWN_RECORD",
                    title="审计记录数据格式异常（已降级展示）",
                    severity="low",
                    claim=str(f)[:300] or None,
                    method="read-path schema validation",
                    needs_human_review=True,
                    normal_explanation=(
                        "该条记录未通过 schema 校验，原始内容见「审计计算」列，请以人工复核为准"
                    ),
                ).model_dump()
            )
    return out


def make_finding(
    finding_type: str,
    *,
    title: str = "",
    severity: str | None = None,
    page: int | None = None,
    bbox: list[float] | None = None,
    claim: str | None = None,
    computed: str | None = None,
    tolerance: float | None = None,
    method: str = "",
    evidence_sources: list[dict[str, Any]] | None = None,
    normal_explanation: str = "",
    needs_human_review: bool = False,
) -> dict[str, Any]:
    """构造一条 Finding dict（未分配 finding_id，由编排层统一编号）。

    severity 缺省时取 FINDING_TYPES 注册表默认值——检测器不应自创 severity。
    """
    meta = FINDING_TYPES.get(finding_type)
    if meta is None:
        raise ValueError(f"未知 Finding 类型: {finding_type}")
    return Finding(
        type=finding_type,
        severity=severity or meta["severity"],
        title=title or meta["description"],
        page=page,
        bbox=bbox,
        claim=claim,
        computed=computed,
        tolerance=tolerance,
        method=method,
        evidence_sources=[EvidenceSource(**e) for e in (evidence_sources or [])],
        normal_explanation=normal_explanation,
        needs_human_review=needs_human_review,
    ).model_dump()


def assign_finding_ids(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """统一编号 F-001... 并按 severity(high>medium>low) 稳定排序。"""
    ordered = sorted(findings, key=lambda f: SEVERITY_ORDER.get(f.get("severity", "low"), 3))
    counter = itertools.count(1)
    for f in ordered:
        f["finding_id"] = f"F-{next(counter):03d}"
    return ordered
