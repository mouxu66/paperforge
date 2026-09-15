"""单篇感悟报告评审一次（供「报告条件化证据检索 / 思考模式」A/B 对照实验调用）。

用法（在仓库根目录）：
    .venv/Scripts/python.exe scripts/calibration/run_evidence_once.py \
        --report reflection_999900000037 --evidence 1 --thinking 1

输出：单行 JSON（scores / verdict / average / evidence_retrieval / thinking 摘录）。
思考模式开启时，完整 CoT 另存 deliverables/thinking_<report>.md。
每篇 × 每种配置用独立子进程跑，避免进程内 TTL 缓存把第二次运行串味。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True, help="报告 paper_id，如 reflection_999900000037")
    ap.add_argument("--evidence", choices=["0", "1"], default="0")
    ap.add_argument("--thinking", choices=["0", "1"], default="0")
    args = ap.parse_args()

    os.environ["PAPERFORGE_REFLECTION_EVIDENCE"] = args.evidence
    os.environ["PAPERFORGE_REFLECTION_THINKING"] = args.thinking
    # Win 沙箱稳定性开关（不影响评分链路）
    os.environ.setdefault("PAPERFORGE_DISABLE_RESOURCE_DETECT", "1")

    from mock_api.database import SessionLocal
    from mock_api.models import Paper
    from mock_api.reflection_pipeline import analyze_reflection_file

    db = SessionLocal()
    try:
        p = db.query(Paper).filter(Paper.id == args.report).first()
        if p is None or not p.reflection_docx_path:
            print(json.dumps({"error": f"report {args.report} 不存在或缺 docx 路径"}))
            return 1
        t0 = time.time()
        res = analyze_reflection_file(
            p.reflection_docx_path, db, source_paper_id=p.source_paper_id
        )
        dt = round(time.time() - t0, 1)
    finally:
        db.close()

    ev = res.get("evidence_retrieval", {})
    thinks = res.get("thinking_excerpts", [])
    # 完整 CoT 落盘（每篇一个文件，追加模式供多轮对照）。
    # ⚠️ 审计写盘绝不能弄死评分主流程：文件被占用（预览句柄/杀软扫描）时
    # 降级为带时间戳的备用文件名，再失败就跳过——JSON 结果必须照常输出。
    def _write_cot(path: Path) -> None:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n\n## run @ {time.strftime('%H:%M:%S')} evidence={args.evidence}\n")
            for i, t in enumerate(thinks, 1):
                f.write(f"\n### 调用 #{i}\n\n{t}\n")

    if thinks:
        out_md = Path("deliverables") / f"thinking_{args.report}.md"
        try:
            out_md.parent.mkdir(exist_ok=True)
            _write_cot(out_md)
        except OSError as e:
            alt = Path("deliverables") / f"thinking_{args.report}_{time.strftime('%H%M%S')}.md"
            print(f"[warn] CoT 落盘降级到 {alt.name}（{e}）", file=sys.stderr)
            try:
                _write_cot(alt)
            except OSError:
                print("[warn] CoT 备用落盘也失败，跳过（不影响评分结果）", file=sys.stderr)
    out = {
        "report": args.report,
        "evidence": args.evidence,
        "thinking": args.thinking,
        "seconds": dt,
        "verdict": res.get("verdict"),
        "average": res.get("average"),
        "llm_failed": res.get("llm_failed"),
        "parse_failed": res.get("parse_failed"),
        "llm_calls": res.get("llm_calls"),
        "llm_empty": res.get("llm_empty"),
        "copy_ratio": res.get("copy_ratio"),
        "scores": {
            k: res.get("scores", {}).get(k)
            for k in (
                "understanding_accuracy",
                "analysis_depth",
                "innovative_insights",
                "evidence_support",
                "fidelity",
                "coverage",
            )
        },
        "evidence_status": ev.get("status"),
        "evidence_chars": ev.get("chars"),
        "evidence_blocks": ev.get("block_count"),
        "thinking_calls": len(thinks),
        "thinking_lens": [len(t) for t in thinks],
        "thinking_preview": [t[:200] for t in thinks[:2]],
        "blocks_preview": [
            {"text": b.get("text", "")[:70], "sim": b.get("sim"), "query": b.get("query", "")[:50]}
            for b in (ev.get("blocks") or [])[:4]
        ],
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
