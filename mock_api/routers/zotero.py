"""Zotero 文件夹监听与导入。

PR9 抽取：从 main.py 搬迁 zotero 路由（依赖 watchdog_zotero + zotero_import）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import crud
from ..database import get_db
from ..schemas import UploadedPaper, ZoteroImportRequest, ZoteroImportResponse, ZoteroWatchConfig
from ..watchdog_zotero import get_watchdog_status, start_watchdog, stop_watchdog
from ..zotero_import import MAX_ITEMS, fetch_zotero_items

router = APIRouter(tags=["zotero"])


@router.get("/api/zotero/watch-status")
def zotero_watch_status() -> dict:
    """获取 Zotero 文件夹监控当前状态。"""
    return get_watchdog_status()


@router.post("/api/zotero/watch-config")
def zotero_watch_config(payload: ZoteroWatchConfig) -> dict:
    """配置 Zotero 文件夹监控（启动/停止/切换目录）。

    示例：
        {"enabled": true, "watchDir": "/path/to/Zotero/storage"}

    返回当前状态。
    """
    enabled = payload.enabled
    watch_dir = payload.watchDir.strip()

    if enabled and watch_dir:
        ok = start_watchdog(watch_dir)
        if not ok:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"无法监控目录: {watch_dir}。请确认目录存在且 watchdog 已安装 "
                    "(pip install watchdog>=3.0.0)"
                ),
            )
    else:
        stop_watchdog()

    return get_watchdog_status()


@router.post("/api/zotero/import", response_model=ZoteroImportResponse)
def zotero_import(req: ZoteroImportRequest, db: Session = Depends(get_db)) -> ZoteroImportResponse:
    """从 Zotero 用户或群组库导入论文到 PaperForge。

    - 支持个人用户（userId）和群组库（userId + apiKey）
    - 单次最多导入 100 篇（Zotero API 限制）
    - 通过 DOI 去重，调用 import_external_paper 执行 INSERT OR IGNORE
    - 失败的条目记录在 results 中供前端展示
    """
    try:
        items = fetch_zotero_items(req.userId, req.apiKey)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Zotero API 请求失败：{e}") from e

    total_fetched = len(items)
    results: list[UploadedPaper] = []
    success_count = 0
    fail_count = 0

    for item in items[:MAX_ITEMS]:
        title = item["title"]
        try:
            # 用 DOI 作为 paper_id 去重，无 DOI 时用标题哈希
            paper_id = item["doi"] or f"zotero_{abs(hash(title)) % (10**10)}"
            crud.import_external_paper(
                db,
                paper_id=paper_id,
                title=title,
                authors=item["authors"],
                year=item["year"],
                abstract=item["abstract"],
                pdf_url=item.get("url", ""),
                source="zotero",
                category="interdisciplinary",
                tags=[],
            )
            results.append(
                UploadedPaper(
                    id=paper_id,
                    title=title,
                    authors=item["authors"],
                    year=item["year"],
                    abstract=item["abstract"],
                    source="zotero",
                    success=True,
                )
            )
            success_count += 1
        except Exception as e:
            results.append(
                UploadedPaper(
                    id="",
                    title=title,
                    authors=item["authors"],
                    year=item["year"],
                    abstract=item["abstract"],
                    source="zotero",
                    success=False,
                    error=f"导入失败：{e}",
                )
            )
            fail_count += 1

    return ZoteroImportResponse(
        results=results,
        successCount=success_count,
        failCount=fail_count,
        totalFetched=total_fetched,
    )
