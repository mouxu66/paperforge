"""图书馆统计、热点词与写作统计。"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Integer, func, text
from sqlalchemy.orm import Session

from ..models import (
    DepthReviewV4 as DepthReviewV4ORM,
)
from ..models import (
    HotspotConfig as HotspotConfigORM,
)
from ..models import (
    Paper as PaperORM,
)
from ..models import (
    WritingProject as WritingProjectORM,
)
from ..models import (
    WritingSnapshot as WritingSnapshotORM,
)
from ..schemas import DailyWordTrend, LibraryStats, WritingStatsResponse
from .writing import get_word_count


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 2) if values else 0.0


def _as_dict(v) -> dict:
    """安全地把 reflection_result(JSON 列) 归一为 dict。

    兼容历史/异常数据：None、dict、JSON 字符串（含二次编码）统一归一为 dict；
    无法解析则回退空 dict，避免对字符串调用 .get 触发 AttributeError 致 /api/stats 500。
    若字符串是合法 JSON 对象则 json.loads 还原，尽量不丢失已存评分。
    """
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
        except (json.JSONDecodeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def get_stats(db: Session) -> LibraryStats:
    rows = db.query(PaperORM).all()
    # 论文统计默认排除感悟报告，与前端「论文」分区口径保持一致
    paper_rows = [p for p in rows if p.category != "report"]
    total_chunks = sum(p.chunk_count for p in paper_rows)
    total_size = sum(p.index_size for p in paper_rows)
    counter = Counter(p.category for p in paper_rows)
    by_category = [{"category": k, "count": v} for k, v in counter.items()]
    source_counter = Counter(p.source for p in paper_rows)
    by_source = [{"source": k, "count": v} for k, v in source_counter.items()]

    report_count = sum(1 for p in rows if p.category == "report")

    # Aggregate reflection scores/fidelity from completed report reviews
    report_reviews = (
        db.query(DepthReviewV4ORM)
        .filter(DepthReviewV4ORM.kind == "report", DepthReviewV4ORM.status == "completed")
        .all()
    )
    avg_scores: list[float] = []
    fidelities: list[float] = []
    for review in report_reviews:
        result = _as_dict(review.reflection_result)
        analysis = _as_dict(result.get("analysis_v2"))
        # analysis_v2 是完整分析口径；LLM 故障记录可能仍处于历史
        # completed 状态，但其 average=None，不能进入统计。
        scores = analysis.get("scores") or result.get("scores") or {}
        average = (
            analysis.get("average")
            if analysis.get("average") is not None
            else scores.get("average")
        )
        llm_failed = analysis.get("llm_failed", result.get("llm_failed", False))
        llm_failed = isinstance(llm_failed, (bool, int, float)) and bool(llm_failed)
        if llm_failed:
            average = None
        if average is not None:
            avg_scores.append(float(average))
        fidelity = (
            analysis.get("fidelity")
            if analysis.get("fidelity") is not None
            else result.get("fidelity")
        )
        if not llm_failed and fidelity is not None:
            fidelities.append(float(fidelity))

    return LibraryStats(
        totalPapers=len(paper_rows),
        reportCount=report_count,
        reportAvgScore=_avg(avg_scores),
        reportAvgFidelity=_avg(fidelities),
        totalChunks=total_chunks,
        totalSize=total_size,
        byCategory=by_category,
        bySource=by_source,
    )


def get_qf_health(db: Session) -> dict[str, Any]:
    """DEPTH 图链路(QF)健康面板：按需聚合 coverage / qf_mean / qf_std / histogram。

    Rec2 KPI 说明：
    - coverage: 含图论文占比（论文级）。
    - figureCount: 原始 figure 簇总数，会被 detector 过度分割放大。
    - realFigureCount: 按 (paper_id, figure_number) 去重后的近似真实图数，
      用于抵消 detector 过度分割带来的分母污染。
    - captionedFigureCount: 成功关联上图注的真实图数量（去重后）。
    - captionCoverage: captionedFigureCount / realFigureCount，
      反映真实带图注图的覆盖，比 captioned/total_clusters 更可信。

    所有指标均从现有表实时聚合，不排定时任务（守硬约束 #1）。
    """
    from ..models import DepthReviewV4 as DepthReviewV4ORM
    from ..models import PaperFigure as PaperFigureORM

    total_papers = db.query(PaperORM).filter(PaperORM.category != "report").count()
    # 有至少一张图的论文数
    with_figures_stmt = (
        db.query(PaperFigureORM.paper_id).distinct().filter(PaperFigureORM.figure_path != "")
    )
    papers_with_figures = db.query(PaperORM).filter(PaperORM.id.in_(with_figures_stmt)).count()
    coverage = round(papers_with_figures / max(total_papers, 1), 4)

    # 从已完成论文审稿的 final_verdict JSON 中解析 figure_consistency_score
    reviews = (
        db.query(DepthReviewV4ORM)
        .filter(DepthReviewV4ORM.kind == "paper", DepthReviewV4ORM.status == "completed")
        .all()
    )
    scores: list[float] = []
    for r in reviews:
        verdict = _as_dict(r.final_verdict)
        # figure_consistency_score 直接挂在 final_verdict 顶层
        score = verdict.get("figure_consistency_score")
        if score is not None:
            try:
                scores.append(float(score))
            except (TypeError, ValueError):
                pass

    if scores:
        import statistics

        qf_mean = round(sum(scores) / len(scores), 4)
        qf_std = round(statistics.pstdev(scores), 4) if len(scores) > 1 else 0.0
    else:
        qf_mean = 0.0
        qf_std = 0.0

    # 简单 5 桶直方图：0.0-0.2, 0.2-0.4, ..., 0.8-1.0
    buckets = [0, 0, 0, 0, 0]
    for s in scores:
        idx = min(int(s * 5), 4)
        buckets[idx] += 1
    histogram = [
        {"range": "0.0-0.2", "count": buckets[0]},
        {"range": "0.2-0.4", "count": buckets[1]},
        {"range": "0.4-0.6", "count": buckets[2]},
        {"range": "0.6-0.8", "count": buckets[3]},
        {"range": "0.8-1.0", "count": buckets[4]},
    ]

    figure_count = db.query(PaperFigureORM).count()

    # Rec2/KPI: 用 figure_number 去重，抵消 detector 过度分割带来的分母污染。
    # real_figure_count  ≈ 论文中真实图的数量（按图号去重）
    # captioned_figure_count ≈ 成功关联上图注的真实图数量
    real_figure_count = (
        db.query(func.count())
        .select_from(
            db.query(PaperFigureORM.paper_id, PaperFigureORM.figure_number)
            .filter(PaperFigureORM.figure_number.is_not(None))
            .distinct()
            .subquery()
        )
        .scalar()
        or 0
    )
    captioned_figure_count = (
        db.query(func.count())
        .select_from(
            db.query(PaperFigureORM.paper_id, PaperFigureORM.figure_number)
            .filter(
                PaperFigureORM.figure_number.is_not(None),
                PaperFigureORM.caption_text.is_not(None),
                PaperFigureORM.caption_text != "",
            )
            .distinct()
            .subquery()
        )
        .scalar()
        or 0
    )
    caption_coverage = round(captioned_figure_count / max(real_figure_count, 1), 4)

    # M0 开关状态：矢量渲染兜底是否启用
    try:
        from ..settings import get_settings

        m0_active = get_settings().depth_vector_render_enabled
    except Exception:  # noqa: BLE001
        m0_active = True

    return {
        "totalPapers": total_papers,
        "papersWithFigures": papers_with_figures,
        "coverage": coverage,
        "qfMean": qf_mean,
        "qfStd": qf_std,
        "histogram": histogram,
        "m0Active": m0_active,
        "figureCount": figure_count,
        "realFigureCount": real_figure_count,
        "captionedFigureCount": captioned_figure_count,
        "captionCoverage": caption_coverage,
    }


def get_qf_timeline(db: Session) -> dict[str, Any]:
    """Rec2: 图注关联修复前后对比（时间轴）统计。

    同时计算旧 KPI（按 cluster 数）和新 KPI（按 figure_number 去重后的真实图数），
    返回全局指标和每篇论文的 before/after 明细。
    所有聚合尽量用 SQL 完成，避免 N+1。
    """
    from ..models import PaperFigure as PaperFigureORM

    # 1) 旧 KPI：所有 figure cluster 总数 vs 成功关联 caption 的 cluster 数
    total_clusters = db.query(PaperFigureORM).count()
    captioned_clusters = (
        db.query(PaperFigureORM)
        .filter(PaperFigureORM.caption_text.is_not(None), PaperFigureORM.caption_text != "")
        .count()
    )

    # 2) 新 KPI（Rec2）：按 (paper_id, figure_number) 去重后的真实图数
    real_figures_subq = (
        db.query(PaperFigureORM.paper_id, PaperFigureORM.figure_number)
        .filter(PaperFigureORM.figure_number.is_not(None))
        .distinct()
        .subquery()
    )
    real_figure_count = db.query(func.count()).select_from(real_figures_subq).scalar() or 0
    captioned_real_subq = (
        db.query(PaperFigureORM.paper_id, PaperFigureORM.figure_number)
        .filter(
            PaperFigureORM.figure_number.is_not(None),
            PaperFigureORM.caption_text.is_not(None),
            PaperFigureORM.caption_text != "",
        )
        .distinct()
        .subquery()
    )
    captioned_real_count = db.query(func.count()).select_from(captioned_real_subq).scalar() or 0

    coverage_before = round(captioned_clusters / max(total_clusters, 1), 4)
    coverage_after = round(captioned_real_count / max(real_figure_count, 1), 4)

    # 3) 每篇论文的 before/after 明细（单次聚合，避免 N+1）
    old_stats = {
        row.paper_id: (row.total, row.captioned)
        for row in db.query(
            PaperFigureORM.paper_id,
            func.count().label("total"),
            func.sum(func.cast(PaperFigureORM.caption_text != "", Integer)).label("captioned"),
        )
        .group_by(PaperFigureORM.paper_id)
        .all()
    }

    new_real_rows = db.execute(
        text(
            """
            SELECT paper_id,
                   COUNT(DISTINCT figure_number) AS total,
                   COUNT(DISTINCT CASE WHEN caption_text IS NOT NULL AND caption_text != ''
                                       THEN figure_number END) AS captioned
            FROM paper_figures
            WHERE figure_number IS NOT NULL
            GROUP BY paper_id
            """
        )
    ).all()
    new_stats = {row[0]: (row[1], row[2]) for row in new_real_rows}

    paper_breakdown: list[dict[str, Any]] = []
    for pid in set(old_stats.keys()) | set(new_stats.keys()):
        old_total, old_captioned = old_stats.get(pid, (0, 0))
        new_total, new_captioned = new_stats.get(pid, (0, 0))
        paper_breakdown.append(
            {
                "paperId": pid,
                "oldTotal": old_total,
                "oldCaptioned": old_captioned,
                "newTotal": new_total,
                "newCaptioned": new_captioned,
                "oldCoverage": round(old_captioned / max(old_total, 1), 4),
                "newCoverage": round(new_captioned / max(new_total, 1), 4),
            }
        )

    return {
        "oldKpiTotalClusters": total_clusters,
        "oldKpiCaptioned": captioned_clusters,
        "newKpiRealFigures": real_figure_count,
        "newKpiCaptionedReal": captioned_real_count,
        "coverageBefore": coverage_before,
        "coverageAfter": coverage_after,
        "aggregateDelta": round(coverage_after - coverage_before, 4),
        "paperBreakdown": paper_breakdown,
    }


def get_hotspot_keywords(db: Session, scope: str | None = None) -> list[str]:
    """按 scope 查询热点词表；未命中则回退到 is_default=True 的配置。"""
    if scope:
        cfg = (
            db.query(HotspotConfigORM)
            .filter(HotspotConfigORM.scope == scope)
            .order_by(HotspotConfigORM.updated_at.desc())
            .first()
        )
        if cfg:
            return list(cfg.keywords or [])
    cfg = (
        db.query(HotspotConfigORM)
        .filter(HotspotConfigORM.is_default == True)
        .order_by(HotspotConfigORM.updated_at.desc())
        .first()
    )
    return list(cfg.keywords or []) if cfg else []


def record_daily_snapshot(db: Session, project_id: int, word_count: int) -> None:
    """记录每日字数快照（每日仅记录一次，已存在则更新）。

    用于趋势图：今日首次打开项目时调用。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    existing = (
        db.query(WritingSnapshotORM)
        .filter(
            WritingSnapshotORM.project_id == project_id,
            WritingSnapshotORM.date == today,
        )
        .first()
    )
    if existing:
        existing.word_count = word_count
    else:
        db.add(
            WritingSnapshotORM(
                project_id=project_id,
                date=today,
                word_count=word_count,
            )
        )
    db.commit()


