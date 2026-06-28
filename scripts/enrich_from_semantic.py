"""回填脚本：从 Semantic Scholar 拉取论文元数据（引用数、影响力、领域）。

B2：批量富化已入库论文的 influential_citations 与 fields_of_study 字段。

使用方式：
    python -m scripts.enrich_from_semantic            # 仅富化未处理的论文
    python -m scripts.enrich_from_semantic --force    # 强制重新富化所有论文
    python -m scripts.enrich_from_semantic --limit 5  # 仅处理前 5 篇（测试用）

断点续传：
    通过 papers.influential_citations 字段实现 —— 非 None 视为已处理（跳过）。
    API 失败时保持 NULL，下次运行会重试。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 确保能导入 mock_api 包（脚本位于项目根的 scripts/ 下）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mock_api.database import SessionLocal, init_db  # noqa: E402
from mock_api.models import Paper as PaperORM  # noqa: E402
from mock_api.crud import enrich_paper_from_semantic  # noqa: E402

# 请求间隔（秒），避免触发 Semantic Scholar 限流
REQUEST_INTERVAL = 1.5


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

    # 1. 初始化数据库（建表 + 迁移）
    init_db()

    db = SessionLocal()
    try:
        # 2. 查询待富化论文
        query = db.query(PaperORM)
        if not force:
            # 仅处理 influential_citations 为空的论文（断点续传）
            query = query.filter(PaperORM.influential_citations.is_(None))
        papers = query.all()

        if limit:
            papers = papers[:limit]

        print(f"待富化论文：{len(papers)} 篇")
        if not papers:
            print("无待富化论文（使用 --force 强制重新富化）")
            return

        success = 0
        skipped = 0
        failed = 0
        for i, p in enumerate(papers, 1):
            print(f"\n[{i}/{len(papers)}] {p.id} - {p.title[:60]}")
            try:
                ok = enrich_paper_from_semantic(db, p.id, force=force)
                if ok:
                    success += 1
                    # 重新查询以显示富化结果
                    db.refresh(p)
                    print(
                        f"  [成功] 引用={p.citations}, "
                        f"影响力={p.influential_citations}, "
                        f"领域={p.fields_of_study}"
                    )
                else:
                    # 可能是已富化（跳过）或 API 失败
                    if p.influential_citations is not None:
                        skipped += 1
                        print(f"  [跳过] 已富化")
                    else:
                        failed += 1
                        print(f"  [失败] API 不可用或论文未找到")
            except KeyboardInterrupt:
                print("\n[中断] 退出...")
                return
            except Exception as e:
                print(f"  [失败] 未预期错误：{e}")
                failed += 1

            # 礼貌延迟，避免限流
            if i < len(papers):
                time.sleep(REQUEST_INTERVAL)

        print(f"\n完成：成功 {success}，跳过 {skipped}，失败 {failed}，总计 {len(papers)}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
