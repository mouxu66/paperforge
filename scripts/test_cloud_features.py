"""5 功能 × N 篇 云端混合评审（本地主 + 云端终审/覆盖）集成测试 harness。

覆盖本次落地的 5 个功能（dcd5ab8 / c07f0f9 / a109309）：
  A  DEPTH 论文路径   run_depth_review_sync        → 云端 second_opinion 覆盖 final_verdict
  B  DEPTH 反思路径   run_depth_reflection_sync    → 云端 second_opinion 覆盖 reflection_result
  C  图理解云端视觉   figure_qwen.ask_qwen         → 云端 GLM 视觉（本地 8082 未起，非空即云端命中）
  D  被引情感云端复核 extract_citation_sentiments  → 低置信触发云端复核，写 cloud_recheck 列
  E  GLM 编排         run_glm_orchestrate.main      → 阶段0/2 云端视觉 + 阶段4 云端 4.7 复核

用法：
  python scripts/test_cloud_features.py --probe            # 每功能 1 篇（快速验证接线）
  python scripts/test_cloud_features.py --feature all --n 10
  python scripts/test_cloud_features.py --feature A --n 10
结果写到 deliverables/feature_test_<ts>.json 并打印汇总。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(ROOT, ".env"))

# ── 云端第二评审结果捕获（验证 A/B 的云端覆盖链路）──
SO_CAPTURE: list = []


def _install_so_capture():
    import mock_api.second_opinion as so

    orig = so.run_second_opinion

    def _wrapper(*a, **k):
        res = orig(*a, **k)
        SO_CAPTURE.append(res)
        return res

    so.run_second_opinion = _wrapper
    return orig


def _feat_a(n, log):
    """DEPTH 论文路径：本地 8080 主评审 + 云端 GLM 覆盖。"""
    from mock_api.database import SessionLocal
    from mock_api.models import DepthReviewV4, Paper
    import mock_api.depth_tasks as depth_tasks

    db = SessionLocal()
    rows = db.query(Paper).filter(Paper.full_text.isnot(None)).all()
    db.close()
    rows = [r for r in rows if r.full_text and len(r.full_text) > 200 and not r.id.startswith("pr_")]
    rows.sort(key=lambda p: len(p.full_text))
    chosen = rows[:n]
    for p in chosen:
        SO_CAPTURE.clear()
        t0 = time.time()
        try:
            rid = depth_tasks.run_depth_review_sync(p.id)
            db = SessionLocal()
            rec = db.query(DepthReviewV4).filter(DepthReviewV4.id == rid).first()
            so = SO_CAPTURE[-1] if SO_CAPTURE else None
            log.append({
                "paper": p.id, "record_id": rid,
                "status": getattr(rec, "status", None),
                "final_verdict": str(getattr(rec, "final_verdict", None))[:200],
                "needs_human": getattr(rec, "needs_human_review", None),
                "so_enabled": so.get("enabled") if so else None,
                "so_flag": so.get("flag") if so else None,
                "so_corrected": so.get("corrected") if so else None,
                "so_provider": so.get("second_provider") if so else None,
                "secs": round(time.time() - t0, 1),
            })
            db.close()
        except Exception as e:
            log.append({"paper": p.id, "error": str(e)[:300], "secs": round(time.time() - t0, 1)})


def _feat_b(n, log):
    """DEPTH 反思路径：本地 8080 主评审 + 云端 GLM 覆盖。"""
    from mock_api.database import SessionLocal
    from mock_api.models import DepthReviewV4, Paper
    import mock_api.depth_tasks as depth_tasks

    db = SessionLocal()
    rows = db.query(Paper).filter(Paper.full_text.isnot(None), Paper.id.like("pr_%")).all()
    db.close()
    rows = [r for r in rows if r.full_text and len(r.full_text) > 500]
    rows.sort(key=lambda p: len(p.full_text))
    chosen = rows[:n]
    for p in chosen:
        SO_CAPTURE.clear()
        t0 = time.time()
        try:
            rid = depth_tasks.run_depth_reflection_sync(p.id)
            db = SessionLocal()
            rec = db.query(DepthReviewV4).filter(DepthReviewV4.id == rid).first()
            so = SO_CAPTURE[-1] if SO_CAPTURE else None
            rr = rec.reflection_result if rec else None
            log.append({
                "paper": p.id, "record_id": rid,
                "status": getattr(rec, "status", None),
                "verdict": (rr or {}).get("verdict"),
                "so_enabled": so.get("enabled") if so else None,
                "so_flag": so.get("flag") if so else None,
                "so_corrected": so.get("corrected") if so else None,
                "secs": round(time.time() - t0, 1),
            })
            db.close()
        except Exception as e:
            log.append({"paper": p.id, "error": str(e)[:300], "secs": round(time.time() - t0, 1)})


def _feat_c(n, log):
    """图理解云端视觉：ask_qwen（本地 8082 未起 → 非空即云端 GLM 命中）。"""
    from mock_api.database import SessionLocal
    from mock_api.models import PaperFigure
    from mock_api.llm.figure_qwen import ask_qwen

    db = SessionLocal()
    figs = db.query(PaperFigure).filter(PaperFigure.figure_path.isnot(None)).all()
    db.close()
    chosen = [f for f in figs if f.figure_path and os.path.exists(f.figure_path)][:n]
    for f in chosen:
        t0 = time.time()
        try:
            summary = ask_qwen("", f.figure_path, caption_text=f.caption_text or "")
            ok = bool(summary and summary.strip())
            log.append({
                "paper": f.paper_id, "fig": os.path.basename(f.figure_path),
                "cloud_ok": ok, "summary_len": len(summary or ""),
                "secs": round(time.time() - t0, 1),
            })
        except Exception as e:
            log.append({"paper": f.paper_id, "error": str(e)[:300], "secs": round(time.time() - t0, 1)})


def _feat_d(n, log):
    """被引情感云端复核：强制低置信阈值=1.0 保证云端路径触发，写 cloud_recheck 列。"""
    from mock_api.database import SessionLocal
    from mock_api.models import CitationSentiment, Paper
    from mock_api.crud import analysis as analysis_crud
    from mock_api.settings import get_settings

    # 强制每次分类都触发云端复核（验证链路），测完恢复
    st = get_settings()
    old_thr = getattr(st, "sentiment_recheck_low_conf", 0.6)
    try:
        st.sentiment_recheck_low_conf = 1.0
    except Exception:
        os.environ["PAPERFORGE_SENTIMENT_RECHECK_LOW_CONF"] = "1.0"

    db = SessionLocal()
    # 选「在语料中真实被引」的目标：库里 citations>0 的论文（如 Attention / GPT-3 /
    # ViT / LoRA …）在其它论文 full_text 中必被提及，保证 extract 能找到引用上下文、
    # 强制低置信阈值下云端复核写路径被真正触发。优先取被引次数最高的前 n 篇。
    chosen = (
        db.query(Paper)
        .filter(Paper.full_text.isnot(None))
        .filter(Paper.citations.isnot(None))
        .filter(Paper.citations > 0)
        .order_by(Paper.citations.desc())
        .limit(n)
        .all()
    )
    if len(chosen) < n:
        extra = (
            db.query(Paper)
            .filter(Paper.full_text.isnot(None))
            .filter(Paper.id.notin_([p.id for p in chosen]))
            .order_by(Paper.id)
            .limit(n - len(chosen))
            .all()
        )
        chosen = chosen + extra
    db.close()
    for t in chosen:
        t0 = time.time()
        try:
            db2 = SessionLocal()
            res = analysis_crud.extract_citation_sentiments_for_target(db2, t.id, max_candidates=50)
            db2.commit()
            rows = db2.query(CitationSentiment).filter(CitationSentiment.target_paper_id == t.id).all()
            n_recheck = sum(1 for r in rows if r.cloud_recheck)
            log.append({
                "target": t.id,
                "processed": res.get("processed"), "saved": res.get("saved"),
                "cloud_recheck_rows": n_recheck,
                "secs": round(time.time() - t0, 1),
            })
            db2.close()
        except Exception as e:
            log.append({"target": t.id, "error": str(e)[:300], "secs": round(time.time() - t0, 1)})

    try:
        st.sentiment_recheck_low_conf = old_thr
    except Exception:
        pass


def _feat_e(n, log):
    """GLM 编排：阶段0/2 云端视觉 + 阶段4 云端 4.7 复核。后台子进程跑（隔离崩溃）。"""
    from mock_api.database import SessionLocal
    from mock_api.models import PaperFigure

    db = SessionLocal()
    pids = [r[0] for r in db.query(PaperFigure.paper_id).distinct().limit(300).all()]
    chosen = []
    for pid in pids:
        figs = db.query(PaperFigure).filter(
            PaperFigure.paper_id == pid, PaperFigure.figure_path.isnot(None)
        ).all()
        if any(os.path.exists(f.figure_path) for f in figs):
            chosen.append(pid)
        if len(chosen) >= n:
            break
    db.close()

    env = dict(os.environ)
    env["PAPERFORGE_ORCH_LOCAL"] = "1"  # 规划/收敛走本地 8080（无限流），视觉+4.7复核仍云端
    py = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
    for pid in chosen:
        t0 = time.time()
        try:
            proc = subprocess.run(
                [py, "scripts/run_glm_orchestrate.py", pid],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=900,
            )
            out = proc.stdout + proc.stderr
            log.append({
                "paper": pid, "rc": proc.returncode,
                "stages_seen": out.count("阶段"),
                "cloud_calls": out.count("glm") + out.count("GLM"),
                "secs": round(time.time() - t0, 1),
                "tail": out[-300:].replace("\n", " "),
            })
        except subprocess.TimeoutExpired:
            log.append({"paper": pid, "error": "timeout 900s", "secs": 900})
        except Exception as e:
            log.append({"paper": pid, "error": str(e)[:300], "secs": round(time.time() - t0, 1)})


FEATURES = {"A": _feat_a, "B": _feat_b, "C": _feat_c, "D": _feat_d, "E": _feat_e}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature", default="all", help="A|B|C|D|E|all")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--probe", action="store_true", help="每功能 1 篇")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    n = 1 if args.probe else args.n
    feats = ["A", "B", "C", "D", "E"] if args.feature == "all" else [args.feature.upper()]

    _install_so_capture()

    # 触发 schema 迁移（_migrate_schema 仅在 init_db 时跑；脚本直接调 SessionLocal 不会触发，
    # 会导致 citation_sentiments.cloud_recheck 列缺失）。显式调用以补齐列。
    try:
        from mock_api.database import init_db

        init_db()
        print("[harness] init_db() 完成（schema 迁移已应用）", flush=True)
    except Exception as e:
        print(f"[harness] init_db 警告: {e}", flush=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = args.out or os.path.join("deliverables", f"feature_test_{ts}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)

    results = {}
    for f in feats:
        print(f"\n===== Feature {f} (n={n}) =====", flush=True)
        log = []
        t0 = time.time()
        FEATURES[f](n, log)
        results[f] = {"n_requested": n, "n_done": len(log), "items": log,
                      "elapsed_secs": round(time.time() - t0, 1)}
        # 实时落盘，防中途崩溃丢结果
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=2)
        # 打印进度
        ok = sum(1 for it in log if "error" not in it)
        print(f"  done {ok}/{len(log)} ; elapsed {results[f]['elapsed_secs']}s", flush=True)
        for it in log[:3]:
            print("   ", it, flush=True)

    # 汇总
    print("\n===== SUMMARY =====", flush=True)
    for f in feats:
        items = results[f]["items"]
        errs = [it for it in items if "error" in it]
        print(f"Feature {f}: {len(items)} items, {len(errs)} errors", flush=True)
    print(f"results -> {out}", flush=True)


if __name__ == "__main__":
    main()