def get_writing_stats(db: Session) -> WritingStatsResponse:
    """获取写作统计：总项目数、总字数、今日新增、近 7 天趋势。

    - totalWords：所有项目当前字数合计
    - todayWords：今日快照合计 - 昨日快照合计（无昨日快照的项目按 0 计）
    - trend：近 7 天每日所有项目快照字数合计
    """
    projects = db.query(WritingProjectORM).all()
    total_projects = len(projects)

    # 总字数：累加所有项目所有章节字数
    total_words = 0
    for p in projects:
        wc = get_word_count(db, p.id)
        if wc:
            total_words += wc.total

    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    # 今日新增 = 今日快照合计 - 昨日快照合计
    today_sum = (
        db.query(func.coalesce(func.sum(WritingSnapshotORM.word_count), 0))
        .filter(WritingSnapshotORM.date == today)
        .scalar()
        or 0
    )
    yesterday_sum = (
        db.query(func.coalesce(func.sum(WritingSnapshotORM.word_count), 0))
        .filter(WritingSnapshotORM.date == yesterday)
        .scalar()
        or 0
    )
    today_words = max(0, today_sum - yesterday_sum)

    # 近 7 天趋势：每日所有项目快照合计
    trend: list[DailyWordTrend] = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        day_sum = (
            db.query(func.coalesce(func.sum(WritingSnapshotORM.word_count), 0))
            .filter(WritingSnapshotORM.date == d)
            .scalar()
            or 0
        )
        trend.append(DailyWordTrend(date=d, wordCount=day_sum))

    return WritingStatsResponse(
        totalProjects=total_projects,
        totalWords=total_words,
        todayWords=today_words,
        trend=trend,
    )
