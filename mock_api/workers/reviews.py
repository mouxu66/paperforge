"""DEPTH review background workers.

包含：
- depth_review：单篇 DEPTH v4.1 审稿
- reflection_review：感悟/报告 reflection 审稿
- depth_batch：v3 DEPTH 批量评分
- v4_batch_review：v4.1 批量审稿
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
import time

logger = logging.getLogger(__name__)


def _get_batch_parallel() -> int:
    """批量评估并发数（从集中式 settings 读取）。"""
    from ..settings import get_settings

    return get_settings().effective_batch_parallel


def depth_review_worker(task_id: str, params: dict) -> None:
    """单篇 DEPTH v4.1 审稿 worker。"""
    from ..tasks import TaskManager

    paper_id = params.get("paperId", "")
    # [P2-3 COMPUTE_MODE_PASSTHROUGH]
    compute_mode = params.get("computeMode") or params.get("compute_mode") or "deep"
    compute_mode = str(compute_mode).strip().lower() or "deep"
    if not paper_id:
        TaskManager.fail(task_id, "缺少 paperId 参数")
        return

    TaskManager.update_progress(task_id, 10, f"DEPTH v4.1: {paper_id}")

    from ..depth_tasks import run_depth_review_sync

    try:
        record_id = run_depth_review_sync(paper_id, compute_mode=compute_mode)
        TaskManager.complete(task_id, {"recordId": record_id, "paperId": paper_id})
    except Exception as e:  # noqa: BLE001 - worker loop - 评审子任务单点失败需隔离，避免整 pipeline 失败
        TaskManager.fail(task_id, str(e))


def reflection_review_worker(task_id: str, params: dict) -> None:
    """感悟/报告 reflection 审稿 worker。"""
    from ..tasks import TaskManager

    paper_id = params.get("paperId", "")
    if not paper_id:
        TaskManager.fail(task_id, "缺少 paperId 参数")
        return

    TaskManager.update_progress(task_id, 10, f"DEPTH reflection: {paper_id}")

    from ..depth_tasks import run_depth_reflection_sync

    try:
        record_id = run_depth_reflection_sync(paper_id)
        TaskManager.complete(task_id, {"recordId": record_id, "paperId": paper_id})
    except Exception as e:  # noqa: BLE001 - worker loop - 评审子任务单点失败需隔离，避免整 pipeline 失败
        TaskManager.fail(task_id, str(e))


def depth_batch_worker(task_id: str, params: dict) -> None:
    """v3 DEPTH 批量评分 worker（并行评估 + 进度上报）。"""
    from ..depth_eval import evaluate_paper
    from ..tasks import TaskManager

    paper_ids = params.get("paperIds", [])
    if not paper_ids:
        TaskManager.fail(task_id, "paperIds 为空")
        return

    total = len(paper_ids)

    # 线程安全的结果收集
    ok_results: list[dict] = []
    err_results: list[dict] = []
    _results_lock = threading.Lock()
    completed_count = 0
    _count_lock = threading.Lock()

    def _eval_one(pid: str) -> dict | None:
        nonlocal completed_count
        try:
            r = evaluate_paper(pid)
            if r.get("error"):
                logger.warning("DEPTH v3 评估失败: paper=%s, error=%s", pid, r["error"])
                with _results_lock:
                    err_results.append({"paper_id": pid, "error": r["error"]})
                return None
            missing = [
                k
                for k in (
                    "final_score",
                    "novelty_score",
                    "rigor_score",
                    "influence_score",
                    "reproducibility_score",
                    "keywords",
                    "critique_points",
                    "type",
                )
                if k not in r or r[k] is None
            ]
            if missing:
                logger.warning("DEPTH v3 结果缺失字段: paper=%s, missing=%s", pid, missing)
            return r
        except Exception as e:  # noqa: BLE001 - worker loop - 评审子任务单点失败需隔离，避免整 pipeline 失败
            logger.exception("DEPTH v3 评估异常: paper=%s", pid)
            with _results_lock:
                err_results.append({"paper_id": pid, "error": str(e)})
            return None
        finally:
            with _count_lock:
                completed_count += 1
                TaskManager.update_progress(
                    task_id,
                    int(completed_count / total * 100),
                    f"{completed_count}/{total}",
                )

    max_workers = min(_get_batch_parallel(), total)
    logger.info("DEPTH v3 批量评估: %d 篇论文, 并行度=%d", total, max_workers)
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="depth-batch-",
    ) as pool:
        futures = {pool.submit(_eval_one, pid): pid for pid in paper_ids}
        for future in concurrent.futures.as_completed(futures):
            try:
                r = future.result()
                if r is not None:
                    with _results_lock:
                        ok_results.append(r)
            except Exception:  # noqa: BLE001 - worker loop - 评审子任务单点失败需隔离，避免整 pipeline 失败
                pass

    ok_results.sort(key=lambda x: x.get("final_score", 0), reverse=True)
    for idx, r in enumerate(ok_results):
        r["rank"] = idx + 1

    results = ok_results + err_results
    summary = {
        "total": total,
        "completed": len(ok_results),
        "failed": len(err_results),
        "highest_score": max((r.get("final_score", 0) for r in ok_results), default=0.0),
        "average_score": round(
            sum(r.get("final_score", 0) for r in ok_results) / max(len(ok_results), 1), 1
        ),
    }
    TaskManager.complete(task_id, {"results": results, "summary": summary, "total": total})


def citation_sentiment_worker(task_id: str, params: dict) -> None:
    """抽取目标论文被引情感的后台 worker。"""
    from ..crud.analysis import extract_citation_sentiments_for_target
    from ..database import SessionLocal
    from ..tasks import TaskManager

    paper_id = params.get("paperId", "")
    if not paper_id:
        TaskManager.fail(task_id, "缺少 paperId 参数")
        return

    TaskManager.update_progress(task_id, 0, f"抽取被引情感: {paper_id}")

    db = SessionLocal()
    try:

        def _progress(current: int, total: int) -> None:
            progress = 5 + int((current / max(total, 1)) * 90)
            TaskManager.update_progress(task_id, progress, f"被引情感: {current}/{total}")

        result = extract_citation_sentiments_for_target(db, paper_id, progress_callback=_progress)
        # 若没有任何候选产生上下文，回调可能未被触发，确保任务完成前进度为 100%
        final_message = (
            f"被引情感抽取完成: "
            f"processed={result.get('processed', 0)}, "
            f"saved={result.get('saved', 0)}, "
            f"failed={result.get('failed', 0)}"
        )
        TaskManager.update_progress(task_id, 100, final_message)
        TaskManager.complete(task_id, result)
    except Exception as e:  # noqa: BLE001 - worker loop - 单点失败需隔离
        TaskManager.fail(task_id, str(e))
    finally:
        db.close()


def v4_batch_review_worker(task_id: str, params: dict) -> None:
    """v4.1 批量审稿 worker。"""
    from ..database import SessionLocal
    from ..depth_tasks import run_depth_review_sync
    from ..tasks import TaskManager

    paper_ids = params.get("paperIds", [])
    if not paper_ids:
        TaskManager.fail(task_id, "没有待审论文")
        return

    total_ = len(paper_ids)
    succeeded = 0
    failed = 0
    failed_papers: list[dict] = []

    TaskManager.update_progress(task_id, 0, f"审稿中: 0/{total_}")

    # B2: 原硬编码 compute_mode="deep" 导致用户对 speed/fast 的选择在批量审稿中被忽略。
    compute_mode = params.get("computeMode") or params.get("compute_mode") or "deep"
    compute_mode = str(compute_mode).strip().lower() or "deep"

    for i, pid in enumerate(paper_ids):
        db2 = SessionLocal()
        try:
            run_depth_review_sync(pid, compute_mode=compute_mode)  # [P2-3 WORKERS_PASSTHROUGH]
            succeeded += 1
        except Exception as e:  # noqa: BLE001 - worker loop - 评审子任务单点失败需隔离，避免整 pipeline 失败
            failed += 1
            failed_papers.append({"paper_id": pid, "error": str(e)[:200]})
            logger.warning("批量审稿: 论文 %s 审稿失败: %s", pid, e)
        finally:
            db2.close()

        progress = int((i + 1) / total_ * 100)
        TaskManager.update_progress(
            task_id,
            progress,
            f"审稿中: {i + 1}/{total_} (成功 {succeeded}, 失败 {failed})",
        )

        # 避免 LLM 限流（最后一条无需等待）
        if i < total_ - 1:
            time.sleep(1)

    TaskManager.complete(
        task_id,
        {
            "total": total_,
            "succeeded": succeeded,
            "failed": failed,
            "failedPapers": failed_papers,
        },
    )
