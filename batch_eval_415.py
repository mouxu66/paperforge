"""几百篇规模 DEPTH 确定性批量评估（P0-A 回填完成后）。

背景
----
P0-A 将 full_text 覆盖率从 145 → 415 篇，满足"几百篇"规模评估的物理前提。
本脚本对全部 415 篇（有 full_text）论文跑 DEPTH v4 审稿，验证：
  (1) 系统稳定性：415 篇连跑不崩溃、不 OOM、8080 不雪崩；
  (2) 真实分布：overall_score / verdict / QF（P0-B 激活）的规模化分布。

确定性
------
启动时设 PAPERFORGE_LLM_TEMPERATURE=0（base.build_payload 强制 greedy），
消除 run-to-run 漂移。如需非确定性对照，去掉该 env 即可。
同时脚本内默认设 PAPERFORGE_DISABLE_FIGURE_TRIGGER=1，禁止批量跑里自动派发
figure_understanding（OCR 同进程加载会 segfault 拖垮进程）。

运行
----
    PAPERFORGE_LLM_TEMPERATURE=0 python batch_eval_415.py [--limit N] [--clear]
    --clear  先删掉这 415 篇已有的 DepthReviewV4，保证快照纯净（默认开）。
    --limit  只跑前 N 篇（试点）。
日志：batch_eval_415.log（每篇一行 + 末尾汇总）。
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# 🛡️ 批量评估必须禁用 figure_understanding 自动触发，
# figure 抽取应作独立小批量 pass，绝不在 bulk DEPTH 里内联/自动触发。
os.environ.setdefault("PAPERFORGE_DISABLE_FIGURE_TRIGGER", "1")

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("batch_eval_415")

from mock_api.database import SessionLocal, init_db  # noqa: E402
from mock_api.models import Paper, DepthReviewV4  # noqa: E402
from mock_api.depth_tasks import run_depth_review_sync  # noqa: E402


def collect_targets(limit: int) -> list[str]:
    db = SessionLocal()
    try:
        rows = (
            db.query(Paper.id)
            .filter(Paper.full_text.isnot(None), Paper.full_text != "")
            .order_by(Paper.id)
            .all()
        )
        ids = [r.id for r in rows]
    finally:
        db.close()
    return ids[:limit] if limit and limit > 0 else ids


def clear_old_reviews(ids: list[str]) -> int:
    db = SessionLocal()
    try:
        n = (
            db.query(DepthReviewV4)
            .filter(DepthReviewV4.paper_id.in_(ids))
            .delete(synchronize_session=False)
        )
        db.commit()
        return n
    finally:
        db.close()


def read_latest_verdict(pid: str) -> dict:
    db = SessionLocal()
    try:
        rec = (
            db.query(DepthReviewV4)
            .filter(DepthReviewV4.paper_id == pid)
            .order_by(DepthReviewV4.created_at.desc())
            .first()
        )
        if rec is None:
            return {}
        return rec.final_verdict or {}
    finally:
        db.close()


def already_done(pid: str) -> bool:
    """续跑判断：该论文已有落库且 final_verdict 含 calibrated_score 视为完成。"""
    db = SessionLocal()
    try:
        rec = (
            db.query(DepthReviewV4)
            .filter(DepthReviewV4.paper_id == pid)
            .order_by(DepthReviewV4.created_at.desc())
            .first()
        )
        if rec is None:
            return False
        fv = rec.final_verdict or {}
        return fv.get("calibrated_score") is not None
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="几百篇 DEPTH 确定性批量评估")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--clear", action="store_true", default=True)
    ap.add_argument("--no-clear", dest="clear", action="store_false")
    ap.add_argument("--resume", action="store_true",
                    help="续跑模式：不清库，跳过已有 calibrated_score 的论文")
    ap.add_argument("--log", type=str, default=str(PROJECT_ROOT / "batch_eval_415_offset.log"))
    args = ap.parse_args()

    fh = logging.FileHandler(args.log, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(fh)

    init_db()
    ids = collect_targets(args.limit)
    log.info("目标论文数: %d（有 full_text）", len(ids))

    if args.resume:
        args.clear = False
        log.info("续跑模式：跳过已完成篇，不清库")
    if args.clear:
        n = clear_old_reviews(ids)
        log.info("已清除旧 DepthReviewV4: %d 行", n)

    total = len(ids)
    done = 0
    ok = 0
    failed = 0
    t_start = time.time()
    per_paper = []

    for idx, pid in enumerate(ids, start=1):
        if args.resume and already_done(pid):
            log.info("[%d/%d] SKIP %s（已完成）", idx, total, pid)
            done += 1
            continue
        t0 = time.time()
        try:
            run_depth_review_sync(pid)
            fv = read_latest_verdict(pid)
            score = fv.get("calibrated_score")
            if score is None:
                score = fv.get("base_score")
            verdict = fv.get("final_verdict") or fv.get("verdict") or fv.get("llm_verdict")
            qf = fv.get("figure_consistency_score")
            ok += 1
            log.info(
                "[%d/%d] OK %s score=%.3f verdict=%s qf=%s (%.1fs)",
                idx, total, pid, score if score is not None else -1,
                verdict, qf, time.time() - t0,
            )
            per_paper.append((pid, True, time.time() - t0, score, verdict, qf))
        except Exception as exc:  # noqa: BLE001 - 单篇失败不影响整体
            failed += 1
            log.warning("[%d/%d] FAIL %s: %s (%.1fs)", idx, total, pid, exc, time.time() - t0)
            per_paper.append((pid, False, time.time() - t0, None, None, None))
        done += 1
        # 每 10 篇打印一次实时进度汇总
        if done % 10 == 0:
            elapsed_so_far = time.time() - t_start
            avg_per = elapsed_so_far / ok if ok else 0
            remaining_papers = total - done
            eta_s = avg_per * remaining_papers
            from collections import Counter as _C
            recent_v = _C(v for _, ok_, _, _, v, _ in per_paper[-10:] if ok_ and v)
            log.info(
                "--- 进度 %d/%d | OK=%d FAIL=%d | 过去10篇: %s | 平均%.1fs/篇 | 剩余%.0f分钟 ---",
                done, total, ok, failed, dict(recent_v), avg_per, eta_s / 60,
            )

    elapsed = time.time() - t_start
    log.info(
        "完成: 处理=%d 成功=%d 失败=%d 总耗时=%.1fs 平均=%.1fs/篇",
        done, ok, failed, elapsed, elapsed / max(done, 1),
    )

    # 汇总分布（仅成功篇）
    import json  # noqa: E402
    from collections import Counter  # noqa: E402

    scores = [s for _, _, _, s, _, _ in per_paper if s is not None]
    verdicts = Counter(v for _, _, _, _, v, _ in per_paper if v)
    qfs = [q for _, _, _, _, _, q in per_paper if q is not None]
    summary = {
        "total": total, "ok": ok, "failed": failed,
        "elapsed_s": round(elapsed, 1),
        "avg_per_paper_s": round(elapsed / max(done, 1), 1),
        "score_count": len(scores),
        "score_mean": round(sum(scores) / len(scores), 3) if scores else None,
        "score_min": round(min(scores), 3) if scores else None,
        "score_max": round(max(scores), 3) if scores else None,
        "verdict_dist": dict(verdicts),
        "qf_count": len(qfs),
        "qf_non_neutral": sum(1 for q in qfs if abs(q - 0.5) > 1e-6),
        "qf_mean": round(sum(qfs) / len(qfs), 3) if qfs else None,
    }
    log.info("SUMMARY: %s", json.dumps(summary, ensure_ascii=False))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
