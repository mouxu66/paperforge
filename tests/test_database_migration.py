"""数据库迁移安全测试 —— 覆盖原 🔴「迁移 DROP 0 测试」。

验证：
1. papers 表数据在迁移后不丢失
2. depth_scores 仅在不兼容时重建（兼容时保留数据）
3. schema_version 表正确记录版本
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import mock_api.database
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def fresh_db(monkeypatch):
    """创建数据库并初始化（直接调用 init_db，不经过 FastAPI lifespan）。

    使用临时数据库文件，避免与开发数据库互相污染。

    隔离策略（关键）：通过 ``monkeypatch`` 把 ``mock_api.database`` 的模块级
    ``DB_URL`` / ``engine`` / ``SessionLocal`` / ``DB_PATH`` / ``DATA_DIR`` 指向
    临时库。``init_db`` 通过模块属性访问这些全局，因此在临时库建表。

    ⚠️ 为什么不用 ``importlib.reload``：reload 会替换 ``sys.modules`` 中的模块
    对象，但 ``app.py`` 等早已用 ``from .database import SessionLocal`` 缓存了**旧**
    模块对象的引用，reload 后这些引用不更新，导致全量跑时后续用例莫名
    ``no such table``（跨测试污染）。monkeypatch 只改模块**属性**，
    ``app`` 缓存的 ``SessionLocal`` 仍指向默认库，不受影响；teardown 时
    monkeypatch 自动撤销，模块属性还原为默认库，零污染。
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_migration.db"
        url = f"sqlite:///{db_path}"
        tmp_engine = create_engine(url, future=True, pool_pre_ping=True)
        tmp_session = sessionmaker(bind=tmp_engine, autoflush=False, autocommit=False, future=True)

        # 把数据库相关模块属性全部切到临时库
        monkeypatch.setattr(mock_api.database, "DB_URL", url)
        monkeypatch.setattr(mock_api.database, "engine", tmp_engine)
        monkeypatch.setattr(mock_api.database, "SessionLocal", tmp_session)
        monkeypatch.setattr(mock_api.database, "DB_PATH", db_path)
        monkeypatch.setattr(mock_api.database, "DATA_DIR", db_path.parent)
        monkeypatch.setenv("PAPERFORGE_DB_PATH", str(db_path))
        from mock_api.settings import reset_settings

        reset_settings()

        from mock_api.database import init_db

        init_db()
        db = tmp_session()
        try:
            yield db
        finally:
            db.close()
            try:
                tmp_engine.dispose()
            except Exception:
                pass
            # 临时 DB 及 WAL/SHM 文件，Windows 下可能延迟释放，忽略删除失败
            for ext in ("", "-wal", "-shm"):
                try:
                    (Path(tmpdir) / f"test_migration.db{ext}").unlink(missing_ok=True)
                except Exception:
                    pass
    # monkeypatch 退出时自动撤销，mock_api.database 模块属性还原为默认库


def _assert_schema_matches_metadata(db: Session) -> None:
    """断言实际 DB 的每张表都包含 Base.metadata 声明的全部列（F3 schema 漂移守卫）。

    方向：metadata -> 实际库。模型新增列必须被 ``create_all`` 或
    ``_migrate_schema`` 追平，否则这里直接断言失败，防止「模型有列、库里缺列」
    的静默漂移（即原 Alembic 双轨漂移的根因）。
    """
    from mock_api.database import Base

    for table_name, table in Base.metadata.tables.items():
        actual = db.execute(text(f"PRAGMA table_info({table_name})")).all()
        actual_cols = {row[1] for row in actual}
        assert actual_cols, f"表 {table_name} 在实际库中不存在或为空"
        missing = [c.name for c in table.columns if c.name not in actual_cols]
        assert not missing, f"表 {table_name} 缺列（模型声明但库缺失）：{missing}"


