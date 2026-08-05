#!/usr/bin/env python3
"""端到端 pipeline：PDF 实验图 → OCR → Qwen 解读 → 存入 DB。

把 figure 的 OCR 文本自动传给本地 Qwen，并将 Qwen 的解读摘要
写回 paper_figures 表的 qwen_summary 字段，与论文/图绑定。

run_pipeline() 支持传入 extract_fn 和 ask_fn，方便 pytest 集成测试做 mock。
"""

from __future__ import annotations

import argparse
import os
import sys
import inspect
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

# 让 OCR 走 GPU（如果 .env 未配置）
if os.environ.get("LLAMA_CPP_N_GPU_LAYERS") is None:
    os.environ["LLAMA_CPP_N_GPU_LAYERS"] = "99"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.figure_utils import ask_qwen, make_experimental_chart_bytes, make_pdf_with_figure


# ---------------------------------------------------------------------------
# 核心 pipeline（支持依赖注入）
# ---------------------------------------------------------------------------
ExtractFn = Callable[[bytes, str], list[dict]]
AskFn = Callable[[str], str]


def run_pipeline(
    paper_id: str,
    title: str,
    pdf_bytes: bytes,
    db,
    extract_fn: ExtractFn | None = None,
    ask_fn: AskFn | None = None,
    vram_bracket: bool = False,
) -> int:
    """运行 figure → Qwen3-VL-4B 视觉摘要 → DB 链路。

    2026-07-27 重构：跳过 PaddleOCR-VL，只用 Qwen3-VL-4B 一个视觉模型。
    - ``extract_figures_for_paper(skip_ocr=True)`` 只抽图 + 关联 caption，不跑 OCR。
    - 直接调用 Qwen3-VL-4B（_ask_vision_on_figure）生成 qwen_summary，
      该模型 prompt 已包含"图中若有文字/标签请一并识别"，等效 OCR + 语义摘要。
    - 不再需要 OCR VRAM bracket（无 PaddleOCR-VL 互斥），避免 segfault。

    Args:
        paper_id: 论文 ID（PaperFigure 外键）。
        title: 论文标题（创建 paper 记录时用）。
        pdf_bytes: 待解析的 PDF 字节。
        db: SQLAlchemy session。
        extract_fn: 替代 ``extract_figures_for_paper`` 的可调用对象（测试用）。
        ask_fn: 替代 ``ask_qwen`` 的可调用对象（测试用 / summary 缺位兜底）。
        vram_bracket: 已废弃（保留参数兼容旧调用），实际无操作。

    Returns:
        0 表示成功，1 表示失败（无 figure）。
    """
    from mock_api.crud.figures import delete_figures_by_paper, upsert_figure
    from mock_api.models import Paper, PaperFigure
    from mock_api.pdf_parser import extract_figures_for_paper, route_vlm_for_figure
    from mock_api.semantic_search import embed_text
    from mock_api.llm.figure_qwen import _ask_vision_on_figure

    extract_fn = extract_fn or extract_figures_for_paper
    ask_fn = ask_fn or ask_qwen
    try:
        production_extractor = "skip_ocr" in inspect.signature(extract_fn).parameters
    except (TypeError, ValueError):
        production_extractor = False

    # 1. 确保 paper 记录存在（满足外键）
    paper = db.query(Paper).filter(Paper.id == paper_id).first()
    if paper is None:
        paper = Paper(
            id=paper_id,
            title=title,
            authors=[],
            abstract="Synthetic paper used for testing figure OCR and Qwen analysis.",
            year=datetime.now().year,
            source="benchmark",
        )
        db.add(paper)
        db.commit()

    # 2. 跳过 PaddleOCR-VL，直接抽图（skip_ocr=True），不进 OCR VRAM bracket
    #    Qwen3-VL-4B 单独生成 qwen_summary，无互斥 segfault
    try:
        # 3. 抽取 figure（只抽图 + 关联 caption，不跑 OCR）
        print("\n[提取 figure（skip_ocr，只用 Qwen3-VL-4B）...]")
        start = time.time()
        # Production extraction explicitly accepts skip_ocr (the retired OCR
        # gate); injected seams may intentionally expose only two positional args.
        if production_extractor:
            figs = extract_fn(pdf_bytes, paper_id, skip_ocr=True)
        else:
            figs = extract_fn(pdf_bytes, paper_id)
        elapsed = time.time() - start

        if not figs:
            print("  [错误] 未抽取到 figure")
            return 1
        print(f"  figure 数: {len(figs)}, 耗时: {elapsed:.2f}s")

        # 4. 幂等：先清旧记录（upsert_figure 为纯追加，重跑会重复）
        delete_figures_by_paper(db, paper_id)

        # 5. 逐图用 Qwen3-VL-4B 生成 qwen_summary（替代 PaddleOCR-VL）
        print("\n[逐图调用 Qwen3-VL-4B 生成 qwen_summary...]")
        stored_count = 0
        for i, fig in enumerate(figs, 1):
            figure_path = fig.get("figure_path", "")
            caption_text = fig.get("caption_text", "") or ""
            # 生产抽取器已退役 OCR，返回空字符串；注入测试/迁移调用可提供
            # 已有文字，便于验证 pipeline 的 DB 消费契约。
            ocr_text = (fig.get("ocr_text") or "").strip() if not production_extractor else ""
            vlm_decision = (
                fig.get("vlm_decision")
                or route_vlm_for_figure(fig, ocr_text=ocr_text)["decision"]
            )

            # Qwen3-VL-4B 视觉理解：生成 qwen_summary（包含文字识别 + 轴信息 + 语义）
            qwen_summary = None
            if figure_path:
                try:
                    print(f"  [{i}/{len(figs)}] Qwen3-VL-4B 视觉理解: {figure_path}")
                    vision_summary = _ask_vision_on_figure(
                        figure_path=figure_path,
                        caption_text=caption_text,
                        timeout=120,
                    )
                    if vision_summary:
                        qwen_summary = vision_summary
                        print(f"    [ok] qwen_summary 长度: {len(qwen_summary)} 字符")
                    else:
                        print(f"    [warn] Qwen3-VL-4B 返回空")
                except Exception as exc:
                    print(f"    [warn] Qwen3-VL-4B 调用失败: {exc}")

            # 测试/注入 extractor 使用 ask_fn 验证消费链；生产路径只依赖
            # Qwen3-VL HTTP 与 caption 兜底，不恢复旧 OCR。
            if not qwen_summary and not production_extractor and ocr_text:
                try:
                    qwen_summary = ask_fn(ocr_text)
                except Exception as exc:
                    print(f"    [warn] 注入 summary 函数失败: {exc}")
            if not qwen_summary and caption_text.strip():
                qwen_summary = f"Caption: {caption_text.strip()}"

            embedding = None
            try:
                # 用 qwen_summary 生成 embedding（替代原 ocr_text）
                embedding = embed_text(qwen_summary) if qwen_summary else None
            except Exception as exc:
                print(f"  [警告] embedding 计算失败，跳过: {exc}")

            upsert_figure(
                db,
                paper_id=paper_id,
                page=fig.get("page", 1),
                figure_index=fig.get("figure_index", 0),
                figure_path=fig.get("figure_path", ""),
                ocr_text=ocr_text,
                embedding=embedding,
                qwen_summary=qwen_summary,
                caption_text=fig.get("caption_text"),
                source=fig.get("source", "bitmap"),
                figure_number=fig.get("figure_number"),
                match_confidence=fig.get("match_confidence"),
                match_label=fig.get("match_label"),
                vlm_decision=vlm_decision,
                axis_info=fig.get("axis_info"),
            )
            stored_count += 1
        db.commit()
        print(f"  已写入 {stored_count} 张 figure")

        # 6. 验证
        stored = db.query(PaperFigure).filter(PaperFigure.paper_id == paper_id).all()
        if stored:
            print(f"  入库 figure 数: {len(stored)}")
            sample = stored[0]
            print(f"  样例 qwen_summary 长度: {len(sample.qwen_summary or '')} 字符")
            print(f"  样例 vlm_decision: {sample.vlm_decision}")
        else:
            print("  [警告] 未查询到入库记录")

        print("\n[完成] figure → Qwen3-VL-4B 视觉摘要 → DB 链路已打通")
        return 0
    finally:
        pass  # 无 OCR VRAM bracket，无需清理


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="端到端 pipeline：PDF 实验图 → OCR → Qwen 解读 → 存入 DB",
    )
    parser.add_argument(
        "--pdf",
        type=str,
        default=None,
        help="输入 PDF 文件路径。不提供时生成合成 PDF 用于测试。",
    )
    parser.add_argument(
        "--paper-id",
        type=str,
        default=None,
        help="论文 ID（PaperFigure 外键）。默认从 PDF 文件名推导，或合成模式下使用默认值。",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="论文标题。默认从 PDF 文件名推导，或合成模式下使用默认标题。",
    )
    parser.add_argument(
        "--qwen-url",
        type=str,
        default="http://localhost:8080",
        help="本地 Qwen 服务基础 URL（默认 http://localhost:8080）。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    print("=" * 60)
    print("端到端 pipeline：Figure → OCR → Qwen → DB")
    print("=" * 60)

    from mock_api.database import SessionLocal, init_db

    # 1. 准备 PDF 字节和 paper 元数据
    if args.pdf:
        pdf_path = Path(args.pdf)
        if not pdf_path.exists():
            print(f"[错误] PDF 文件不存在: {pdf_path}", file=sys.stderr)
            return 1
        if not pdf_path.is_file():
            print(f"[错误] 路径不是文件: {pdf_path}", file=sys.stderr)
            return 1
        print(f"\n[1/4] 读取输入 PDF: {pdf_path}")
        pdf_bytes = pdf_path.read_bytes()
        paper_id = args.paper_id or pdf_path.stem
        title = args.title or pdf_path.stem
    else:
        print("\n[1/4] 未指定 --pdf，生成合成实验图并嵌入 PDF...")
        img_bytes = make_experimental_chart_bytes(
            title="Figure 3: Test Accuracy on ImageNet-1K",
            best_accuracy=89.7,
        )
        pdf_bytes = make_pdf_with_figure(
            img_bytes,
            title="Realistic Experimental Figure Test",
            paragraphs=["We compare Baseline and Proposed methods on ImageNet-1K."],
            caption="The proposed method achieves 89.7% accuracy, surpassing baseline by 2.2%.",
        )
        print(f"  图像 {len(img_bytes)} bytes, PDF {len(pdf_bytes)} bytes")
        paper_id = args.paper_id or "realistic_benchmark_001"
        title = args.title or "Benchmark Paper for Figure Understanding"

    # 2. 初始化 DB
    print("\n[2/4] 初始化 DB session...")
    init_db()

    # 3. 跑 pipeline（figure 视觉改走 Qwen3-VL-4B HTTP，无 PaddleOCR-VL 互斥，
    #    无需 OCR VRAM bracket；run_pipeline 内部已 skip_ocr，见模块 docstring）。
    print("\n[3/4] 运行 figure → Qwen3-VL-4B 摘要 → DB pipeline...")
    ask_fn = lambda text: ask_qwen(text, base_url=args.qwen_url)
    with SessionLocal() as db:
        rc = run_pipeline(
            paper_id=paper_id,
            title=title,
            pdf_bytes=pdf_bytes,
            db=db,
            ask_fn=ask_fn,
        )
    return rc


if __name__ == "__main__":
    sys.exit(main())
