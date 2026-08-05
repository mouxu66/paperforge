"""后台定时任务调度器。

功能：
- arXiv 定时拉取（子任务 6）：
  启动时根据 settings.arxiv_keywords_list 注册定时任务；
  每隔 settings.arxiv_auto_fetch_interval 小时执行一次：对每个关键词调用 search_arxiv，
  拉取最新 5 篇论文，未入库的自动导入；
  提供 run_fetch_once() 供手动触发（POST /api/admin/arxiv/fetch-now 调用）。
- Reflection 原论文补识别（P2）：
  每 10 分钟执行一次 retry_reflection_source_resolution(batch_size=50)，
  仅处理 source_paper_status='rate_limited' 的报告，避免每日全量扫描。

设计要点：
- 使用 apscheduler 的 BackgroundScheduler（后台线程，不阻塞主线程）
- 任务函数在独立 SessionLocal 中执行（Session 非线程安全）
- 关键词列表为空时不注册 arXiv 拉取任务，但补识别任务仍会注册
- 数据库异常时仅记录日志，不抛出（避免后台线程崩溃）
- shutdown() 在应用退出时优雅关闭调度器
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler  # noqa: F401

from .settings import get_settings

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


# ---------------------------------------------------------------------------
# 单次拉取实现
# ---------------------------------------------------------------------------
def run_fetch_once(keywords: list[str] | None = None) -> dict:
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

    @dataclass
    class _FetchDetail:
        """单次关键词拉取的计数器（替代 dict 算术，类型更安全）。"""

        keyword: str
        added: int = 0
        skipped: int = 0
        failed: int = 0

        def to_dict(self) -> dict[str, Any]:
            return {
                "keyword": self.keyword,
                "added": self.added,
                "skipped": self.skipped,
                "failed": self.failed,
            }

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

    kws = keywords if keywords is not None else get_settings().arxiv_keywords_list
    added_count = 0
    skipped_count = 0
    failed_count = 0
    details: list[dict[str, Any]] = []
    result: dict[str, Any] = {
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "keywords": list(kws),
        "added": added_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "details": details,
    }

    if not kws:
        logger.info("arXiv 定时拉取：未配置关键词，跳过")
        return result

    db = SessionLocal()
    try:
        for kw in kws:
            detail = _FetchDetail(keyword=kw)
            items: list[dict[str, Any]] = []
            try:
                items = search_arxiv(kw, max_results=get_settings().arxiv_auto_fetch_max_results)
            except Exception as e:  # noqa: BLE001 - 网络错误不应中断其他关键词
                logger.warning("arXiv 拉取关键词「%s」失败：%s", kw, e)
                detail.failed += len(items)
                failed_count += len(items)
                result["failed"] = failed_count
                result["details"].append(detail.to_dict())
                continue

            for paper in items:
                try:
                    # 通过 id 检查是否已入库（避免重复导入触发唯一约束错误）
                    existing = crud.get_paper(db, paper.get("id", ""))
                    if existing is not None:
                        detail.skipped += 1
                        skipped_count += 1
                        result["skipped"] = skipped_count
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
                        category=crud.normalize_category(
                            paper.get("category", "interdisciplinary")
                        ),
                        tags=paper.get("tags", []),
                    )
                    detail.added += 1
                    added_count += 1
                    result["added"] = added_count
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "arXiv 论文入库失败（kw=%s, id=%s）：%s",
                        kw,
                        paper.get("id", "?"),
                        e,
                    )
                    detail.failed += 1
                    failed_count += 1
                    result["failed"] = failed_count
            result["details"].append(detail.to_dict())
            logger.info(
                "arXiv 拉取「%s」完成：新增 %d 篇，跳过 %d 篇，失败 %d 篇",
                kw,
                detail.added,
                detail.skipped,
                detail.failed,
            )
    finally:
        db.close()

    return result


# ---------------------------------------------------------------------------
# 调度器生命周期
# ---------------------------------------------------------------------------
def start_scheduler() -> None:
    """启动后台定时任务调度器（应用启动时调用）。

    - 若已启动则直接返回（幂等）
    - 无论 arxiv_keywords_list 是否为空，都会注册 reflection 补识别任务
    - 仅当 arxiv_keywords_list 非空时才注册 arXiv 自动拉取任务
    - 使用 BackgroundScheduler + interval trigger
    """
    global _scheduler

    if _scheduler is not None:
        return  # 已启动

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning(
            "apscheduler 未安装，后台定时任务不可用。请运行 pip install apscheduler>=3.10.0"
        )
        return

    _scheduler = BackgroundScheduler(daemon=True)

    # ── P2：reflection 原论文补识别任务（每 10 分钟一次，轻量） ──
    # 延迟导入避免启动时循环依赖；任务函数内部自己管理 SessionLocal
    from .tasks import retry_reflection_source_resolution

    _scheduler.add_job(
        retry_reflection_source_resolution,
        "interval",
        minutes=10,
        id="retry_reflection_source_resolution",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        kwargs={"batch_size": 50},
    )
    logger.info("已注册 reflection 原论文补识别任务：每 10 分钟执行一次")

    # ── arXiv 定时拉取任务（可选，依赖关键词配置） ──
    if get_settings().arxiv_keywords_list:
        _scheduler.add_job(
            run_fetch_once,
            "interval",
            hours=get_settings().arxiv_auto_fetch_interval,
            id="arxiv_auto_fetch",
            replace_existing=True,
            # 错过执行窗口时跳过，避免重启后立即补跑大量历史任务
            max_instances=1,
            coalesce=True,
        )
        logger.info(
            "已注册 arXiv 定时拉取任务：每 %d 小时拉取一次，关键词=%s",
            get_settings().arxiv_auto_fetch_interval,
            get_settings().arxiv_keywords_list,
        )
    else:
        logger.info("arXiv 定时拉取任务未注册：未配置 get_settings().arxiv_keywords_list")

    try:
        _scheduler.start()
        logger.info("后台定时任务调度器已启动")
    except Exception as e:  # noqa: BLE001
        logger.error("后台定时任务调度器启动失败：%s", e)
        _scheduler = None


def is_scheduler_running() -> bool:
    """返回调度器是否已启动且处于运行状态。"""
    return _scheduler is not None and getattr(_scheduler, "running", False)


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
