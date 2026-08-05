"""为 PeerRead reject 论文下载 PDF + 跑 figure pass 预处理。

流程：
  1. 从 peerread_index.json 取 N 篇 reject 论文（arxiv 子集，PDF 好下载）
  2. 从 arxiv 下载 PDF 到 uploads/pr_reject_{stem}.pdf
  3. 对每篇跑 pipeline_figure_understanding.run_pipeline（VRAM 自理）
  4. 落库到 PaperFigure 表（paper_id = pr_{stem}）

用法：
    python scripts/calibration/prepare_reject_figures.py --limit 8          # 下载 + 跑 8 篇
    python scripts/calibration/prepare_reject_figures.py --download-only 8  # 只下载
    python scripts/calibration/prepare_reject_figures.py --figure-only      # 只跑 figure pass（PDF 已下载）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import pipeline 之前
os.environ.setdefault("LLAMA_CPP_N_GPU_LAYERS", "99")

IDX = ROOT / "deliverables" / "peerread_index.json"
UPLOADS = ROOT / "uploads"

# arxiv PDF 下载基础 URL
ARXIV_PDF_BASE = "https://arxiv.org/pdf/"


def select_reject_papers(n: int) -> list[dict]:
    """从 PeerRead index 取 N 篇 arxiv reject 论文（与 accept 同分布）。

    去重：跳过 PaperFigure 表已有图证据的论文（避免重复跑 figure pass）。
    """
    idx = json.loads(IDX.read_text(encoding="utf-8"))
    rej = [j for j in idx if not j["accepted"] and j["venue"].startswith("arxiv.cs")]

    # 去重：查 PaperFigure 表，跳过已有图证据的论文
    import sqlite3
    db = ROOT / "mock_api" / "paperforge_mock.db"
    if db.exists():
        conn = sqlite3.connect(str(db))
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT paper_id FROM paper_figures WHERE paper_id LIKE 'pr_%'")
        existing_pids = set(r[0] for r in cur.fetchall())
        conn.close()
        before = len(rej)
        rej = [j for j in rej if f"pr_{j['stem']}" not in existing_pids]
        print(f"  [去重] 跳过 {before - len(rej)} 篇已有图证据的论文")

    # 优先 ai/lg（有实验图），cl 语言学论文多无图
    pref = ["arxiv.cs.ai_2007-2017", "arxiv.cs.lg_2007-2017", "arxiv.cs.cl_2007-2017"]
    rej.sort(key=lambda j: pref.index(j["venue"]) if j["venue"] in pref else 99)
    return rej[:n]


def download_pdf(stem: str, out_path: Path) -> bool:
    """从 arxiv 下载 PDF。返回是否成功。"""
    if out_path.exists() and out_path.stat().st_size > 10000:
        print(f"    [skip] 已存在: {out_path.name} ({out_path.stat().st_size} bytes)")
        return True
    url = f"{ARXIV_PDF_BASE}{stem}"
    print(f"    [download] {url} -> {out_path.name}")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PaperForge/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        if len(data) < 10000:
            print(f"    [warn] PDF 过小 ({len(data)} bytes)，可能下载失败")
            return False
        out_path.write_bytes(data)
        print(f"    [ok] {len(data)} bytes")
        return True
    except Exception as e:
        print(f"    [fail] {e}")
        return False


def run_figure_pass(paper_id: str, title: str, pdf_path: Path) -> int:
    """对单篇 PDF 跑 figure pass（使用 Qwen3-VL-4B 视觉模型）。

    返回入库图数（0=失败）。
    """
    import urllib.request as _urllib

    def _vision_alive() -> bool:
        try:
            req = _urllib.Request("http://localhost:8082/v1/models", headers={"User-Agent": "x"})
            with _urllib.urlopen(req, timeout=3) as r:
                return r.status == 200
        except Exception:
            return False

    print(f"    [vision] Qwen3-VL-4B 8082: {'ALIVE' if _vision_alive() else 'DOWN'}", flush=True)

    if not _vision_alive():
        print(f"    [err] 8082 Qwen3-VL-4B 未启动，请先启动", flush=True)
        return 0

    from mock_api.database import SessionLocal
    from scripts.pipeline_figure_understanding import run_pipeline

    pdf_bytes = pdf_path.read_bytes()
    print(f"    [pdf] {len(pdf_bytes)} bytes", flush=True)

    try:
        print(f"    [pipeline] 开始 run_pipeline（Qwen3-VL-4B only）...", flush=True)
        with SessionLocal() as db:
            rc = run_pipeline(
                paper_id=paper_id,
                title=title,
                pdf_bytes=pdf_bytes,
                db=db,
                vram_bracket=False,  # 已废弃，无操作
            )
        print(f"    [pipeline] rc={rc}", flush=True)
        return rc  # 0=成功，1=失败（与 run_pipeline 一致）
    finally:
        pass  # 无 VRAM 清理


def check_figures_in_db(paper_id: str) -> int:
    """查 PaperFigure 表里该论文已有多少图。"""
    import sqlite3
    conn = sqlite3.connect(str(ROOT / "mock_api" / "paperforge_mock.db"))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM paper_figures WHERE paper_id = ?", (paper_id,))
    n = cur.fetchone()[0]
    conn.close()
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=8, help="下载 + 跑 figure pass 的论文数")
    ap.add_argument("--download-only", action="store_true", help="只下载 PDF，不跑 figure pass")
    ap.add_argument("--figure-only", action="store_true", help="只跑 figure pass（PDF 已下载）")
    ap.add_argument("--stem", type=str, default=None, help="只跑指定 stem（调试用）")
    args = ap.parse_args()

    UPLOADS.mkdir(parents=True, exist_ok=True)

    # 选论文
    if args.stem:
        idx = json.loads(IDX.read_text(encoding="utf-8"))
        papers = [j for j in idx if j["stem"] == args.stem]
        if not papers:
            print(f"[err] stem {args.stem} 不在 peerread_index.json")
            return
    else:
        papers = select_reject_papers(args.limit)
    print(f"[准备] 选定 {len(papers)} 篇 reject 论文")
    for j in papers:
        print(f"  {j['stem']} | {j['venue']} | {j['title'][:50]}")

    # 下载 PDF
    downloaded = []
    for j in papers:
        stem = j["stem"]
        pdf_path = UPLOADS / f"pr_{stem}.pdf"
        print(f"\n[{stem}] 下载 PDF...")
        if download_pdf(stem, pdf_path):
            downloaded.append((j, pdf_path))

    if args.download_only:
        print(f"\n[done] 下载完成: {len(downloaded)}/{len(papers)}")
        return

    if not downloaded:
        print("[err] 无可用 PDF，终止")
        return

    # 数据库初始化一次性完成，避免每个 paper 重复 init_db 导致并发锁
    from mock_api.database import init_db

    init_db()

    # 跑 figure pass
    if not args.figure_only and not args.download_only:
        # 默认流程：下载 + figure pass
        pass

    print(f"\n[figure pass] 开始处理 {len(downloaded)} 篇...")
    ok = 0
    failed = 0
    for i, (j, pdf_path) in enumerate(downloaded, 1):
        stem = j["stem"]
        paper_id = f"pr_{stem}"
        # 续跑检查
        existing = check_figures_in_db(paper_id)
        if existing > 0:
            print(f"\n[{i}/{len(downloaded)}] {stem} 已有 {existing} 图，跳过 figure pass")
            ok += 1
            continue
        print(f"\n[{i}/{len(downloaded)}] {stem} figure pass...")
        try:
            rc = run_figure_pass(paper_id, j["title"], pdf_path)
            if rc == 0:
                n = check_figures_in_db(paper_id)
                print(f"    [ok] 入库 {n} 张图")
                ok += 1
            else:
                print(f"    [fail] run_pipeline 返回 {rc}")
                failed += 1
        except Exception as e:
            print(f"    [fail] 异常: {e}")
            failed += 1
        # 间隔，避免 arxiv/VRAM 压力
        time.sleep(2)

    print(f"\n[done] 成功 {ok}/{len(downloaded)}，失败 {failed}")


if __name__ == "__main__":
    main()
