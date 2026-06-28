"""arXiv 定时拉取调度器（子任务 6）。

功能：
- 启动时根据 config.ARXIV_AUTO_FETCH_KEYWORDS 注册定时任务
- 每隔 ARXIV_AUTO_FETCH_INTERVAL 小时执行一次：对每个关键词调用 search_arxiv，
  拉取最新 5 篇论文，未入库的自动导入
- 提供 run_fetch_once() 供手动触发（POST /api/admin/arxiv/fetch-now 调用）

设计要点：
- 使用 apscheduler 的 BackgroundScheduler（后台线程，不阻塞主线程）
- 任务函数在独立 SessionLocal 中执行（Session 非线程安全）
- 关键词列表为空时不启动调度器
- 数据库异常时仅记录日志，不抛出（避免后台线程崩溃）
- shutdown() 在应用退出时优雅关闭调度器
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from .config import (
    ARXIV_AUTO_FETCH_INTERVAL,
    ARXIV_AUTO_FETCH_KEYWORDS,
    ARXIV_AUTO_FETCH_MAX_RESULTS,
)

logger = logging.getLogger(__name__)

_scheduler: Optional["BackgroundScheduler"] = None  # type: ignore[name-defined]


# ---------------------------------------------------------------------------
# 单次拉取实现
# ---------------------------------------------------------------------------
def run_fetch_once(keywords: Optional[list[str]] = None) -> dict:
    """对每个关键词拉取最新论文并入库。

    Args:
        keywords: 自定义关键词列表；None 时使用 config 默认值。

    Returns:
        汇总结果，形如：
            {
                "fetched_at": "2026-06-26 15:00",
                "keywords": ["LoRA", "transformer"],
                "added": 3,           # 新入库数
                "skipped": 2,          # 已存在跳过数
                "failed": 1,           # 失败数
                "details": [
                    {"keyword": "LoRA", "added": 2, "skipped": 1, "failed": 0},
                    ...
                ],
            }
    """
    # 延迟导入，避免循环依赖与导入时副作用
    from . import crud
    from .arxiv_crawler import search_arxiv
    from .database import SessionLocal

    kws = keywords if keywords is not None else ARXIV_AUTO_FETCH_KEYWORDS
    result = {
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "keywords": list(kws),
        "added": 0,
        "skipped": 0,
        "failed": 0,
        "details": [],
    }

    if not kws:
        logger.info("arXiv 定时拉取：未配置关键词，跳过")
        return result

    db = SessionLocal()
    try:
        for kw in kws:
            detail = {"keyword": kw, "added": 0, "skipped": 0, "failed": 0}
            try:
                items = search_arxiv(kw, max_results=ARXIV_AUTO_FETCH_MAX_RESULTS)
            except Exception as e:  # noqa: BLE001 - 网络错误不应中断其他关键词
                logger.warning("arXiv 拉取关键词「%s」失败：%s", kw, e)
                detail["failed"] = len(items) if 'items' in locals() else 0
                result["details"].append(detail)
                result["failed"] += detail["failed"]
                continue

            for paper in items:
                try:
                    # 通过 id 检查是否已入库（避免重复导入触发唯一约束错误）
                    existing = crud.get_paper(db, paper.get("id", ""))
                    if existing is not None:
                        detail["skipped"] += 1
                        result["skipped"] += 1
                        continue
                    crud.import_external_paper(
                        db,
                        paper_id=paper["id"],
                        title=paper.get("title", ""),
                        authors=paper.get("authors", []),
                        year=paper.get("year", 0),
                        abstract=paper.get("abstract", ""),
                        pdf_url=paper.get("pdf_url", ""),
                        source=paper.get("source", "arxiv"),
                        category=paper.get("category", "arxiv"),
                        tags=paper.get("tags", []),
                    )
                    detail["added"] += 1
                    result["added"] += 1
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "arXiv 论文入库失败（kw=%s, id=%s）：%s",
                        kw, paper.get("id", "?"), e,
                    )
                    detail["failed"] += 1
                    result["failed"] += 1
            result["details"].append(detail)
            logger.info(
                "arXiv 拉取「%s」完成：新增 %d 篇，跳过 %d 篇，失败 %d 篇",
                kw, detail["added"], detail["skipped"], detail["failed"],
            )
    finally:
        db.close()

    return result


# ---------------------------------------------------------------------------
# 调度器生命周期
# ---------------------------------------------------------------------------
def start_scheduler() -> None:
    """启动定时拉取调度器（应用启动时调用）。

    - 若 ARXIV_AUTO_FETCH_KEYWORDS 为空，则跳过启动
    - 若已启动则直接返回（幂等）
    - 使用 BackgroundScheduler + interval trigger
    """
    global _scheduler

    if _scheduler is not None:
        return  # 已启动

    if not ARXIV_AUTO_FETCH_KEYWORDS:
        logger.info("arXiv 调度器未启动：未配置 ARXIV_AUTO_FETCH_KEYWORDS")
        return

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning(
            "apscheduler 未安装，arXiv 定时拉取功能不可用。"
            "请运行 pip install apscheduler>=3.10.0"
        )
        return

    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        run_fetch_once,
        "interval",
        hours=ARXIV_AUTO_FETCH_INTERVAL,
        id="arxiv_auto_fetch",
        replace_existing=True,
        # 错过执行窗口时跳过，避免重启后立即补跑大量历史任务
        max_instances=1,
        coalesce=True,
    )
    try:
        _scheduler.start()
        logger.info(
            "arXiv 调度器已启动：每 %d 小时拉取一次，关键词=%s",
            ARXIV_AUTO_FETCH_INTERVAL,
            ARXIV_AUTO_FETCH_KEYWORDS,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("arXiv 调度器启动失败：%s", e)
        _scheduler = None


def shutdown_scheduler() -> None:
    """关闭调度器（应用退出时调用，幂等）。"""
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
        logger.info("arXiv 调度器已关闭")
    except Exception as e:  # noqa: BLE001
        logger.warning("arXiv 调度器关闭异常：%s", e)
    finally:
        _scheduler = None
