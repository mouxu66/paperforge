"""回填脚本：为已入库论文提取 PDF 全文并更新 FTS5 索引。

B1：将已入库论文的 PDF 全文内容纳入 FTS5 索引，提升搜索/问答检索颗粒度。

使用方式：
    python -m scripts.index_pdf_fulltext            # 仅处理 full_text 为空的论文
    python -m scripts.index_pdf_fulltext --force    # 强制重新提取所有论文
    python -m scripts.index_pdf_fulltext --limit 5  # 仅处理前 5 篇（测试用）

断点续传：
    脚本通过 papers.full_text 字段实现断点续传 ——
    full_text 非空视为已处理（跳过），为空则待处理。
    PDF 下载或提取失败时保持 full_text 为 NULL，下次运行会重试。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# 确保能导入 mock_api 包（脚本位于项目根的 scripts/ 下）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402
from sqlalchemy import text  # noqa: E402

from mock_api.database import SessionLocal, init_db  # noqa: E402
from mock_api.models import Paper as PaperORM  # noqa: E402
from mock_api.pdf_parser import extract_full_text  # noqa: E402

# 进度状态文件（记录已成功处理的 paper_id，便于精确断点续传）
PROGRESS_FILE = Path(__file__).resolve().parent / ".pdf_fulltext_progress.json"

# PDF 下载配置
DOWNLOAD_TIMEOUT = 30  # 秒
DOWNLOAD_CHUNK = 8192  # 字节
MAX_PDF_SIZE = 50 * 1024 * 1024  # 50MB，防止超大文件耗尽内存


def load_progress() -> set[str]:
    """加载已成功处理的 paper_id 集合。"""
    if not PROGRESS_FILE.exists():
        return set()
    try:
        return set(json.loads(PROGRESS_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_progress(ids: set[str]) -> None:
    """持久化已处理的 paper_id 集合。"""
    try:
        PROGRESS_FILE.write_text(
            json.dumps(sorted(ids), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[警告] 进度文件写入失败：{e}")


def download_pdf(url: str) -> bytes:
    """下载 PDF 二进制内容。

    Raises:
        requests.RequestException: 网络错误
        ValueError: 非 PDF 内容或文件过大
    """
    resp = requests.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True)
    resp.raise_for_status()

    content_type = resp.headers.get("Content-Type", "").lower()
    if "pdf" not in content_type and not url.lower().endswith(".pdf"):
        # arXiv 可能返回 HTML（如需要重定向），不严格阻断但给出警告
        print(f"  [警告] Content-Type={content_type}，仍尝试解析")

    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK):
        if chunk:
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_PDF_SIZE:
                raise ValueError(f"PDF 超过大小上限（{MAX_PDF_SIZE // 1024 // 1024}MB）")
    return b"".join(chunks)


def update_fts_index(db, paper_id: str) -> None:
    """更新 FTS5 索引中指定论文的 full_text 列。

    使用 INSERT OR REPLACE 覆盖整行，确保 full_text 同步到索引。
    """
    p = db.query(PaperORM).filter(PaperORM.id == paper_id).first()
    if not p:
        return
    authors_str = " ".join(p.authors or [])
    full_text_value = p.full_text or ""
    db.execute(
        text(
            "INSERT OR REPLACE INTO paper_fts(paper_id, title, abstract, authors, full_text) "
            "VALUES (:pid, :title, :abstract, :authors, :full_text)"
        ),
        {
            "pid": paper_id,
            "title": p.title,
            "abstract": p.abstract or "",
            "authors": authors_str,
            "full_text": full_text_value,
        },
    )
    db.commit()


def process_one(db, paper: PaperORM, progress: set[str]) -> bool:
    """处理单篇论文：下载 PDF → 提取全文 → 更新数据库 + FTS。

    Returns:
        True 表示成功（full_text 非空），False 表示失败或全文为空。
    """
    if not paper.pdf_url:
        print(f"  [跳过] {paper.id}: 无 pdf_url")
        return False

    print(f"  下载 PDF: {paper.pdf_url}")
    try:
        content = download_pdf(paper.pdf_url)
    except Exception as e:
        print(f"  [失败] 下载错误：{e}")
        return False

    print(f"  提取全文（{len(content) // 1024}KB）...")
    full_text = extract_full_text(content)
    if not full_text:
        print(f"  [失败] 全文提取为空（可能为扫描版 PDF）")
        return False

    # 更新 papers.full_text
    paper.full_text = full_text
    db.commit()

    # 更新 FTS5 索引
    update_fts_index(db, paper.id)

    print(f"  [成功] 提取 {len(full_text)} 字符")
    return True


def main() -> None:
    force = "--force" in sys.argv
    limit: int | None = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        if idx + 1 < len(sys.argv):
            try:
                limit = int(sys.argv[idx + 1])
            except ValueError:
                pass

    # 1. 初始化数据库（建表 + 迁移 + FTS5）
    init_db()

    progress = load_progress()

    db = SessionLocal()
    try:
        # 2. 查询待处理论文
        query = db.query(PaperORM)
        if not force:
            # 仅处理 full_text 为空的论文（断点续传）
            query = query.filter(PaperORM.full_text.is_(None))
        query = query.filter(PaperORM.pdf_url != "")
        papers = query.all()

        if limit:
            papers = papers[:limit]

        print(f"待处理论文：{len(papers)} 篇")
        if not papers:
            print("无待处理论文（使用 --force 强制重新提取）")
            return

        success = 0
        failed = 0
        for i, p in enumerate(papers, 1):
            print(f"\n[{i}/{len(papers)}] {p.id} - {p.title[:60]}")
            try:
                ok = process_one(db, p, progress)
                if ok:
                    success += 1
                    progress.add(p.id)
                    # 每 5 篇保存一次进度
                    if success % 5 == 0:
                        save_progress(progress)
                else:
                    failed += 1
            except KeyboardInterrupt:
                print("\n[中断] 保存进度后退出...")
                save_progress(progress)
                return
            except Exception as e:
                print(f"  [失败] 未预期错误：{e}")
                failed += 1

            # 礼貌延迟，避免被 arXiv 限流
            if i < len(papers):
                time.sleep(1)

        # 最终保存进度
        save_progress(progress)

        print(f"\n完成：成功 {success}，失败 {failed}，总计 {len(papers)}")
        print(f"进度文件：{PROGRESS_FILE}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
