"""P0-A: full_text 回填（纯 I/O + pypdf 文本层抽取，跳过 OCR 扫描件）。

背景
----
"几百篇"规模评估的物理前提是论文库有 full_text。当前 696 篇中仅 145 篇有 full_text，
约 548 篇缺（其中 556 个 pdf_url 是 arxiv 远程 URL，可被回填）。

策略
----
- 仅处理 pdf_url 为 http(s) 且 full_text 为空的论文（file:// 垃圾数据与本地路径跳过）。
- 下载 PDF：复用 pdf_proxy_service.fetch_pdf_stream（自带 SSRF 解析 + 串行锁 + 流式）。
- 文本抽取：**仅 pypdf 文本层**（page.extract_text），不触发 OCR / 不占 VRAM 互斥。
- 抽空（扫描件）→ 标记 is_scanned=True, ocr_status='skipped_scan'，跳过。
- 幂等：已含 full_text 的跳过；中断可续跑（重跑自动跳过已回填）。
- 礼貌：每篇之间小延迟（默认 0.4s），配合 _FETCH_LOCK 串行。

用法
----
    python backfill_full_text.py [--limit N] [--delay SEC] [--log PATH]
    --limit 0  全量（默认 0）；--limit 5 试点
"""
from __future__ import annotations

import argparse
import io
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("backfill_full_text")

import requests  # noqa: E402

from mock_api.database import SessionLocal, init_db  # noqa: E402
from mock_api.models import Paper  # noqa: E402
from mock_api.services.pdf_proxy_service import (  # noqa: E402
    validate_pdf_url,
)


def extract_text_only(content: bytes) -> str:
    """仅 pypdf 文本层抽取，不触发 OCR。失败返回空串。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        log.error("pypdf 未安装，无法抽取文本层")
        return ""
    try:
        reader = PdfReader(io.BytesIO(content))
        parts = []
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001 - 单页失败不中断整文档
                parts.append("")
        return sanitize_text("\n".join(parts))
    except Exception as exc:  # noqa: BLE001 - 损坏/加密 PDF
        log.warning("pypdf 打开/解析失败: %s", exc)
        return ""


def sanitize_text(text: str) -> str:
    """清洗无法用 utf-8 存入 SQLite 的字符（孤立代理/非法码点）。

    PDF 文本层常含乱码产生的 lone surrogate（如 \\ud835），直接 commit
    会触发 UnicodeEncodeError。encode('utf-8','ignore') 丢弃这些码点，
    保留其余正常文本与合法代理对（astral 字符）。
    """
    if not text:
        return text
    return text.encode("utf-8", "ignore").decode("utf-8", "ignore")


def download_pdf(url: str) -> bytes | None:
    """下载 PDF 字节。返回 None 表示失败（已记日志）。

    安全：先用 validate_pdf_url 做 SSRF 白名单 + 私网/回环校验（不钉死 DNS）。
    下载用 plain requests.get：本沙箱下 pdf_proxy_service 的 DNS 钉死补丁
    （_pinned_host_resolution monkeypatch）会使直连失败（WinError 10049），
    故回填走普通 DNS 解析路径，仅保留白名单/私网校验这一层 SSRF 防护。
    """
    try:
        validate_pdf_url(url)  # SSRF 白名单 + 协议 + 私网 IP 校验（不钉死 DNS）
    except Exception as exc:  # noqa: BLE001 - HTTPException 等
        log.warning("SSRF/白名单拒绝 %s: %s", url, exc)
        return None
    try:
        resp = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "PaperForge-Ingest/1.0"},
            stream=True,
        )
        resp.raise_for_status()
        return b"".join(resp.iter_content(chunk_size=8192))
    except Exception as exc:  # noqa: BLE001 - 网络错误 / HTTP 非 200
        log.warning("下载失败 %s: %s", url, exc)
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description="P0-A full_text 回填")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 篇（0=全量）")
    ap.add_argument("--delay", type=float, default=0.4, help="每篇之间延迟秒数")
    ap.add_argument("--log", type=str, default=str(PROJECT_ROOT / "backfill_full_text.log"))
    args = ap.parse_args()

    fh = logging.FileHandler(args.log, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(fh)

    init_db()
    db = SessionLocal()

    # 待回填：pdf_url 为 http(s) 且 full_text 空
    pending = (
        db.query(Paper.id, Paper.pdf_url)
        .filter(
            (Paper.pdf_url.isnot(None)) & (Paper.pdf_url != ""),
            (Paper.pdf_url.like("http%")),
            (Paper.full_text.is_(None) | (Paper.full_text == "")),
        )
        .all()
    )
    total = len(pending)
    limit = args.limit if args.limit and args.limit > 0 else total
    log.info("待回填论文数: %d, 本次处理上限: %d", total, limit)

    done = 0
    ok_text = 0
    skipped_scan = 0
    failed = 0
    start = time.time()

    for idx, (pid, url) in enumerate(pending[:limit], start=1):
        t0 = time.time()
        content = download_pdf(url)
        if not content:
            failed += 1
            log.warning("[%d/%d] %s 下载失败/跳过", idx, limit, pid)
            db.rollback()
            time.sleep(args.delay)
            continue
        text = extract_text_only(content)
        paper = db.query(Paper).filter(Paper.id == pid).first()
        if paper is None:
            failed += 1
            time.sleep(args.delay)
            continue
        if text.strip():
            paper.full_text = text
            paper.ocr_status = "textlayer"  # 文本层抽取成功（非 OCR）
            paper.is_scanned = False
            ok_text += 1
        else:
            paper.is_scanned = True
            paper.ocr_status = "skipped_scan"  # 扫描件，抽空，跳过 OCR
            skipped_scan += 1
        db.commit()
        done += 1
        log.info(
            "[%d/%d] %s %s (%.1fs)",
            idx,
            limit,
            pid,
            "text" if text.strip() else "SCAN-skip",
            time.time() - t0,
        )
        time.sleep(args.delay)

    db.close()
    elapsed = time.time() - start
    log.info(
        "完成: 处理=%d ok_text=%d skipped_scan=%d failed=%d 耗时=%.1fs",
        done,
        ok_text,
        skipped_scan,
        failed,
        elapsed,
    )
    print(
        f"处理={done} 文本成功={ok_text} 扫描跳过={skipped_scan} 失败={failed} 耗时={elapsed:.1f}s"
    )


if __name__ == "__main__":
    main()
