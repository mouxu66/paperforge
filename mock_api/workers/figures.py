"""Figure extraction worker.

负责把 PDF 上传后的 figure 抽取任务放到后台线程执行，
避免阻塞上传主流程。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from ..llm.figure_qwen import ask_qwen
from ..utils.tempfiles import safe_remove, safe_write  # noqa: F401 - v4.2 tempfile safety

logger = logging.getLogger(__name__)


# Per-figure Qwen call timeout (seconds).  Keep it bounded so one slow figure
# does not hold the whole worker task forever.
_QWEN_TIMEOUT = 60

# OCR 噪声检测：兜底 qwen_summary 前过滤明显乱码，避免把噪声当信号喂给 QF LLM。
# 诊断依据：OCR 模型在处理学术图表（attention heat maps、轴刻度等）时可能产生
# 训练数据残留幻觉（如通用爬虫语料中的新闻文本片段、实体占位符等）。这些噪声
# 会让 LLM 误判"图文严重不一致"→ 误减分。
_NOISE_HALLUCINATION_MARKERS = (
    "example.com",  # 通用 URL 幻觉（模型训练数据残留）
    "X_UNK",  # 未识别 token 占位符
    "_UNK",  # 通用未知 token 标记
    "<unk>",  # 未知 token 标记（llama/bert 词表）
    "http://",  # 裸 HTTP URL 片段（新闻/网页语料残留）
    "arxiv.org",  # 外部链接幻觉
)
# 纯轴刻度/数值碎片模式（如 "0% 10% 20% ..."、"Method³ Acc %"）
_AXIS_NOISE_PATTERNS = [
    re.compile(r"^\s*[\d.,%]+\s*[\d.,%]+\s*[\d.,%]+", re.M),  # 连续数值
    re.compile(r"\\multicolumn|\\cite|\\ref", re.M),  # LaTeX 残渣
]
_MIN_OCR_TEXT_LEN = 20  # < 20 字符大概率是轴标签碎片


def _is_ocr_text_noise(ocr_text: str) -> bool:
    """检测 ocr_text 是否为噪声，决定是否兜底为 qwen_summary。

    判定规则（任一命中即判噪声）：
    1. 长度 < 20 字符（轴标签碎片）
    2. 含模型训练数据残留幻觉（URL 片段、未知 token 标记等）
    3. 纯数值/百分比的轴刻度碎片
    4. LaTeX 命令残渣（`\\multicolumn` / `\\cite` / `\\ref`）
    5. 字母+下划线+数字的 token 占比 > 50%（实体占位符幻觉）
    """
    if not ocr_text or not ocr_text.strip():
        return True

    text = ocr_text.strip()
    # 短文本也可能是有效的图例、轴标签或指标值；仅凭长度不能判定为噪声。

    # 幻觉标记
    low = text.lower()
    if any(marker.lower() in low for marker in _NOISE_HALLUCINATION_MARKERS):
        return True

    # 轴刻度 / LaTeX 残渣
    if any(p.search(text) for p in _AXIS_NOISE_PATTERNS):
        return True

    # 实体占位符密度：ent20/ent48/entXXX 这种 token 占比 > 50% → CNN/DailyMail 幻觉
    tokens = re.split(r"\s+", text)
    if tokens:
        ent_tokens = sum(1 for t in tokens if re.fullmatch(r"ent\d+|ent\d+,?", t))
        if ent_tokens / len(tokens) > 0.5:
            return True

    return False


def _persist_figures(
    db: Session,
    content: bytes,
    paper_id: str,
    *,
    progress_callback: Callable[[int, str], None] | None = None,
) -> int:
    """抽取 figure 并持久化到数据库；返回处理的 figure 数量。

    会先清空该论文已有 figure，保证幂等（重试/重复提交时不重复）。
    可选 progress_callback(progress, message) 用于上报进度。

    VRAM 策略：本函数在提取 + 语义摘要期间只切一次 vision 模型括号：
    vram_guard("vision") 包裹 extract_figures_for_paper 和 _generate_qwen_summaries，
    vision 完成（上下文退出）后同卡 exclusive 下自动拉回 text-Qwen(8080)。避免双重冷启动。
    """
    from ..crud.figures import delete_figures_by_paper, upsert_figure

    # 延迟导入：避免与 pdf_parser 形成循环依赖
    from ..figure_claims import (
        apply_curve_correction,
        extract_claims_from_text,
        extract_source_text_span,
        validate_claim_with_axis,
    )
    from ..models import Paper
    from ..pdf_parser import extract_figures_for_paper
    from ..semantic_search import embed_text
    from ..vram_scheduler import vram_guard

    delete_figures_by_paper(db, paper_id)

    # ADR-013: 单次 vision VRAM bracket，使用 vram_guard 上下文管理器（自动请求+释放）
    # 取代手动 request_vision + try/finally vision_finished 模式。
    try:
        with vram_guard("vision"):
            figs = extract_figures_for_paper(content, paper_id)
            total = len(figs)

            # Generate Qwen semantic summaries for figures that need richer VLM summary.
            # Fail-open: any Qwen error leaves qwen_summary empty so extraction always succeeds.
            summaries = _generate_qwen_summaries(figs, paper_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[figure] vision/VRAM guard 失败，继续（视觉模型可能 OOM）: %s", exc)
        figs = extract_figures_for_paper(content, paper_id)
        total = len(figs)
        summaries = _generate_qwen_summaries(figs, paper_id)

    # vision_attempted is an internal transient flag; do not persist it.
    for _fig in figs:
        _fig.pop("vision_attempted", None)

    # Fetch the paper's full_text to extract source text spans and claims.
    try:
        paper = db.query(Paper).filter(Paper.id == paper_id).first()
    except Exception as exc:  # noqa: BLE001 - DB query failure should not block figure extraction
        logger.warning("[figure] 无法查询论文 full_text: %s", exc)
        paper = None
    full_text = paper.full_text if paper and paper.full_text else None

    for i, fig in enumerate(figs):
        ocr_text = (fig.get("ocr_text") or "").strip()
        embedding = embed_text(ocr_text) if ocr_text else None

        # P0-1: source text span from full_text
        figure_number = fig.get("figure_number")
        source_text_span = None
        if full_text and figure_number is not None:
            try:
                source_text_span = extract_source_text_span(full_text, figure_number)
            except (re.error, TypeError) as exc:
                logger.warning("[figure] source_text_span 提取失败: %s", exc)

        # P1: validate any numerical claims from caption/source against axis_info
        axis_info = fig.get("axis_info") or None
        claim_validation: dict[str, Any] | None = None
        if axis_info is not None:
            try:
                caption = (fig.get("caption_text") or "").strip()
                text_to_mine = " ".join(part for part in [caption, source_text_span or ""] if part)
                claims = extract_claims_from_text(text_to_mine)
                validated = [validate_claim_with_axis(c, axis_info) for c in claims]
                if validated:
                    claim_validation = {
                        "claims": claims,
                        "validated": validated,
                    }
            except (TypeError, ValueError, KeyError) as exc:
                logger.warning("[figure] claim 校验失败: %s", exc)

        # P3: extract curve points from matplotlib-style line charts
        curve_points: list[dict[str, Any]] | None = None
        if axis_info is not None:
            try:
                from ..figure_curves import extract_curve_points

                curve_points = extract_curve_points(fig["figure_path"], axis_info)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[figure] curve_points 提取失败: %s", exc)

        # P4: 用曲线点对越界断言做二次复核并持久化
        if claim_validation and curve_points:
            apply_curve_correction(claim_validation, curve_points)

        # Qwen/VLM summary 兜底：视觉模型或 text Qwen 均失败时，
        # 用 OCR 文本本身作为摘要，避免 qwen_summary 大量 NULL 导致 QF 节点无信号。
        # 质量门控：过滤 OCR 噪声，避免把乱码当信号喂给 QF LLM。
        qwen_summary = summaries.get(fig["figure_index"])
        if not qwen_summary and ocr_text:
            if _is_ocr_text_noise(ocr_text):
                logger.info(
                    "[figure] ocr_text 判为噪声，不兜底 qwen_summary (paper=%s figure_index=%s ocr_text[:80]=%r)",
                    paper_id,
                    fig["figure_index"],
                    ocr_text[:80],
                )
            else:
                qwen_summary = ocr_text
                logger.debug(
                    "[figure] qwen_summary 回退到 ocr_text (paper=%s figure_index=%s)",
                    paper_id,
                    fig["figure_index"],
                )

        upsert_figure(
            db,
            paper_id=paper_id,
            page=fig["page"],
            figure_index=fig["figure_index"],
            figure_path=fig["figure_path"],
            ocr_text=ocr_text,
            embedding=embedding,
            qwen_summary=qwen_summary,
            caption_text=fig.get("caption_text"),
            source=fig.get("source", "bitmap"),
            figure_number=fig.get("figure_number"),
            match_confidence=fig.get("match_confidence"),
            match_label=fig.get("match_label"),
            vlm_decision=fig.get("vlm_decision"),
            source_text_span=source_text_span,
            axis_info=axis_info,
            claim_validation=claim_validation,
            curve_points=curve_points,
        )
        if progress_callback and total > 0:
            progress_callback(int((i + 1) / total * 90), f"已处理 {i + 1}/{total} 张图")
    return total


def _generate_qwen_summaries(
    figs: list[dict],
    paper_id: str,
) -> dict[int, str | None]:
    """Generate a Qwen semantic summary for figures that need richer summary.

    Figures without OCR text or whose M2 routing decision is ``rule_only``/``skip``
    are skipped by Qwen. ``vlm_decision`` missing defaults to VLM for backward
    compatibility. Exceptions are logged and result in ``None``.

    Note: the caller (``_persist_figures``) already owns the vision VRAM bracket,
    so this function never calls request_vision/vision_finished itself.
    """
    summaries: dict[int, str | None] = {}

    for fig in figs:
        ocr_text = (fig.get("ocr_text") or "").strip()
        figure_path = fig.get("figure_path")
        decision = fig.get("vlm_decision", "vlm")
        # OCR 已退役：有图像文件即可走 Qwen3-VL；仅在既无图像也无文字时跳过。
        if not ocr_text and not figure_path:
            continue

        # M2 VLM 路由：只有 decision 明确为 vlm 时才调用 VLM；
        # rule_only 用 caption/OCR 规则摘要（不占用 Qwen），skip 直接跳过。
        if decision in ("rule_only", "skip"):
            if decision == "rule_only":
                # 规则摘要：组合 caption + OCR，供 QF 节点直接消费
                caption = (fig.get("caption_text") or "").strip()
                summary = f"Caption: {caption}" if caption else f"OCR: {ocr_text[:200]}"
                summaries[fig["figure_index"]] = summary
            # skip 不生成任何摘要
            continue

        # decision == "vlm"：优先使用合并视觉分析阶段已生成的 summary。
        # 若合并视觉调用已经尝试过（vision_attempted=True），即使 summary
        # 为空也不再 fallback 到 ask_qwen，避免同一张图被视觉模型看两次。
        try:
            summary = fig.get("summary")
            if summary:
                summaries[fig["figure_index"]] = (
                    summary.strip() if isinstance(summary, str) else str(summary)
                )
                continue

            if fig.get("vision_attempted"):
                # 视觉模型已经看过这张图但没有产出 summary；不再重复调用，
                # 直接让下游看到空摘要即可（fail-open）。
                summaries[fig["figure_index"]] = None
                continue

            summary = ask_qwen(
                ocr_text,
                figure_path=fig.get("figure_path"),
                timeout=_QWEN_TIMEOUT,
                vram_bracket_external=True,
                caption_text=(fig.get("caption_text") or "").strip(),
            )
            summaries[fig["figure_index"]] = summary.strip() if summary else None
        except Exception as exc:  # noqa: BLE001 - worker loop - single figure failure isolated
            logger.warning(
                "[figure] Qwen/VLM summary failed for paper=%s page=%s fig=%s: %s",
                paper_id,
                fig.get("page"),
                fig.get("figure_index"),
                exc,
            )

    return summaries


def submit_extract_figures_task(paper_id: str) -> str | None:
    """提交 figure 抽取后台任务，上传完成后调用。

    任务从磁盘读取已保存的 PDF，异步抽取 figure 并入库。
    失败不影响主流程。
    """
    try:
        from ..tasks import TaskManager
    except ImportError:  # pragma: no cover - 防御性导入
        logger.warning("[figure] TaskManager 不可用，无法提交后台抽取任务")
        return None

    try:
        task_id = TaskManager.submit(
            "extract_figures",
            params={"paper_id": paper_id},
            worker_fn=_extract_figures_worker,
        )
        logger.info("[figure] 已提交后台抽取任务: %s, paper_id=%s", task_id, paper_id)
        return task_id
    except Exception as e:  # noqa: BLE001 - worker loop - figure 抽取/OCR 单图失败需隔离
        logger.warning("[figure] 提交后台抽取任务失败: %s: %s", paper_id, e)
        return None


def _download_pdf_for_figures(db: Session, paper_id: str) -> bytes | None:
    """本地 PDF 缺失时按需下载（远程 pdf_url 论文）。

    复用 pdf_proxy_service 的 SSRF 白名单 + 私网校验（validate_pdf_url），
    但用 plain requests.get 下载（本沙箱下 pdf_proxy_service 的 DNS 钉死
    补丁会使直连失败，故不走 fetch_pdf_stream）。
    """
    import requests

    from ..models import Paper
    from ..services.pdf_proxy_service import resolve_pdf_url, validate_pdf_url

    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if not paper or not paper.pdf_url:
        logger.warning("[figure] 无 pdf_url，无法按需下载: %s", paper_id)
        return None
    url = resolve_pdf_url(paper_id, paper.pdf_url)
    try:
        validate_pdf_url(url)  # SSRF 白名单 + 协议 + 私网 IP 校验（不钉死 DNS）
    except Exception as exc:  # noqa: BLE001
        logger.warning("[figure] 按需下载 SSRF/白名单拒绝 %s: %s", url, exc)
        return None
    try:
        # SSRF：禁止自动跟随重定向（allow_redirects=False），逐跳复核 host，
        # 防止外部可控 pdf_url 经 302 跳到内网绕过白名单/私网校验（Cody #1/#2）。
        resp = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "PaperForge-Ingest/1.0"},
            stream=True,
            allow_redirects=False,
        )
        max_redirects = 5
        current_url = url
        for _ in range(max_redirects):
            if resp.status_code not in (301, 302, 303, 307, 308):
                break
            location = resp.headers.get("Location")
            if not location:
                break
            if not location.startswith(("http://", "https://")):
                from urllib.parse import urljoin

                location = urljoin(current_url, location)
            validate_pdf_url(location)  # 逐跳 SSRF 白名单 + 私网校验（拒绝则抛 HTTPException）
            current_url = location
            resp = requests.get(
                location,
                timeout=30,
                headers={"User-Agent": "PaperForge-Ingest/1.0"},
                stream=True,
                allow_redirects=False,
            )
        else:
            logger.warning("[figure] 按需下载 PDF 重定向次数超限 %s", paper_id)
            return None
        resp.raise_for_status()
        return b"".join(resp.iter_content(chunk_size=8192))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[figure] 按需下载 PDF 失败 %s: %s", paper_id, exc)
        return None


def _extract_figures_worker(task_id: str, params: dict) -> None:
    """后台任务：从已落盘的 PDF 抽取 figure 并入库。

    失败时调用 TaskManager.fail，成功时调用 TaskManager.complete。
    """
    paper_id = params.get("paper_id")
    if not paper_id:
        from ..tasks import TaskManager

        TaskManager.fail(task_id, "缺少 paper_id")
        return

    from ..database import SessionLocal

    db = SessionLocal()
    try:
        from ..tasks import TaskManager, get_cancel_event

        TaskManager.update_progress(task_id, 0, "开始抽取实验图")
        cancel_event = get_cancel_event(task_id)

        # 延迟导入：避免与 pdf_parser 形成循环依赖
        from ..utils.paths import _get_uploads_dir

        pdf_path = _get_uploads_dir() / f"{paper_id}.pdf"
        if pdf_path.exists():
            content = pdf_path.read_bytes()
        else:
            # 本地无 PDF（如仅存远程 pdf_url 的论文）→ 按需下载，避免 "PDF 文件不存在" 失败。
            # 这关闭了回填/远程论文无法做 figure 抽取的缺口。
            content = _download_pdf_for_figures(db, paper_id)
            if not content:
                TaskManager.fail(task_id, f"PDF 不可得（本地缺失且下载失败）: {paper_id}")
                return

        def _progress(progress: int, message: str) -> None:
            if cancel_event.is_set():
                raise RuntimeError("任务已取消")
            TaskManager.update_progress(task_id, progress, message)

        if cancel_event.is_set():
            raise RuntimeError("任务已取消")
        count = _persist_figures(db, content, paper_id, progress_callback=_progress)
        db.commit()
        logger.info("[figure] 后台抽取完成: %s, %d 张, paper_id=%s", task_id, count, paper_id)
        TaskManager.complete(task_id, {"paper_id": paper_id, "figures_count": count})
    except Exception as e:  # noqa: BLE001 - worker loop - figure 抽取/OCR 单图失败需隔离
        logger.exception("[figure] 后台抽取失败 [%s]: %s", paper_id, e)
        try:
            db.rollback()
        except Exception:  # noqa: BLE001 - worker loop - figure 抽取/OCR 单图失败需隔离
            pass
        from ..tasks import TaskManager

        TaskManager.fail(task_id, str(e))
    finally:
        db.close()
