"""实验审计主服务：audit pipeline 编排。

职责：
- 固定证据（PDF SHA-256）→ 结构解析（表格）→ 逐项检测 → 汇总入库。
- **fail-open**：单项检测异常只记入 checks_run，不中断整体审计。
- 同步执行（供 TaskManager worker 线程调用），本模块不做异步封装。
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import ExperimentAudit
from ..models import Paper as PaperORM
from . import (
    ablation,
    baseline,
    figure_reuse,
    figures,
    metrics,
    reproducibility,
    table_numbers,
    tables,
)
from .schemas import assign_finding_ids, coerce_findings

logger = logging.getLogger(__name__)


def _resolve_pdf_bytes(paper_id: str) -> bytes | None:
    """读取 uploads/ 下的论文 PDF 字节流；不存在返回 None。"""
    # 路径遍历防御：paper_id 参与文件路径拼接，必须是纯文件名（不带分隔符）。
    # 正常 id 由系统生成（UUID/arXiv id），此检查兜底未来可能从外部传入的调用方。
    if not paper_id or paper_id != Path(paper_id).name:
        logger.warning("非法 paper_id（含路径分隔符）: %r", paper_id)
        return None
    try:
        from ..utils.paths import _get_uploads_dir
    except ImportError:
        from ..pdf_parser import _get_uploads_dir

    pdf_path = _get_uploads_dir() / f"{paper_id}.pdf"
    if not pdf_path.exists():
        return None
    try:
        return pdf_path.read_bytes()
    except OSError as e:
        logger.warning("读取 PDF 失败 [%s]: %s", paper_id, e)
        return None


def _per_page_texts(pdf_bytes: bytes) -> list[str]:
    """PDF 逐页文本（1-based 索引：texts[i] 为第 i+1 页）。失败返回空。"""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001 - fail-open
        return []
    try:
        return [doc[i].get_text() for i in range(doc.page_count)]
    finally:
        doc.close()


class AuditService:
    """论文实验审计编排器（无状态，方法均为实例方法便于未来注入配置）。"""

    def run_paper_audit(
        self,
        db: Session,
        paper_id: str,
        checks: list[str] | None = None,
    ) -> ExperimentAudit:
        """执行一次完整审计并落库，返回审计记录。

        Args:
            db: 数据库会话
            paper_id: 论文 ID
            checks: 限定运行的检测项 key（None=全部已实现项）
        """
        paper = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
        audit = ExperimentAudit(paper_id=paper_id, status="running")
        db.add(audit)
        db.commit()

        checks_run: list[dict] = []
        findings: list[dict] = []

        def enabled(name: str) -> bool:
            return checks is None or name in checks

        try:
            if paper is None:
                raise ValueError(f"论文不存在: {paper_id}")

            pdf_bytes = self._timed(checks_run, "ingest_pdf", lambda: _resolve_pdf_bytes(paper_id))
            if pdf_bytes:
                audit.source_pdf_hash = hashlib.sha256(pdf_bytes).hexdigest()

            full_text = paper.full_text or ""
            if not full_text and not pdf_bytes:
                raise ValueError("论文无全文且本地无 PDF，无法审计")
            if not full_text and pdf_bytes:
                full_text = "\n".join(_per_page_texts(pdf_bytes))

            # ── 表格提取（P0-1/P0-8 的前置）──
            extracted: list[tables.ExtractedTable] = []
            if pdf_bytes and not tables.pymupdf_available():
                checks_run.append(
                    {
                        "check": "table_extraction",
                        "status": "skipped",
                        "reason": "PyMuPDF 未安装，表格提取不可用（数值类检测随之跳过）",
                    }
                )
            elif pdf_bytes:
                extracted = self._timed(
                    checks_run,
                    "table_extraction",
                    lambda: tables.extract_tables_from_pdf(pdf_bytes),
                )
            else:
                checks_run.append(
                    {
                        "check": "table_extraction",
                        "status": "skipped",
                        "reason": "本地无 PDF 文件，表格类检测跳过",
                    }
                )

            page_texts = _per_page_texts(pdf_bytes) if pdf_bytes else []

            # ── P0-1 正文-表格数字一致性 ──
            if extracted and enabled("P0-1_numeric_mismatch"):
                findings += self._timed_list(
                    checks_run,
                    "P0-1_numeric_mismatch",
                    lambda: metrics.check_numeric_claims_vs_tables(full_text, extracted),
                )
            else:
                checks_run.append(
                    {
                        "check": "P0-1_numeric_mismatch",
                        "status": "skipped",
                        "reason": "无可提取表格",
                    }
                )

            # ── P0-2 指标数学自洽性（逐页扫描以带页码）──
            if enabled("P0-2_metric_consistency"):
                findings += self._timed_list(
                    checks_run,
                    "P0-2_metric_consistency",
                    lambda: self._scan_metric_consistency(full_text, page_texts),
                )

            # ── P0-5 可复现性清单 ──
            if enabled("P0-5_reproducibility"):
                findings += self._timed_list(
                    checks_run,
                    "P0-5_reproducibility",
                    lambda: reproducibility.check_reproducibility_info(full_text),
                )

            # ── P0-3 Ablation 结论检查（依赖表格）──
            if extracted and enabled("P0-3_ablation"):
                findings += self._timed_list(
                    checks_run,
                    "P0-3_ablation",
                    lambda: ablation.check_ablation_consistency(full_text, extracted),
                )
            elif not extracted:
                checks_run.append(
                    {
                        "check": "P0-3_ablation",
                        "status": "skipped",
                        "reason": "无可提取表格",
                    }
                )

            # ── P0-6 Baseline 公平性（LLM 判定；不可用时内部跳过）──
            if enabled("P0-6_baseline_fairness"):
                findings += self._timed_list(
                    checks_run,
                    "P0-6_baseline_fairness",
                    lambda: baseline.check_baseline_fairness(full_text),
                )

            # ── P0-4 图表坐标轴审计（PaperFigure + OpenCV + VLM 兜底）──
            if enabled("P0-4_figure_axis"):
                if not figures.opencv_available():
                    checks_run.append(
                        {
                            "check": "P0-4_figure_axis.opencv",
                            "status": "skipped",
                            "reason": "OpenCV 未安装，断轴/子图尺度确定性检测跳过（axis_info 截断仍运行）",
                        }
                    )
                findings += self._timed_list(
                    checks_run,
                    "P0-4_figure_axis",
                    lambda: figures.check_figure_axis_risks(db, paper_id),
                )

            # ── P0-11 图内数值造假指纹（VLM 转写数值 → 统计指纹）──
            if enabled("P0-11_figure_numbers"):
                if not table_numbers.vision_available():
                    checks_run.append(
                        {
                            "check": "P0-11_figure_numbers",
                            "status": "skipped",
                            "reason": "vision_http_url 未配置，图内数值转写不可用",
                        }
                    )
                else:
                    findings += self._timed_list(
                        checks_run,
                        "P0-11_figure_numbers",
                        lambda: table_numbers.check_figure_number_patterns(db, paper_id),
                    )

            # ── P0-9 曲线/图片复用候选（pHash + SIFT/RANSAC）──
            if enabled("P0-9_figure_reuse"):
                if not figure_reuse.reuse_deps_available():
                    checks_run.append(
                        {
                            "check": "P0-9_figure_reuse",
                            "status": "skipped",
                            "reason": "cv2/imagehash/Pillow 缺失，图片复用检测跳过",
                        }
                    )
                else:
                    findings += self._timed_list(
                        checks_run,
                        "P0-9_figure_reuse",
                        lambda: figure_reuse.detect_figure_reuse(db, paper_id),
                    )

            # ── P0-8 标准差/显著性缺失 ──
            if extracted and enabled("P0-8_significance_missing"):
                findings += self._timed_list(
                    checks_run,
                    "P0-8_significance_missing",
                    lambda: tables.detect_significance_missing(extracted, full_text),
                )
            else:
                checks_run.append(
                    {
                        "check": "P0-8_significance_missing",
                        "status": "skipped",
                        "reason": "无可提取表格",
                    }
                )

            # 写库前再校验一次（防御检测器构造非法结构），脏数据不落库
            audit.findings = coerce_findings(assign_finding_ids(findings))
            audit.checks_run = checks_run
            audit.status = "completed"
            audit.completed_at = datetime.now()
            db.commit()
            return audit
        except Exception as e:  # noqa: BLE001 - 审计整体失败也要落库
            logger.exception("论文实验审计失败 [%s]", paper_id)
            # 先 rollback 清理事务状态：若失败源于 commit/flush 异常，
            # session 已被污染（PendingRollbackError），不回滚则失败态也写不进。
            try:
                db.rollback()
            except Exception:  # noqa: BLE001 - 连接彻底壤掉时放弃落库
                pass
            try:
                audit.status = "failed"
                audit.error_message = str(e)[:2000]
                audit.checks_run = checks_run
                audit.completed_at = datetime.now()
                db.merge(audit)  # rollback 后实例已 expired，merge 回新事务
                db.commit()
            except Exception:  # noqa: BLE001 - 失败态落库失败不拖垮 worker
                logger.exception("审计失败态落库也失败 [%s]", paper_id)
                try:
                    db.rollback()
                except Exception:  # noqa: BLE001
                    pass
            return audit

    # ── 内部工具 ────────────────────────────────────────────────
    @staticmethod
    def _scan_metric_consistency(full_text: str, page_texts: list[str]) -> list[dict]:
        """P0-2：优先逐页扫描（带页码），无分页文本时退回全文扫描。"""
        findings: list[dict] = []
        if page_texts:
            for page_no, text in enumerate(page_texts, start=1):
                for sent in metrics.split_sentences(text):
                    findings += metrics.check_sentence_metric_consistency(sent, page=page_no)
        else:
            for sent in metrics.split_sentences(full_text):
                findings += metrics.check_sentence_metric_consistency(sent)
        return findings

    @staticmethod
    def _timed(checks_run: list[dict], name: str, fn: Callable):
        """运行单项并记录耗时/失败（fail-open：异常返回 None 并标注 failed）。"""
        t0 = time.perf_counter()
        try:
            result = fn()
            checks_run.append(
                {
                    "check": name,
                    "status": "ok",
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                }
            )
            return result
        except Exception as e:  # noqa: BLE001 - 单项 fail-open
            logger.warning("检测项 %s 失败: %s", name, e, exc_info=True)
            checks_run.append(
                {
                    "check": name,
                    "status": "failed",
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                    "reason": str(e)[:500],
                }
            )
            return None

    def _timed_list(self, checks_run: list[dict], name: str, fn: Callable) -> list[dict]:
        """_timed 的 list 版：失败返回空列表，成功时附带 findings 计数。"""
        t0 = time.perf_counter()
        try:
            result = fn() or []
            checks_run.append(
                {
                    "check": name,
                    "status": "ok",
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                    "findings": len(result),
                }
            )
            return result
        except Exception as e:  # noqa: BLE001 - 单项 fail-open
            logger.warning("检测项 %s 失败: %s", name, e, exc_info=True)
            checks_run.append(
                {
                    "check": name,
                    "status": "failed",
                    "duration_ms": int((time.perf_counter() - t0) * 1000),
                    "reason": str(e)[:500],
                }
            )
            return []


def get_latest_audit(db: Session, paper_id: str) -> ExperimentAudit | None:
    """查询某论文最新一条审计记录。"""
    return (
        db.query(ExperimentAudit)
        .filter(ExperimentAudit.paper_id == paper_id)
        .order_by(ExperimentAudit.created_at.desc())
        .first()
    )
