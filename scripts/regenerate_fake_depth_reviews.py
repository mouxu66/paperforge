#!/usr/bin/env python3
"""用当前代码重生成两个 fake 论文的 depth_review run JSON（覆盖过时基线）。

依赖真实 LLM：`DepthReviewer` 内部默认复用项目的 `call_llm`（自动读取
PAPERFORGE_LLM_* 等环境变量 / 配置）。在无 LLM 凭据的机器上运行会失败，
这是预期行为——请在已配置 LLM 的环境执行本脚本。

用法（在仓库根目录）：
    .venv/Scripts/python.exe scripts/regenerate_fake_depth_reviews.py

会覆盖：
    scripts/runs/depth_review_fake_DataManip.json
    scripts/runs/depth_review_fake_NSGT.json
"""
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mock_api.depth_eval_v4 import DepthReviewer  # noqa: E402

PAPERS = {
    "DataManip": "calib_papers/fake_paper_DataManip.txt",
    "NSGT": "calib_papers/fake_paper_NSGT.txt",
}


async def main() -> None:
    reviewer = DepthReviewer(compute_mode="deep")
    for name, rel in PAPERS.items():
        path = os.path.join(ROOT, rel)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        result = await reviewer.review_async_dag(
            paper_id=f"fake_{name}",
            title=f"fake_{name}",
            full_text=text,
            abstract=text[:1500],
        )
        out_path = os.path.join(ROOT, "scripts", "runs", f"depth_review_fake_{name}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(result.model_dump_json(indent=2))
        print(
            f"{name}: verdict={result.final_verdict} "
            f"score={result.calibrated_score:.3f} "
            f"flags={len(result.statistical_flags)}"
        )


if __name__ == "__main__":
    asyncio.run(main())