class TestMigrationSafety:
    """迁移安全性测试。"""

    def test_papers_table_exists_after_init(self, fresh_db):
        """init_db 后 papers 表应存在。"""
        result = fresh_db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='papers'")).first()
        assert result is not None

    def test_paper_figures_caption_text_column_exists(self, fresh_db):
        """M0/Rec4: paper_figures 表应包含 caption_text 列。"""
        result = fresh_db.execute(
            text("SELECT name FROM pragma_table_info('paper_figures') WHERE name='caption_text'")
        ).first()
        assert result is not None, "paper_figures.caption_text 列不存在"

    def test_paper_figures_source_column_exists(self, fresh_db):
        """M0: paper_figures 表应包含 source 列（bitmap / vector）。"""
        result = fresh_db.execute(
            text("SELECT name FROM pragma_table_info('paper_figures') WHERE name='source'")
        ).first()
        assert result is not None, "paper_figures.source 列不存在"

    def test_paper_figures_figure_number_column_exists(self, fresh_db):
        """Rec2: paper_figures 表应包含 figure_number 列。"""
        result = fresh_db.execute(
            text("SELECT name FROM pragma_table_info('paper_figures') WHERE name='figure_number'")
        ).first()
        assert result is not None, "paper_figures.figure_number 列不存在"

    def test_paper_figures_m1_m2_columns_exist(self, fresh_db):
        """M1/M2: paper_figures 表应包含 match_confidence / match_label / vlm_decision 列。"""
        for col in ("match_confidence", "match_label", "vlm_decision"):
            result = fresh_db.execute(
                text(f"SELECT name FROM pragma_table_info('paper_figures') WHERE name='{col}'")
            ).first()
            assert result is not None, f"paper_figures.{col} 列不存在"

    def test_citation_sentiments_cloud_recheck_column_exists(self, fresh_db):
        """被引情感云端复核审计列：citation_sentiments 应包含 cloud_recheck。"""
        result = fresh_db.execute(
            text("SELECT name FROM pragma_table_info('citation_sentiments') WHERE name='cloud_recheck'")
        ).first()
        assert result is not None, "citation_sentiments.cloud_recheck 列不存在"

    def test_schema_version_table_exists(self, fresh_db):
        """_schema_version 表应在迁移后存在。"""
        result = fresh_db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='_schema_version'")).first()
        assert result is not None

    def test_paper_figures_paper_id_figure_number_index_exists(self, fresh_db):
        """KPI/Rec2: paper_figures 表应包含 (paper_id, figure_number) 复合索引。"""
        result = fresh_db.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='ix_paper_figures_paper_id_figure_number'"
            )
        ).first()
        assert result is not None, "paper_figures(paper_id, figure_number) 复合索引不存在"

    def test_seed_papers_not_lost_after_reinit(self, fresh_db):
        """种子数据在重复 init 后不应丢失。"""
        from mock_api.database import init_db
        count_before = fresh_db.execute(text("SELECT count(*) FROM papers")).scalar()
        assert count_before > 0, "Seed data should exist after init"

        # 再次 init（模拟重启）
        init_db()

        count_after = fresh_db.execute(text("SELECT count(*) FROM papers")).scalar()
        assert count_after == count_before, "Papers should not be lost after re-init"

    def test_fts5_table_exists(self, fresh_db):
        """paper_fts 虚拟表应在迁移后存在。"""
        result = fresh_db.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='paper_fts'")).first()
        assert result is not None

    def test_fts5_has_data(self, fresh_db):
        """FTS5 表应有回填数据。"""
        count = fresh_db.execute(text("SELECT count(*) FROM paper_fts")).scalar()
        assert count > 0, "FTS5 should be backfilled from papers table"

    def test_no_alembic_version_table_after_init(self, fresh_db):
        """退役 Alembic 后，全新库不应再生成 alembic_version 表。"""
        result = fresh_db.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")
        ).first()
        assert result is None, "alembic 已退役，不应再生成 alembic_version 表"

    def test_schema_matches_metadata(self, fresh_db):
        """F3 守卫：init_db 后所有 Base.metadata 声明的列都存在于实际库。"""
        _assert_schema_matches_metadata(fresh_db)

    def test_legacy_database_single_source_no_alembic(self, monkeypatch):
        """F3 回归：退役 Alembic 后，legacy 库经 init_db 不应再有 alembic_version 表，
        且数据不丢、schema 与 Base.metadata 一致（双轨漂移消除）。"""
        from mock_api.database import Base, init_db

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "legacy.db"
            url = f"sqlite:///{db_path}"
            tmp_engine = create_engine(url, future=True, pool_pre_ping=True)
            tmp_session = sessionmaker(bind=tmp_engine, autoflush=False, autocommit=False, future=True)

            # 1. 模拟 legacy 库：用 Base.metadata.create_all 建表（Alembic 引入之前的方式）
            Base.metadata.create_all(bind=tmp_engine)

            # 2. 写入一条 legacy 数据
            db = tmp_session()
            try:
                db.execute(
                    text(
                        "INSERT INTO papers (id, title, authors, abstract, category, tags, year, "
                        "journal, pdf_url, citations, chunk_count, index_size, source) "
                        "VALUES (:id, :title, :authors, :abstract, :category, :tags, :year, "
                        ":journal, :pdf_url, :citations, :chunk_count, :index_size, :source)"
                    ),
                    {
                        "id": "legacy-paper-1",
                        "title": "Legacy Paper",
                        "authors": '["Alice"]',
                        "abstract": "abstract",
                        "category": "arxiv",
                        "tags": '[]',
                        "year": 2020,
                        "journal": "Nature",
                        "pdf_url": "",
                        "citations": 0,
                        "chunk_count": 0,
                        "index_size": 0,
                        "source": "arxiv",
                    },
                )
                db.commit()
            finally:
                db.close()

            # 3. 确认无 alembic_version 表（退役前理论上也没有，这里固化不变量）
            with tmp_engine.connect() as conn:
                result = conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'")
                )
                assert result.first() is None, "退役 Alembic 后不应存在 alembic_version 表"

            # 4. 把模块全局属性切到临时库并 init_db
            monkeypatch.setattr(mock_api.database, "DB_URL", url)
            monkeypatch.setattr(mock_api.database, "engine", tmp_engine)
            monkeypatch.setattr(mock_api.database, "SessionLocal", tmp_session)
            monkeypatch.setattr(mock_api.database, "DB_PATH", db_path)
            monkeypatch.setattr(mock_api.database, "DATA_DIR", db_path.parent)
            monkeypatch.setenv("PAPERFORGE_DB_PATH", str(db_path))
            from mock_api.settings import reset_settings

            reset_settings()

            init_db()

            # 5. legacy 数据未被破坏
            db2 = tmp_session()
            try:
                count = db2.execute(
                    text("SELECT count(*) FROM papers WHERE id = 'legacy-paper-1'")
                ).scalar()
                assert count == 1, "legacy 数据应被保留"
                # 6. F3 守卫：schema 与 Base.metadata 一致（无缺列）
                _assert_schema_matches_metadata(db2)
            finally:
                db2.close()

            # 释放引擎，避免 Windows 下临时目录清理时文件仍被占用
            try:
                tmp_engine.dispose()
            except Exception:
                pass
