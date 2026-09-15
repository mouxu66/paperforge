"""一次性：用云端 GLM 视觉跑 pr_1612.08810 的坐标轴审计，并做 OCR 数字交叉对比。

前置：进程环境变量需含
  PAPERFORGE_GLM_VISION_ENABLED=1
  PAPERFORGE_GLM_VISION_API_KEY=xxx
（本地 Qwen 配置不受影响；此脚本只读 GLM 独立字段）

输出：
  1) 审计 findings（P0-4 风险清单 = 用户要看的"分数"）
  2) 有 OCR 的图：GLM 视觉读数 vs OCR 文本 的数字级冲突对比
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PAPER_ID = os.environ.get("PAPER_ID", "pr_1612.08810")


def main() -> int:
    from mock_api.database import SessionLocal
    from mock_api.experiment_audit.figures import (
        analyze_figure_semantic_glm,
        check_figure_axis_risks,
        _parse_glm_json,
        _compare_ocr_vs_glm,
    )
    from mock_api.settings import get_settings

    st = get_settings()
    print(f"[cfg] glm_enabled={st.glm_vision_enabled} model={st.glm_vision_model} "
          f"base={st.glm_vision_base_url} max_concur={st.glm_vision_max_concur}\n")

    db = SessionLocal()
    try:
        # ── 阶段1：完整审计（含 GLM 语义兜底）──
        print(f"===== 阶段1: check_figure_axis_risks({PAPER_ID}) =====")
        findings = check_figure_axis_risks(db, PAPER_ID, allow_vlm=True)
        print(f"总 findings: {len(findings)}")
        for f in findings:
            print(f"  - [{f.get('severity','?')}] {f.get('title')} | page={f.get('page')} "
                  f"| human_review={f.get('needs_human_review')}")
            if f.get('claim'):
                print(f"      claim: {f['claim'][:160]}")
        print()

        # ── 阶段2：有 OCR 的图，强制调 GLM 做数字级交叉对比 ──
        from mock_api.models import PaperFigure
        figs = (
            db.query(PaperFigure)
            .filter(PaperFigure.paper_id == PAPER_ID)
            .order_by(PaperFigure.page, PaperFigure.figure_index)
            .all()
        )
        uploads = ROOT / "uploads" / "figures" / PAPER_ID
        print(f"===== 阶段2: OCR × GLM 数字交叉对比（有 OCR 的图）=====")
        n_ocr = 0
        n_conflict = 0
        for fig in figs:
            ocr = fig.ocr_text or ""
            if not ocr.strip():
                continue
            n_ocr += 1
            fid = f"Figure {fig.figure_number}"
            img_path = uploads / Path(fig.figure_path).name if fig.figure_path else None
            # 与阶段1 同口径：把 OCR 当锚点喂入，并用统一的 A+B 交叉验证逻辑。
            glm_response = (
                analyze_figure_semantic_glm(
                    str(img_path), fig.caption_text or "", ocr_text=fig.ocr_text or ""
                )
                if img_path and img_path.exists()
                else ""
            )
            glm_json = _parse_glm_json(glm_response)
            cross = _compare_ocr_vs_glm(glm_json, fig.ocr_text or "", glm_raw=glm_response)
            if cross is None:  # 无数字可比（A+B 信息不足，退化为纯视觉）
                print(f"\n  {fid} (page {fig.page})  skip(A+B 无数字可比)")
                continue
            conflict = bool(cross.get("conflict"))
            if conflict:
                n_conflict += 1
            print(f"\n  {fid} (page {fig.page})  conflict={conflict}")
            print(f"    OCR数字: {cross['ocr_numbers']}")
            print(f"    GLM数字: {cross['glm_numbers']}")
            if cross.get("missing_in_glm"):
                print(f"    ⚠ OCR有但GLM未读到: {cross['missing_in_glm']}")
            if cross.get("extra_in_glm"):
                print(f"    · GLM独有(图像真实/OCR漏): {cross['extra_in_glm']}")
        print(f"\n有OCR图={n_ocr}  数字维度潜在冲突={n_conflict}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
