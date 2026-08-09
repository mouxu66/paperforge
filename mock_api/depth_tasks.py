"""DEPTH 批量评估后台任务管理 + v4.2 单篇审稿任务。

使用 threading 在后台线程中逐篇执行 DEPTH 评估，支持进度查询与结果缓存。
v4.2：DAG 波次并行引擎 + QF 图文一致性 + 校准偏移层（PeerRead 198 篇实证）。

批量评估支持并行处理：通过 ThreadPoolExecutor 并发评估多篇论文，
并发数由 PAPERFORGE_BATCH_PARALLEL 环境变量控制（默认 min(4, cpu_count)）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime
from typing import Any

from .settings import get_settings

logger = logging.getLogger(__name__)


# 🛡️ P1-6 降级开关：若 DepthReviewInvalidError 频发（LLM 服务不稳定），
# 设 PAPERFORGE_DISABLE_INVALID_GATE=1 可瞬间切回旧逻辑（不抛异常，
# 返回 record_id，task 标记 completed），避免金丝雀环境被大量 failed 任务阻塞。
def _is_invalid_gate_disabled() -> bool:
    """动态检查降级开关是否启用（从集中式 settings 读取）。"""
    return get_settings().disable_invalid_gate


def _is_figure_trigger_disabled() -> bool:
    """批量/确定性评估时禁用自动派发 figure_understanding 任务。

    figure_understanding 会加载 Qwen3-VL，与 8080 上的 Qwen 在 8GB 显存上
    互斥；且 worker 与主进程同进程运行，OCR 模型加载一旦 segfault 会直接拖垮
    整个审稿进程。故批量 DEPTH 跑须设 PAPERFORGE_DISABLE_FIGURE_TRIGGER=1，
    figure 抽取作为独立小批量 pass，而非在 bulk 审稿里自动触发。
    """
    return os.environ.get("PAPERFORGE_DISABLE_FIGURE_TRIGGER") == "1"


class DepthReviewInvalidError(Exception):
    """深度审阅产出无效结果（证据池空 + 评分全默认），应走 failed 终态而非 completed。

    🛡️ P1-6 修复：原版 run_depth_review_sync 在无效闸口触发时 return record_id，
    外层 worker 无条件调 TM.complete() → tasks.status='completed' 但
    DepthReviewV4.status='failed'，前端显示"完成"实际失败。改为抛此异常，
    worker 的 except 子句会调 TM.fail()，统一终态。
    """


def _get_batch_parallel() -> int:
    """批量评估并发数（从集中式 settings 读取）。"""
    return get_settings().effective_batch_parallel


def submit_depth_job(paper_ids: list[str]) -> str:
    """提交批量 DEPTH 评估任务（通过 TaskManager 持久化执行）。

    统一走 ``workers.get_worker("depth_batch")`` 注册表，避免与
    ``/api/task`` 通用任务入口的 ``depth_batch_worker`` 形成双真相源。
    """
    from . import tasks as task_manager
    from .workers import get_worker

    worker = get_worker("depth_batch")
    if worker is None:
        raise RuntimeError("depth_batch worker 未注册")

    task_id = task_manager.TaskManager.submit(
        "depth_batch",
        params={"paperIds": paper_ids},
        worker_fn=worker,
    )
    return task_id


def get_job_status(task_id: str) -> dict[str, Any] | None:
    """查询批量 DEPTH 评估任务状态（委托到 TaskManager）。"""
    from . import tasks as task_manager

    result = task_manager.TaskManager.get(task_id)
    if result is None:
        return None
    result_data = result.get("result") or {}
    return {
        "task_id": result["id"],
        "status": result["status"],
        "progress": {
            "current": result["progress"],
            "total": result_data.get("total", 100),
        },
        "results": result_data.get("results"),
        "errors": [],
        "summary": result_data.get("summary"),
    }


# ===========================================================================
# DEPTH v4.2 单篇审稿任务（异步 + 数据库持久化）
# ===========================================================================


def run_depth_review_sync(
    paper_id: str,
    compute_mode: str | None = None,  # [P2-3 DTASKS_PASSTHROUGH]
) -> str:
    """同步执行 DEPTH v4.2 审稿，结果写入 depth_reviews_v4 表。

    流程：
    1. 从数据库获取论文的 full_text / title / abstract
    2. 创建 DepthReviewV4 记录（status=running）
    3. 调用 segment_paper_text + DepthReviewer.review() 执行审稿
    4. 更新记录：填充所有节点结果和 final_verdict，status=completed
    5. 异常时 status=failed，记录 error_message

    Returns:
        记录 ID（UUID）。
    """
    from .database import SessionLocal
    from .depth_eval_v4 import DepthReviewer
    from .models import DepthReviewV4, Paper

    db = SessionLocal()
    try:
        # 1. 获取论文
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if not paper:
            raise ValueError(f"论文 {paper_id} 不存在")
        if not paper.full_text:
            raise ValueError(f"论文 {paper_id} 尚无全文（full_text 为空），无法执行深度审稿")

        # 1.5 构造分档偏移上下文（source/year），供 correct_final_score 按语料自适应
        paper_meta = {
            "source": getattr(paper, "source", None),
            "year": getattr(paper, "year", None),
        }

        # 2. 创建审稿记录（标记为 v4.2）
        resolved_mode = (compute_mode or "deep").strip().lower()
        record = DepthReviewV4(
            paper_id=paper_id,
            version="v4.2",  # 数据库版本号保持 v4.2（与 routers/depth.py 查询一致）
            status="running",
            compute_mode=resolved_mode,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        record_id = record.id

        # 3. 执行审稿（优先 DAG 引擎，不支持时回退串行）
        reviewer = DepthReviewer(compute_mode=resolved_mode)
        try:
            result = asyncio.run(
                reviewer.review_async_dag(
                    paper_id=paper_id,
                    title=paper.title or "",
                    full_text=paper.full_text or "",
                    abstract=paper.abstract or "",
                    paper_meta=paper_meta,
                )
            )
        except Exception:
            logger.warning(
                "DAG 引擎执行失败，回退串行 review(): paper=%s",
                paper_id,
                exc_info=True,
            )
            result = reviewer.review(
                paper_id=paper_id,
                title=paper.title or "",
                full_text=paper.full_text or "",
                abstract=paper.abstract or "",
                paper_meta=paper_meta,
            )

        # 4. 序列化节点结果并更新记录
        # 将 Pydantic 模型转为 dict（CritiquePoint → dict）
        result_dict = result.model_dump()

        record.q0_result = {
            "has_substance": result_dict.get("has_substance"),
            "expectation": result_dict.get("expectation"),
            "reasoning": result_dict.get("q0_reasoning"),
            "evidence": result_dict.get("q0_evidence"),
        }
        record.q1_result = {
            "type": result_dict.get("paper_type"),
            "secondary_type": result_dict.get("secondary_type"),
            "confidence": result_dict.get("confidence"),
            "reasoning": result_dict.get("q1_reasoning"),
        }
        record.evidence_pool = result_dict.get("evidence_pool")
        record.q2_result = {
            "novelty_score": result_dict.get("novelty_score"),
            "hotspot_alignment_score": result_dict.get("hotspot_alignment_score"),
            "core_contribution": result_dict.get("core_contribution"),
            "reasoning": result_dict.get("q2_reasoning"),
            # B5: evidence_id 应存真实证据ID字符串（如 "E3"），而非校验布尔值。
            # evidence_checks["Q2"] 存的是 q2.verified（bool），evidence_ids["Q2"] 才是真实ID。
            "evidence_id": result_dict.get("evidence_ids", {}).get("Q2", ""),
            "evidence_verified": result_dict.get("evidence_checks", {}).get("Q2", False),
        }
        record.q3_result = {
            "rigor_score": result_dict.get("rigor_score"),
            "missing_items": result_dict.get("missing_items"),
            "reasoning": result_dict.get("q3_reasoning"),
        }
        record.q4_result = {
            "influence_score": result_dict.get("influence_score"),
            "reproducibility_score": result_dict.get("reproducibility_score"),
            "reasoning": result_dict.get("q4_reasoning"),
        }
        record.q5a_result = {
            "critique_points": [
                {"point": cp["point"], "severity": cp["severity"]}
                for cp in result_dict.get("critique_points", [])
            ],
        }
        record.q5b_result = {"defense_points": result_dict.get("defense_points", [])}
        record.q5c_result = {
            "reasoning": result_dict.get("chair_reasoning"),
            "calibrated_score": result_dict.get("calibrated_score"),
            "delta": result_dict.get("delta"),
            "delta_missing": result_dict.get("delta_missing"),
            "llm_verdict": result_dict.get("llm_verdict"),
        }
        record.final_verdict = {
            "final_verdict": result_dict.get("final_verdict"),
            "calibrated_score": result_dict.get("calibrated_score"),
            # 偏移前原始校准分（base_score + delta），供审计/双口径对比
            "calibrated_score_raw": round(
                float(result_dict.get("base_score") or 0.0)
                + float(result_dict.get("delta") or 0.0),
                4,
            ),
            "offset_applied": abs(
                float(result_dict.get("calibrated_score") or 0.0)
                - round(
                    float(result_dict.get("base_score") or 0.0)
                    + float(result_dict.get("delta") or 0.0),
                    4,
                )
            )
            > 1e-4,
            "override_reason": result_dict.get("override_reason"),
            "llm_verdict": result_dict.get("llm_verdict"),
            "base_score": result_dict.get("base_score"),
            "weights": result_dict.get("weights"),
            "evidence_checks": result_dict.get("evidence_checks"),
            "node_score_stds": result_dict.get("node_score_stds", {}),
            # v4.2 QF 图文一致性（无新增列，随 final_verdict JSON 持久化）
            "figure_consistency_score": result_dict.get("figure_consistency_score"),
            "figure_flags": result_dict.get("figure_flags", []),
            "figure_evidence_count": result_dict.get("figure_evidence_count", 0),
            "figure_coverage": result_dict.get("figure_coverage", "disabled"),
            "qf_reasoning": result_dict.get("qf_reasoning", ""),
        }
        # ── ADR-014 可复核性字段：评分如何产生、把握多大、可复现参数 ──
        # 这些字段来自 DepthV4Result（仅当对应 env 门控开启时非空），
        # 原样透传到 final_verdict JSON，前端「评审依据」面板展示。
        record.final_verdict["score_uncertainty"] = result_dict.get("score_uncertainty", {}) or {}
        record.final_verdict["llm_params_snapshot"] = (
            result_dict.get("llm_params_snapshot", {}) or {}
        )
        record.final_verdict["citation_integrity"] = result_dict.get("citation_integrity", {}) or {}
        # ── 有效性闸口：防止 LLM 超时/空响应被静默标为 completed ──
        # 若证据池为空 + critique_points 为空 → 整个流水线未真正执行（LLM 全超时/返回空）
        # 此时标 failed 而非 completed，避免前端拿到空评审且无失败信号。
        # B1: fast 模式是合成 neutral review（_fast_result 跳过全部 LLM），
        # 其 evidence_pool=[] + critique_points=[] + 全 0.5 是预期行为，不应判为无效。
        evidence_pool_raw = result_dict.get("evidence_pool") or []
        critique_points_raw = result_dict.get("critique_points") or []
        all_scores = [
            result_dict.get("novelty_score"),
            result_dict.get("rigor_score"),
            result_dict.get("influence_score"),
            result_dict.get("reproducibility_score"),
            result_dict.get("calibrated_score"),
        ]
        all_default = all(v == 0.5 for v in all_scores if v is not None)

        is_fast_mode = resolved_mode == "fast"

        if not is_fast_mode and not evidence_pool_raw and not critique_points_raw and all_default:
            record.status = "failed"
            record.error_message = (
                "深度审阅未产出有效结果（证据池为空且所有评分均为默认值 0.5），"
                "可能原因：LLM 超时或返回空响应。请重试或检查 LLM 服务状态。"
            )
            record.completed_at = datetime.now()
            db.commit()
            logger.warning(
                "DEPTH v4.2 审阅无效（静默失败闸口触发）: paper=%s, record=%s",
                paper_id,
                record_id,
            )
            # 🛡️ P1-6 修复：抛异常让 worker 走 TM.fail()，统一终态（避免前端显示"完成"实际失败）
            # 🛡️ 降级开关：PAPERFORGE_DISABLE_INVALID_GATE=1 时切回旧逻辑（return record_id）
            if _is_invalid_gate_disabled():
                logger.warning(
                    "DEPTH v4.2 审阅无效但降级开关已启用（PAPERFORGE_DISABLE_INVALID_GATE=1），"
                    "回退旧逻辑：返回 record_id，task 标记 completed。paper=%s",
                    paper_id,
                )
                return record_id
            raise DepthReviewInvalidError(record.error_message)

        # ── ADR-014 P8：双模型交叉复核（仅 PAPERFORGE_SECOND_OPINION=1 且
        #    配置了第二模型时执行；全程 fail-open，绝不改变主评审结论）──
        # 放在有效性闸口之后：被闸口判死的评审（证据池空 + 全默认分）不值得
        # 再花一次云端 API 调用做复核。
        try:
            from .second_opinion import run_second_opinion

            _so = run_second_opinion(
                (paper.abstract or "") + "\n\n" + (paper.full_text or ""),
                primary_score=result_dict.get("calibrated_score"),
                primary_verdict=result_dict.get("final_verdict"),
                kind="paper",
                db=db,  # 复用调用方 session，避免独立 session 的 close() 副作用（见 find_second_provider）
            )
            if _so.get("enabled"):
                record.final_verdict["cross_check"] = _so
        except Exception as _so_err:  # noqa: BLE001 - 第二评审异常不影响主评审
            logger.warning("DEPTH v4.2 双模型复核失败（非致命）: %s", _so_err)

        record.status = "completed"
        record.completed_at = datetime.now()
        db.commit()

        # v4.2: if figure coverage is missing, kick off a background
        # figure-understanding pipeline so the next review can use it.
        # 🛡️ 批量/确定性评估时禁用（OCR 同进程加载会 segfault 拖垮进程）：
        #   设 PAPERFORGE_DISABLE_FIGURE_TRIGGER=1 跳过，figure 作独立 pass。
        if result_dict.get("figure_coverage") == "missing" and not _is_figure_trigger_disabled():
            try:
                from .workers.figure_understanding import submit_figure_understanding_task

                submit_figure_understanding_task(paper_id)
            except Exception as exc:  # noqa: BLE001 - background scheduling must not fail review
                logger.warning(
                    "DEPTH v4.2 failed to schedule figure understanding for paper=%s: %s",
                    paper_id,
                    exc,
                )

        logger.info(
            "DEPTH v4.2 审稿完成: paper=%s, record=%s, verdict=%s",
            paper_id,
            record_id,
            result_dict.get("final_verdict"),
        )
        return record_id

    except Exception as e:
        logger.exception("DEPTH v4.2 审稿失败: paper=%s", paper_id)
        # 尝试更新记录状态为 failed
        try:
            record = (
                db.query(DepthReviewV4)  # type: ignore[assignment]
                .filter(
                    DepthReviewV4.paper_id == paper_id,
                    DepthReviewV4.status == "running",
                )
                .order_by(DepthReviewV4.created_at.desc())
                .first()
            )
            if record:
                record.status = "failed"
                record.error_message = str(e)
                record.completed_at = datetime.now()
                db.commit()
        except Exception:
            db.rollback()
        raise
    finally:
        db.close()


def run_depth_review_async(paper_id: str) -> None:
    """在后台线程中启动 DEPTH v4.2 审稿（不阻塞 API 响应）。

    线程内调用 run_depth_review_sync，异常仅记录日志不抛出。
    """

    def _worker():
        try:
            run_depth_review_sync(paper_id)
        except Exception:
            logger.exception("DEPTH v4.2 后台审稿异常: paper=%s", paper_id)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    logger.info("DEPTH v4.2 后台审稿已启动: paper=%s", paper_id)


# ===========================================================================
# DEPTH reflection 单篇报告评审（轻量 pipeline + 硬编码校验）
# ===========================================================================


def run_depth_reflection_sync(paper_id: str) -> str:
    """同步执行 DEPTH reflection 报告评审，结果写入 depth_reviews_v4.reflection_result 列。

    与 v4.2 论文审稿的差异：
    - 写入 kind='report' 行（与 v4.2 论文同表不同 kind）
    - 使用单 LLM 调用 + 硬编码校验（详见 depth_eval_reflection.ReflectionReviewer）
    - 不填充 q*~final_verdict 列（这些是论文类字段，保持 NULL）
    - 填充 reflection_result JSON 列（4 维评分 + 内嵌证据池 + summary + verdict）

    Returns:
        记录 ID（UUID）。
    """
    from .database import SessionLocal
    from .depth_eval_reflection import ReflectionReviewer
    from .models import DepthReviewV4, Paper

    db = SessionLocal()
    try:
        # 1. 获取论文
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
        if not paper:
            raise ValueError(f"报告 {paper_id} 不存在")
        if not paper.full_text:
            raise ValueError(
                f"报告 {paper_id} 尚无全文（full_text 为空），无法执行 reflection 评审"
            )

        # 2. 创建评审记录（kind='report'，version='reflection.v1' 与 v4.2 区别）
        record = DepthReviewV4(
            paper_id=paper_id,
            kind="report",
            version="reflection.v1",
            status="running",
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        record_id = record.id

        # 3. 执行评审（绑定原论文时注入论文参考，供千问对照核验理解准确性）
        reviewer = ReflectionReviewer()
        src_full = ""
        if paper.source_paper_id:
            try:
                from .reflection_pipeline import _get_paper_text_emb

                src_full, _ = _get_paper_text_emb(db, paper.source_paper_id)
            except Exception:  # noqa: BLE001 - 论文参考注入失败不阻塞评审
                src_full = ""
        result = reviewer.review(
            paper_id=paper_id,
            title=paper.title or "",
            full_text=paper.full_text or "",
            paper_text=src_full,
        )

        # 4. 写入 reflection_result JSON 列；论文专属列保持 NULL
        # 【Layer C】解析失败或任一次 LLM 调用空返回时改为 status='failed'，
        # 让 SSE 发出终结事件，前端不再把系统故障当作正常低分结果。
        # MagicMock/旧替身可能没有这些字段，因此只接受真实 bool/int。
        parse_failed = getattr(result, "parse_failed", False) is True
        llm_empty = getattr(result, "llm_empty", 0)
        llm_empty_failed = isinstance(llm_empty, (bool, int, float)) and bool(llm_empty)
        if parse_failed or llm_empty_failed:
            # 即使评审失败也持久化最小诊断结果。否则诚信报告/列表只能看到
            # reflection_result=NULL，无法区分「系统故障」与「报告证据不足」，
            # 还可能把失败记录误显示成普通 needs_evidence。
            failed_result = result.model_dump(exclude={"node_logs"})
            if not isinstance(failed_result, dict):
                failed_result = {}
            failed_result["llm_failed"] = True
            failed_result["verdict"] = "llm_failed"
            failed_result["scores"] = {}
            record.reflection_result = failed_result
            record.status = "failed"
            record.error_message = (
                "JSON 解析失败：LLM 输出了非空但无法解析为合法 JSON，reflection 评审无有效结果."
                if parse_failed
                else "LLM 调用返回空/超时，reflection 评审无可信结果；请检查模型服务后重试。"
            )
            record.completed_at = datetime.now()
            db.commit()
            # 🛡️ P1-6 修复：抛异常让 worker 走 TM.fail()，统一终态
            # 🛡️ 降级开关：PAPERFORGE_DISABLE_INVALID_GATE=1 时切回旧逻辑
            if _is_invalid_gate_disabled():
                logger.warning(
                    "DEPTH reflection 评审无效但降级开关已启用 "
                    "（PAPERFORGE_DISABLE_INVALID_GATE=1），"
                    "回退旧逻辑：返回 record_id。paper=%s",
                    paper_id,
                )
                return record_id
            raise DepthReviewInvalidError(record.error_message)
        else:
            record.reflection_result = result.model_dump(exclude={"node_logs"})
            # ── 完整修复：若报告有 docx 文件路径 + source_paper_id，调 analyze_reflection_file ──
            # analyze_reflection_file 负责：四段解析 + 绑定 + fidelity + 5 维融合
            if paper.reflection_docx_path and paper.source_paper_id:
                try:
                    from pathlib import Path as _Path

                    from .reflection_pipeline import analyze_reflection_file

                    docx_path = paper.reflection_docx_path
                    if _Path(docx_path).exists():
                        ana = analyze_reflection_file(docx_path, db, paper.source_paper_id)
                        rr = record.reflection_result or {}
                        # 用 analyze_reflection_file 的 5 维 scores 覆盖 4 维
                        # （保留 average 键以防前端回退读取）
                        rr["scores"] = {
                            **ana.get("scores", {}),
                            "average": ana.get("average"),
                        }
                        rr["verdict"] = ana.get("verdict", rr.get("verdict"))
                        rr["fidelity"] = ana.get("fidelity")
                        rr["fidelity_status"] = ana.get("fidelity_status")
                        rr["fidelity_anchors"] = ana.get("fidelity_anchors")
                        rr["fidelity_stray_claims"] = ana.get("stray_claims")
                        # analysis_v2 同时保留 6 维分数与诊断字段，避免只写
                        # average 后丢失 llm_failed/evidence/truncated 等可信度信息。
                        rr["analysis_v2"] = {
                            **ana,
                            **ana.get("scores", {}),
                            "average": ana.get("average"),
                        }
                        rr["bound_paper_id"] = ana.get("bound_paper_id")
                        record.reflection_result = rr
                        logger.info(
                            "reflection 完整分析完成: paper=%s, source=%s, "
                            "fidelity=%.3f, avg_5d=%.3f",
                            paper_id,
                            paper.source_paper_id,
                            ana.get("fidelity", 0),
                            ana.get("average", 0),
                        )
                except Exception as fidelity_err:
                    logger.warning(
                        "reflection 完整分析失败（非致命）: paper=%s, source=%s, docx=%s, err=%s",
                        paper_id,
                        paper.source_paper_id,
                        paper.reflection_docx_path,
                        fidelity_err,
                    )
            # ── 降级：仅有 source_paper_id 但没有 docx 路径时，走轻量 fidelity ──
            elif paper.source_paper_id:
                try:
                    from .reflection_fidelity import FIDELITY_FAIL, compute_fidelity
                    from .reflection_pipeline import _get_paper_text_emb

                    src_full, src_emb = _get_paper_text_emb(db, paper.source_paper_id)
                    if src_full:
                        fid = compute_fidelity(
                            {"q": paper.full_text or ""},
                            src_full,
                            src_emb,
                        )
                        rr = record.reflection_result or {}
                        rr["fidelity"] = fid.fidelity
                        rr["fidelity_status"] = fid.status
                        rr["fidelity_anchors"] = fid.anchors
                        rr["fidelity_stray_claims"] = fid.stray_claims
                        scores_4 = rr.get("scores", {}) or {}
                        rr["analysis_v2"] = {
                            "understanding_accuracy": scores_4.get("understanding_accuracy"),
                            "analysis_depth": scores_4.get("analysis_depth"),
                            "innovative_insights": scores_4.get("innovative_insights"),
                            "evidence_support": scores_4.get("evidence_support"),
                            "fidelity": fid.fidelity,
                            "average": round(
                                sum(
                                    [
                                        scores_4.get("understanding_accuracy", 0) or 0,
                                        scores_4.get("analysis_depth", 0) or 0,
                                        scores_4.get("innovative_insights", 0) or 0,
                                        scores_4.get("evidence_support", 0) or 0,
                                        fid.fidelity or 0,
                                    ]
                                )
                                / 5,
                                4,
                            ),
                        }
                        if fid.fidelity is not None and fid.fidelity < FIDELITY_FAIL:
                            rr["verdict"] = "rewrite_required"
                        record.reflection_result = rr
                except Exception as fidelity_err:
                    logger.warning(
                        "reflection fidelity 计算失败（非致命）: paper=%s, source=%s, err=%s",
                        paper_id,
                        paper.source_paper_id,
                        fidelity_err,
                    )
            # ── ADR-014 可复核性：LLM 参数快照 + 双模型交叉复核（fail-open）──
            rr = record.reflection_result or {}
            try:
                from .llm.reproducibility import get_llm_params_snapshot

                rr.setdefault("llm_params_snapshot", get_llm_params_snapshot())
            except Exception as _snap_err:  # noqa: BLE001 - 快照仅为存档
                logger.warning("reflection LLM 参数快照失败（非致命）: %s", _snap_err)
            try:
                from .second_opinion import run_second_opinion

                # 显式 is not None 判断：analysis_v2.average 优先，缺失才回退顶层 average。
                # 不能用 or 链——average=0.0 是合法分值，会被 or 误吞成 None 导致
                # 第二评审缺失 primary_score，分歧判定退化。
                _avg = (rr.get("analysis_v2") or {}).get("average")
                if _avg is None:
                    _avg = rr.get("average")
                _so = run_second_opinion(
                    paper.full_text or "",
                    primary_score=_avg,
                    primary_verdict=rr.get("verdict"),
                    kind="report",
                    db=db,  # 复用调用方 session（同上）
                )
                if _so.get("enabled"):
                    rr["cross_check"] = _so
            except Exception as _so_err:  # noqa: BLE001 - 第二评审异常不影响主评审
                logger.warning("reflection 双模型复核失败（非致命）: %s", _so_err)
            if rr is not record.reflection_result:
                record.reflection_result = rr
            record.status = "completed"
        record.completed_at = datetime.now()
        db.commit()

        logger.info(
            "DEPTH reflection 评审完成: paper=%s, record=%s, verdict=%s, "
            "evidence_count=%d, average=%.3f",
            paper_id,
            record_id,
            result.verdict,
            result.effective_evidence_count,
            result.scores.get("average", 0),
        )
        return record_id

    except Exception as e:
        logger.exception("DEPTH reflection 评审失败: paper=%s", paper_id)
        # 尝试更新记录状态为 failed
        try:
            record = (
                db.query(DepthReviewV4)  # type: ignore[assignment]
                .filter(
                    DepthReviewV4.paper_id == paper_id,
                    DepthReviewV4.kind == "report",
                    DepthReviewV4.status == "running",
                )
                .order_by(DepthReviewV4.created_at.desc())
                .first()
            )
            if record:
                record.status = "failed"
                record.error_message = str(e)
                record.completed_at = datetime.now()
                db.commit()
        except Exception:
            db.rollback()
        raise
    finally:
        db.close()


def run_depth_reflection_async(paper_id: str) -> None:
    """在后台线程中启动 DEPTH reflection 评审（不阻塞 API 响应）。

    线程内调用 run_depth_reflection_sync，异常仅记录日志不抛出。
    """

    def _worker():
        try:
            run_depth_reflection_sync(paper_id)
        except Exception:
            logger.exception("DEPTH reflection 后台评审异常: paper=%s", paper_id)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    logger.info("DEPTH reflection 后台评审已启动: paper=%s", paper_id)
