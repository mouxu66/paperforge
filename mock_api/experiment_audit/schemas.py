"""实验审计 Finding 类型注册表与 Pydantic schema。

FINDING_TYPES 是 10 种 Finding 的唯一事实源：severity 默认值、中文描述、
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
    "STD_OR_SIGNIFICANCE_MISSING": {
        "severity": "medium",
        "description": "缺少标准差/置信区间/统计检验",
        "example": "Table 3 只有均值，无标准差，无法判断显著性",
        "check": "detect missing ± values, error bars, p-values",
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
