"""PaperForge mock 后端数据库连接与初始化。

使用 SQLite + SQLAlchemy 2.x（同步引擎）。数据库文件存放在本包目录下，
首次启动时自动建表并写入种子数据，并创建 FTS5 全文索引虚拟表。
"""
from __future__ import annotations

from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker, declarative_base

# 数据库文件固定存放于 mock_api 包目录下，避免受启动 CWD 影响
DB_PATH: Path = Path(__file__).parent / "paperforge_mock.db"
DB_URL: str = f"sqlite:///{DB_PATH}"

# check_same_thread=False：FastAPI 同步路由运行在线程池中，需允许跨线程使用连接
engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False},
    echo=False,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：每个请求获取独立 Session，请求结束自动关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_fts5(db: Session) -> None:
    """创建 FTS5 虚拟表并在表为空时从 papers 表回填数据。

    设计决策：采用「独立 FTS 表」而非 content 模式。
    - papers.id 是 arXiv ID 字符串（如 '1706.03762'），不能作为 FTS5 的
      content_rowid（必须是整数）；而用 papers 隐式 rowid 做 content_rowid
      时，content 表 rowid 不连续 + content 模式 rowid 对齐机制脆弱，调试成本高。
    - 独立 FTS 表自带 title/abstract/authors 文本副本，存储开销在 24 篇量级可忽略，
      查询时直接 JOIN papers.id = paper_fts.paper_id（字符串关联），逻辑最简。
    - 同步：种子写入时由 crud 维护；后续新增论文需同步写 FTS（见 seed_if_empty）。

    FTS5 不支持 ALTER TABLE ADD COLUMN，因此当旧表缺少 authors 或 full_text
    列时需 DROP 后重建并回填数据。
    """
    # 1) 检查现有 FTS 表是否含 authors 与 full_text 列；缺任一则 DROP 重建
    schema_row = db.execute(
        text("SELECT sql FROM sqlite_master WHERE name = 'paper_fts'")
    ).first()
    schema_sql = (schema_row[0] if schema_row and schema_row[0] else "").lower()
    if schema_sql and ("authors" not in schema_sql or "full_text" not in schema_sql):
        db.execute(text("DROP TABLE paper_fts"))
        db.commit()

    # 2) 建独立 FTS 虚拟表（含 paper_id + title + abstract + authors + full_text 文本副本）
    #    authors 存为以空格连接的字符串，便于 MATCH 全文检索
    #    full_text 为 PDF 全文（可空字符串），供 B1 全文检索
    db.execute(
        text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5("
            "paper_id UNINDEXED, title, abstract, authors, full_text"
            ")"
        )
    )

    # 3) 若 FTS 表为空，从 papers 表回填（paper_id 关联 papers.id，
    #    authors 用空格连接，full_text 为空时填空字符串）
    count_row = db.execute(text("SELECT count(*) FROM paper_fts")).first()
    fts_count = count_row[0] if count_row else 0
    if fts_count == 0:
        db.execute(
            text(
                "INSERT INTO paper_fts(paper_id, title, abstract, authors, full_text) "
                "SELECT id, title, abstract, "
                "COALESCE((SELECT group_concat(value, ' ') FROM json_each(authors)), ''), "
                "COALESCE(full_text, '') "
                "FROM papers"
            )
        )

    db.commit()


def init_db() -> None:
    """建表、写入种子数据，并初始化 FTS5 全文索引。"""
    # 在函数内部导入 ORM 模型，避免模块加载期循环导入
    from . import models  # noqa: F401
    from .crud import seed_if_empty

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        _migrate_schema(db)
        seed_if_empty(db)
        _ensure_fts5(db)
    finally:
        db.close()


def _migrate_schema(db: Session) -> None:
    """轻量迁移：为已有表补充新列（SQLite ALTER TABLE ADD COLUMN）。

    create_all 只建新表不修改已有表结构，开发环境升级时需手动补充列。
    """
    # papers.full_text（B1：PDF 全文，可空）
    paper_cols = db.execute(text("PRAGMA table_info(papers)")).all()
    paper_col_names = {row[1] for row in paper_cols}
    if paper_col_names and "full_text" not in paper_col_names:
        db.execute(text("ALTER TABLE papers ADD COLUMN full_text TEXT"))
        db.commit()

    # papers.influential_citations / fields_of_study（B2：Semantic Scholar 富化）
    if paper_col_names and "influential_citations" not in paper_col_names:
        db.execute(text("ALTER TABLE papers ADD COLUMN influential_citations INTEGER"))
        db.commit()
    if paper_col_names and "fields_of_study" not in paper_col_names:
        db.execute(text("ALTER TABLE papers ADD COLUMN fields_of_study JSON"))
        db.commit()

    # writing_projects.target_word_count
    cols = db.execute(text("PRAGMA table_info(writing_projects)")).all()
    col_names = {row[1] for row in cols}
    if col_names and "target_word_count" not in col_names:
        db.execute(
            text(
                "ALTER TABLE writing_projects ADD COLUMN target_word_count INTEGER DEFAULT 0 NOT NULL"
            )
        )
        db.commit()

    # writing_chapters.created_at / updated_at
    ch_cols = db.execute(text("PRAGMA table_info(writing_chapters)")).all()
    ch_names = {row[1] for row in ch_cols}
    if ch_names and "created_at" not in ch_names:
        db.execute(
            text("ALTER TABLE writing_chapters ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL")
        )
        db.commit()
    if ch_names and "updated_at" not in ch_names:
        db.execute(
            text("ALTER TABLE writing_chapters ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL")
        )
        db.commit()
