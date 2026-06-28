"""遍历所有论文，为摘要生成向量并写入 PaperEmbedding 表。

使用方式：
    python -m scripts.embed_papers

向量模型：fastembed + BAAI/bge-small-en-v1.5（384 维，纯 CPU 推理，无 PyTorch）。
首次运行时会自动下载模型（~130MB）到 ~/.cache/fastembed/。
后续运行会跳过已有向量（除非加 --force 强制重新生成）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 确保能导入 mock_api 包（脚本位于项目根的 scripts/ 下）
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mock_api.database import SessionLocal, init_db  # noqa: E402
from mock_api.models import Paper as PaperORM, PaperEmbedding as PaperEmbeddingORM  # noqa: E402
from mock_api.semantic_search import (  # noqa: E402
    EMBEDDING_DIM,
    MODEL_NAME,
    embed_batch,
    is_available,
)


def embed_all_papers(force: bool = False) -> None:
    """为所有论文生成向量。

    Args:
        force: True 时强制重新生成所有向量（包括已有的）。
    """
    # 1. 确保数据库已初始化（建表 + 种子数据）
    init_db()

    # 2. 检查 fastembed 是否可用
    if not is_available():
        print("[错误] fastembed 未安装或模型加载失败。")
        print("       请先安装依赖：pip install fastembed>=0.2.0 numpy>=1.24.0")
        print(f"       首次运行会自动下载 {MODEL_NAME} 模型（~130MB）到 ~/.cache/fastembed/。")
        print("       未联网时将降级为 FTS5 关键词检索（无需向量）。")
        sys.exit(1)

    db = SessionLocal()
    try:
        # 3. 取所有论文
        papers = db.query(PaperORM).all()
        print(f"共 {len(papers)} 篇论文")

        # 4. 过滤已有向量（除非 force）
        if not force:
            existing_ids = {
                r[0]
                for r in db.query(PaperEmbeddingORM.paper_id).all()
            }
            pending = [p for p in papers if p.id not in existing_ids]
        else:
            pending = papers

        if not pending:
            print("所有论文向量已生成，跳过（使用 --force 强制重新生成）")
            return

        print(f"待生成向量：{len(pending)} 篇")

        # 5. 批量编码（比逐条快得多）
        # 用「标题 + 摘要」拼接作为编码文本，信息更完整
        texts = [
            f"{p.title}. {p.abstract or ''}".strip()
            for p in pending
        ]
        print(f"正在用 {MODEL_NAME} 编码（维度={EMBEDDING_DIM}）...")
        vectors = embed_batch(texts)
        if vectors is None:
            print("[错误] 向量编码失败，请检查模型是否正常加载。")
            sys.exit(1)

        # 6. 写入数据库
        from mock_api.crud import upsert_paper_embedding

        success = 0
        for paper, vec in zip(pending, vectors):
            if vec:
                upsert_paper_embedding(db, paper.id, vec)
                success += 1

        print(f"[OK] 成功生成 {success}/{len(pending)} 篇论文的向量")
        print(f"向量总数：{db.query(PaperEmbeddingORM).count()}")

    finally:
        db.close()


def main() -> None:
    force = "--force" in sys.argv
    embed_all_papers(force=force)


if __name__ == "__main__":
    main()
