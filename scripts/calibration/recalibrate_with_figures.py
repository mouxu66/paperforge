"""M0 含图偏移重校准 · 编排驱动（阶段2：图证据上线后刷新 default / peerread 偏移）。

前置（代码已就绪）：
  - M0 抽取层 + 消费层已接入：run_pipeline 将 Qwen3-VL-4B 视觉摘要写入 qwen_summary；
    DEPTH QF 节点读取 paper_figures.qwen_summary，由 DEPTH_FIGURE_EVIDENCE_ENABLED 开关。
  - 后端 /api/admin/figures/understand-batch 是【独立小批量 pass】（VRAM 安全，不与 bulk DEPTH 内联）。

本脚本只做【编排】，绝不自己跑 OCR：
  prepare     把 PeerRead sample 论文 ingest 成 Paper 记录（id=pr_<stem>），arxiv stem 填 pdf_url。
  fetch-pdfs  为 arxiv stem 下载原始 PDF 到 uploads/pr_<stem>.pdf（figure worker 需要本地 PDF）。
  figures     通过后端 admin 端点派发 figure_understanding（后端 worker 跑 OCR+VLM），轮询 DB 直到 qwen_summary 就绪。
  run         调 scripts/peerread_rescore.py run --with-figures --baseline，产出含图 offset=0 基线 jsonl。
  scan        调 scripts/offset_scan.py 给出推荐 Δ（确定性加法扫描，无需重跑 LLM）。
  status      报告 sample 中已具备 qwen_summary 的论文占比（图覆盖率）。

关键约束 / 已知限制：
  - 仅 arxiv stem（150/200）能拿到原始 PDF 做图抽取；iclr_2017（50/200）无原始 PDF 来源，
    其 DEPTH 走文本 QF 兜底（figure_consistency 视为无图）。
  - 图抽取期间 Qwen(8080) 被后端 VRAM 调度临时释放，跑完自动拉回（设计内）。
  - 全量 200 篇图抽取是小时级作业（>280s/篇量级），建议先用 --limit 跑小批量 pilot 验证端到端。

用法：
  python scripts/recalibrate_with_figures.py prepare [--dry-run]
  python scripts/recalibrate_with_figures.py fetch-pdfs [--dry-run] [--limit N]
  python scripts/recalibrate_with_figures.py figures [--dry-run] [--limit N] [--base-url URL] [--token T]
  python scripts/recalibrate_with_figures.py run [--out baseline_fig.jsonl]
  python scripts/recalibrate_with_figures.py scan --in baseline_fig.jsonl --source-name peerread
  python scripts/recalibrate_with_figures.py status
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from peerread_rescore import SAMPLE, ROOT, extract_full_text  # reuse text extractor + sample

ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}$")

# 后端 base url / admin token（dev 态 loopback 可免 token）
BASE_URL = os.environ.get("PAPERFORGE_BASE_URL", "http://127.0.0.1:8770")
ADMIN_TOKEN = os.environ.get("PAPERFORGE_ADMIN_TOKEN")  # None -> 依赖 dev loopback 免 token

UPLOADS = ROOT / "uploads"


def is_arxiv(stem: str) -> bool:
    return bool(ARXIV_RE.match(stem))


def load_sample() -> list[dict]:
    if not SAMPLE.exists():
        raise SystemExit("peerread_sample.json 不存在，先跑 peerread_rescore.py sample")
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def _post(path: str, payload: dict, token: str | None = ADMIN_TOKEN, timeout: int = 30) -> dict:
    import requests

    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = requests.post(f"{BASE_URL}{path}", json=payload, headers=h, timeout=timeout)
    r.raise_for_status()
    return r.json()


# ───────────────────────── prepare ─────────────────────────
def cmd_prepare(args):
    sel = load_sample()
    if args.dry_run:
        arxiv = [j for j in sel if is_arxiv(j["stem"])]
        print(f"[prepare DRY-RUN] sample={len(sel)} arxiv_stems={len(arxiv)} "
              f"iclr={len(sel)-len(arxiv)}")
        print("  将 ingest 为 Paper(id=pr_<stem>)，arxiv 填 pdf_url=https://arxiv.org/pdf/<stem>")
        return
    from mock_api.database import SessionLocal
    from mock_api.models import Paper

    db = SessionLocal()
    n_new = n_skip = 0
    for j in sel:
        stem = j["stem"]
        pid = f"pr_{stem}"
        existing = db.query(Paper).filter(Paper.id == pid).first()
        if existing is not None:
            n_skip += 1
            continue
        try:
            full, abstract = extract_full_text(j["parsed_path"])
        except Exception as e:
            print(f"  [warn] {stem} 全文提取失败，跳过 ingest: {e}")
            continue
        year = int("20" + stem[:2]) if is_arxiv(stem) else 0
        paper = Paper(
            id=pid,
            title=j.get("title") or "",
            authors=json.dumps([]),
            abstract=abstract,
            full_text=full,
            year=year,
            source="peerread",
            pdf_url=f"https://arxiv.org/pdf/{stem}" if is_arxiv(stem) else "",
        )
        db.add(paper)
        n_new += 1
    db.commit()
    db.close()
    print(f"[prepare] ingest={n_new} skip(已存在)={n_skip}")


# ──────────────────────── fetch-pdfs ───────────────────────
def _download_arxiv_pdf(stem: str, timeout: int = 90) -> bytes | None:
    import requests

    url = f"https://arxiv.org/pdf/{stem}"
    try:
        r = requests.get(url, timeout=timeout,
                         headers={"User-Agent": "Mozilla/5.0 (PaperForge M0 recalibration)"})
        r.raise_for_status()
        ct = r.headers.get("Content-Type", "").lower()
        if "pdf" not in ct and len(r.content) < 2000:
            return None
        if not r.content.startswith(b"%PDF"):
            # arXiv 偶尔返回 HTML 错误页
            return None
        return r.content
    except Exception as e:
        print(f"    [warn] arxiv 下载失败 {stem}: {e}")
        return None


def cmd_fetch_pdfs(args):
    sel = load_sample()
    arxiv = [j for j in sel if is_arxiv(j["stem"])]
    if args.limit:
        arxiv = arxiv[: args.limit]
    UPLOADS.mkdir(parents=True, exist_ok=True)
    n_dl = n_skip = n_fail = 0
    for j in arxiv:
        stem = j["stem"]
        pid = f"pr_{stem}"
        dest = UPLOADS / f"{pid}.pdf"
        if dest.exists() and dest.stat().st_size > 1000:
            n_skip += 1
            continue
        if args.dry_run:
            print(f"  [DRY-RUN] would download {stem} -> {dest}")
            n_dl += 1
            continue
        data = _download_arxiv_pdf(stem)
        if not data:
            n_fail += 1
            continue
        dest.write_bytes(data)
        n_dl += 1
        print(f"  downloaded {stem} ({len(data)//1024} KB)")
    print(f"[fetch-pdfs] 新下载={n_dl} 跳过(已有)={n_skip} 失败={n_fail}")


# ──────────────────────── figures ──────────────────────────
def cmd_figures(args):
    sel = load_sample()
    arxiv = [j for j in sel if is_arxiv(j["stem"])]
    if args.limit:
        arxiv = arxiv[: args.limit]
    pids = [f"pr_{j['stem']}" for j in arxiv]
    # 仅派发已有本地 PDF 的（figure worker 不自己下载，需 uploads/<pid>.pdf）
    ready = [p for p in pids if (UPLOADS / f"{p}.pdf").exists()]
    missing = [p for p in pids if not (UPLOADS / f"{p}.pdf").exists()]
    if missing:
        print(f"[figures] {len(missing)} 个 arxiv stem 缺本地 PDF，先跑 fetch-pdfs："
              + ", ".join(missing[:5]) + ("..." if len(missing) > 5 else ""))

    if args.dry_run:
        print(f"[figures DRY-RUN] 将派发 {len(ready)} 篇 figure_understanding 到 {BASE_URL}/api/admin/figures/understand-batch")
        print("  iclr_2017 (50) 无原始 PDF，跳过图抽取，DEPTH 走文本 QF 兜底。")
        return

    if not ready:
        print("[figures] 无就绪 PDF，退出。先跑 fetch-pdfs。")
        return

    # 派发（后端 worker 跑 OCR+VLM，VRAM 安全）
    try:
        resp = _post("/api/admin/figures/understand-batch",
                     {"paper_ids": ready, "skip_existing": True})
    except Exception as e:
        print(f"[figures] 派发失败（后端未起 / 鉴权失败？）：{e}")
        return
    dispatched = resp.get("dispatched", [])
    skipped = resp.get("skipped", [])
    failed = resp.get("failed", [])
    print(f"[figures] dispatched={len(dispatched)} skipped={len(skipped)} failed={len(failed)}")
    if failed:
        print("  failed:", failed[:10])

    # 轮询 DB 直到 qwen_summary 就绪（小时级，后台跑；这里给出进度并超时返回剩余）
    from mock_api.database import SessionLocal
    from mock_api.crud.figures import get_figures_by_paper

    pending = set(dispatched)
    poll = args.poll
    timeout = args.timeout
    elapsed = 0
    db = SessionLocal()
    try:
        while pending and elapsed < timeout:
            done = []
            for pid in list(pending):
                figs = get_figures_by_paper(db, pid)
                if figs and any(
                    ((f.qwen_summary or "") or (f.ocr_text or "") or (f.caption_text or "")).strip()
                    for f in figs
                ):
                    done.append(pid)
            for pid in done:
                pending.discard(pid)
            if not pending:
                break
            print(f"  [{elapsed}s] 待完成 {len(pending)}/{len(dispatched)} ...")
            time.sleep(poll)
            elapsed += poll
    finally:
        db.close()
    if pending:
        print(f"[figures] 超时仍有 {len(pending)} 篇未完成（可重跑本命令续跑）："
              + ", ".join(sorted(pending)[:5]) + "...")
    else:
        print(f"[figures] 全部 {len(dispatched)} 篇 qwen_summary 就绪。")


# ───────────────────────── run ─────────────────────────────
def cmd_run(args):
    out = args.out or str(ROOT / "deliverables" / "peerread_rescore_n200_base_fig.jsonl")
    print(f"[run] 调 peerread_rescore.py run --with-figures --baseline -> {out}")
    subprocess.run(
        [sys.executable, "scripts/peerread_rescore.py", "run",
         "--with-figures", "--baseline", "--out", out],
        check=True,
    )


# ───────────────────────── scan ────────────────────────────
def cmd_scan(args):
    print(f"[scan] 对 {args.inp} 跑 offset_scan（source={args.source_name}）")
    subprocess.run(
        [sys.executable, "scripts/offset_scan.py",
         "--in", args.inp, "--source-name", args.source_name,
         "--recommend-out", str(ROOT / "deliverables" / f"{args.source_name}_offset_recommend_fig.json")],
        check=True,
    )


# ──────────────────────── status ───────────────────────────
def cmd_status(args):
    sel = load_sample()
    from mock_api.database import SessionLocal
    from mock_api.crud.figures import get_figures_by_paper

    db = SessionLocal()
    total = len(sel)
    with_fig = 0
    try:
        for j in sel:
            pid = f"pr_{j['stem']}"
            figs = get_figures_by_paper(db, pid)
            if figs and any((f.qwen_summary or "").strip() for f in figs):
                with_fig += 1
    finally:
        db.close()
    print(f"[status] sample={total}  已具备图摘要(qwen_summary)={with_fig}  "
          f"覆盖率={with_fig/total:.1%}")


def main():
    ap = argparse.ArgumentParser(description="M0 含图偏移重校准编排驱动")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="ingest PeerRead sample 为 Paper(pr_<stem>)")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("fetch-pdfs", help="下载 arxiv stem 原始 PDF 到 uploads/")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.set_defaults(func=cmd_fetch_pdfs)

    p = sub.add_parser("figures", help="经后端 admin 端点派发 figure_understanding")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--base-url", default=BASE_URL)
    p.add_argument("--token", default=ADMIN_TOKEN)
    p.add_argument("--poll", type=int, default=30, help="轮询间隔秒")
    p.add_argument("--timeout", type=int, default=7200, help="轮询超时秒（默认2h）")
    p.set_defaults(func=cmd_figures)

    p = sub.add_parser("run", help="DEPTH 含图 offset=0 基线")
    p.add_argument("--out", default=None)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("scan", help="offset_scan 推荐 Δ")
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--source-name", default="peerread")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("status", help="图覆盖率")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
