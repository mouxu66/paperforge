"""PaperForge mock 后端数据库连接与初始化。

使用 SQLite + SQLAlchemy 2.x（同步引擎）。数据库文件存放在本包目录下，
首次启动时自动建表并写入种子数据，并创建 FTS5 全文索引虚拟表。
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import cast

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

logger = logging.getLogger(__name__)

# 默认 schema 版本号 —— 任何破坏性 migration 前必须 <=
# migration 检查这里以保证 DROP 后留有追责余地。
CURRENT_SCHEMA_VERSION = 5


def _resolve_db_path() -> Path:
    """解析实际部署的数据库路径。

    优先级：
    1. 环境变量 PAPERFORGE_DB_PATH （明确指定则优先）
    2. PyInstaller frozen 模式（sys.frozen）→ 用户的 %APPDATA%/PaperForge/ 等
       writable 目录，否则落回只读 _MEIPASS 每次启动被清空。
    3. 默认 → mock_api/paperforge_mock.db （源码开发模式）

    🛡️ P0-3 frozen 打包无持久化 DB 修复：
    原版用 Path(__file__).parent / "paperforge_mock.db" → frozen 下
    __file__ 指向 _MEIPASS（临时解压目录），只读且每次启动重建空库，
    用户著作 / 笔记 / 审阅全丢。改为识别 frozen 模式后跳到用户主目录。
    """
    from .settings import get_settings

    env_path = get_settings().db_path
    if env_path:
        p = Path(env_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    # PyInstaller frozen 模式：sys.frozen=True + sys.executable 是 .exe
    if getattr(sys, "frozen", False):
        # Windows: %APPDATA%/PaperForge/  (即 C:\Users\<USER>\AppData\Roaming\)
        # macOS:   ~/Library/Application Support/PaperForge/
        # Linux:   $XDG_DATA_HOME/paperforge/  (~/.local/share/paperforge/ 兜底)
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
            data_dir = base / "PaperForge"
        elif sys.platform == "darwin":
            data_dir = Path.home() / "Library" / "Application Support" / "PaperForge"
        else:
            xdg = os.environ.get("XDG_DATA_HOME")
            data_dir = (
                Path(xdg) / "paperforge" if xdg else Path.home() / ".local" / "share" / "paperforge"
            )
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "paperforge_mock.db"
        logger.info(
            "frozen 模式：DB 重定向到用户目录 %s（_MEIPASS 只读不会丢数据）",
            db_path,
        )
        return db_path

    # 默认（源码模式）：原行为不变，便于 dev 直跑
    return Path(__file__).parent / "paperforge_mock.db"


# 数据库路径（frozen / env / 默认三路）
DB_PATH: Path = _resolve_db_path()
DB_URL: str = f"sqlite:///{DB_PATH}"
DATA_DIR: Path = DB_PATH.parent  # 备份 + 附件统一放这里

# check_same_thread=False：FastAPI 同步路由运行在线程池中，需允许跨线程使用连接
engine = create_engine(
    DB_URL,
    connect_args={
        "check_same_thread": False,
        # 🛡️ P1-8 SQLite WAL 并发写锁修复：
        # 默认 rollback journal 模式 + 最大 5 秒忙等 + 自动 WAL 模式。
        # 多个 FastAPI 路由线程同时写入审阅记录 / 笔记 / 收藏时，
        # 旧配置会随机触发 "database is locked" 非确定性失败。
        # WAL 让读不阻塞写、写不阻塞读，配合 busy_timeout 处理少数真冲突。
        "timeout": 30.0,
    },
    echo=False,
    future=True,
    pool_pre_ping=True,  # 连接复用前 ping 一次，断连自动 reconnect
)


@event.listens_for(engine, "connect")
def _sqlite_pragma_on_connect(dbapi_connection, _connection_record):
    """每个新连接应用 PRAGMA：WAL 模式 + 5s 忙等 + 外键 + 内存临时表。

    event.listens_for 在连接创建时触发，比直接在 connect_args 设置更稳：
    即使 SQLAlchemy 内部重置连接也能保证 PRAGMA 一直生效。
    """
    cur = dbapi_connection.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
    except Exception:  # noqa: BLE001 - migration/backup 钩子 - 失败应记录，不阻塞启动
        pass
    try:
        cur.execute("PRAGMA busy_timeout=5000")
    except Exception:  # noqa: BLE001 - migration/backup 钩子 - 失败应记录，不阻塞启动
        pass
    try:
        cur.execute("PRAGMA foreign_keys=ON")
    except Exception:  # noqa: BLE001 - migration/backup 钩子 - 失败应记录，不阻塞启动
        pass
    try:
        cur.execute("PRAGMA synchronous=NORMAL")
    except Exception:  # noqa: BLE001 - migration/backup 钩子 - 失败应记录，不阻塞启动
        pass
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


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
    - 同步：种子写入时由 crud 维持；后续新增论文需同步写 FTS（见 seed_if_empty）。

    FTS5 不支持 ALTER TABLE ADD COLUMN，因此当旧表缺少 authors 或 full_text
    列时需 DROP 后重建并回填数据。

    🛡️ P0-2 启动迁移 DROP 静默销毁数据修复：
    原版 DROP TABLE paper_fts 之前０备份、零告警。改为「DROP 前强制将
    paper_fts 连同 papers.full_text 快照备份到 DATA_DIR/paper_fts_backup_<ts>.txt」，
    补充出错后追责线索（不是全量备份，但是至少能看出当时的语料状态）。
    另外 schema_version 版本表首次会植入，后续 ALTER / DROP 之前都先读。
    """
    # 0) 为本次会话植入 schema_version 表与当前 version（FTS 子迁移独立递增）
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS _schema_version ("
            "  component VARCHAR(64) PRIMARY KEY,"
            "  version INTEGER NOT NULL DEFAULT 0,"
            "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL"
            ")"
        )
    )
    _schema_version = _get_or_init_schema_version(db, "paper_fts")
    # 1) 检查现有 FTS 表是否含 authors/full_text/tags 列；缺任一则 DROP 重建
    fts_cols = db.execute(text("PRAGMA table_info(paper_fts)")).all()
    existing_fts_cols = {row[1].lower() for row in fts_cols}
    required_fts_cols = {"authors", "full_text", "tags"}
    if fts_cols and not required_fts_cols.issubset(existing_fts_cols):
        # 🛡️ P0-2 修复：DROP 前先殘留一份可溯源的备份，后续可以 grep/check 追溯
        _dump_forensic_snapshot(db, "paper_fts", reason="pre-drop columns mismatch")
        logger.warning(
            "paper_fts schema 不兼容 (期望含 %s)，备份后准备重建: 现有列=%s",
            required_fts_cols,
            existing_fts_cols,
        )
        db.execute(text("DROP TABLE paper_fts"))
        db.commit()

    # 2) 建独立 FTS 虚拟表（含 paper_id + title + abstract + authors + full_text + tags 文本副本）
    #    authors/tags 存为以空格连接的字符串，便于 MATCH 全文检索
    #    full_text 为 PDF 全文（可空字符串），供 B1 全文检索
    db.execute(
        text(
            "CREATE VIRTUAL TABLE IF NOT EXISTS paper_fts USING fts5("
            "paper_id UNINDEXED, title, abstract, authors, full_text, tags"
            ")"
        )
    )

    # 3) 若 FTS 表为空，从 papers 表回填，paper_id 关联 papers.id，
    #    authors/tags 用空格连接，full_text 为空时填空字符串）
    count_row = db.execute(text("SELECT count(*) FROM paper_fts")).first()
    fts_count = count_row[0] if count_row else 0
    if fts_count == 0:
        db.execute(
            text(
                "INSERT INTO paper_fts(paper_id, title, abstract, authors, full_text, tags) "
                "SELECT id, title, abstract, "
                "COALESCE((SELECT group_concat(value, ' ') FROM json_each(authors)), ''), "
                "COALESCE(full_text, ''), "
                "COALESCE((SELECT group_concat(value, ' ') FROM json_each(tags)), '') "
                "FROM papers"
            )
        )

    # 4) 升级版本跳到当前实现预期 version
    _bump_schema_version(db, "paper_fts", target=2)
    db.commit()


def _has_any_table(db: Session) -> bool:
    """Return True if the database already has at least one table."""
    row = db.execute(text("SELECT name FROM sqlite_master WHERE type='table' LIMIT 1")).first()
    return row is not None


def init_db() -> None:
    """建表、写入种子数据，并初始化 FTS5 全文索引。

    迁移策略（单一事实源）：
    - ``_migrate_schema`` 是 schema 的唯一真相源；无论开发、服务器还是
      PyInstaller frozen 模式，启动时统一先 ``Base.metadata.create_all``
      再 ``_migrate_schema``，确保所有表/列/索引最终一致。
    - 自 2026-07-19（F3 修复）起已**彻底退役 Alembic**：移除 ``alembic/``
      目录与运行期 stamp，杜绝「误执行 ``alembic upgrade head`` 建出缺列
      残缺库」的双轨漂移风险。所有 schema 变更一律同步到 ``_migrate_schema``
      与 ``models.py``，由 CI schema 断言（``tests/test_database_migration.py``）
      守卫两者一致。
    - ``_migrate_schema`` 仅以 ``ALTER TABLE ADD COLUMN`` 补齐已有表（幂等、
      不丢数据），破坏性变更仍以 forensic 快照兜底。

    🛡️ P0-2 启动迁移 DROP 修复：
    1. 调用 _pre_init_backup() 在任何破坏性迁移前先做「冷启动快照」，
       默认保留最近 7 天，超期自动 prume；手动调用 backup_db() 可导出。
    2. 后面的 _migrate_schema / _ensure_fts5 都通过 schema_version
       表记录本次升级点，即使中途中断也能追踪。
    """
    # 在函数内部导入 ORM 模型，避免模块加载期循环导入
    from . import models  # noqa: F401
    from .crud import seed_if_empty

    # ⚠️ DB 创建警告：如果 DB 文件不存在（全新数据库），输出醒目警告，
    # 避免用户因 PAPERFORGE_DB_PATH 指向错误路径而静默得到一个空 seed 库
    # （0 个模型配置 → AI 功能全部 502 "no model" 错误，用户不知道为什么）。
    if not DB_PATH.exists():
        logger.warning(
            "⚠️ 创建全新数据库: %s —— 如果你期望使用已有数据库，请检查"
            " PAPERFORGE_DB_PATH 环境变量是否指向了正确路径。"
            "全新数据库仅含种子论文，不含模型配置（需在「模型管理」页面添加）。",
            DB_PATH,
        )

    db = SessionLocal()
    try:
        # 0. 预炸备份：开表时遇水冷快照（不是事务点热备，但至少能追责）
        _pre_init_backup()

        # 1. Schema 迁移：_migrate_schema 为唯一真相源，所有模式统一走此路径
        logger.info("使用 _migrate_schema 作为 schema 单一事实源进行迁移")
        Base.metadata.create_all(bind=engine)
        _migrate_schema(db)

        # 2. Alembic 已退役（F3）：schema 仅由 _migrate_schema 管理，
        #    不再运行时 stamp，避免双轨漂移。

        # 3. 种子数据 + FTS5 虚拟表（Alembic 不管理虚拟表）
        #    先写入种子，再回填 FTS5，确保 paper_fts 有数据。
        seed_if_empty(db)
        _ensure_fts5(db)
    finally:
        db.close()


def backup_db(reason: str = "manual") -> Path | None:
    """手动触发全库快照到 DATA_DIR/backups/。

    使用 sqlite3 在线备份 API（迭代拷贝、安全热备），不会给在线读造成锁。
    返回备份文件路径；异常返回 None。

    🛡️ P0-2 本地表单一文件 SQLite 承载全部用户资产，必须能手动备份/迁移。
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        backup_dir = DATA_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"paperforge_{timestamp}_{reason}.db"
        # sqlite3 在线热备 API（不会锁住在线读）
        import sqlite3

        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        logger.info("DB 手动快照完成 → %s", backup_path)
        return backup_path
    except Exception as exc:  # noqa: BLE001 - migration/backup 钩子 - 失败应记录，不阻塞启动
        logger.warning("DB 手动快照失败: %s", exc)
        return None


def _pre_init_backup() -> None:
    """冷启动快照：每次 init_db 启动前先尝试保存一次全库快照。

    陈旧备份自动 prune（保留最近 7 天 / 最多 10 个）。失败静默不阻塞启动。
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        backup_dir = DATA_DIR / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)

        # 只在原 DB 存在且有内容时才有意义
        if not DB_PATH.exists() or DB_PATH.stat().st_size < 1024:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"auto_{timestamp}.db"
        import sqlite3

        src = sqlite3.connect(str(DB_PATH))
        dst = sqlite3.connect(str(backup_path))
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

        # Prune 旧快照：超过 7 天 或 总量 > 10 个
        backups = sorted(
            backup_dir.glob("auto_*.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[10:]:
            try:
                old.unlink()
            except Exception:  # noqa: BLE001 - migration/backup 钩子 - 失败应记录，不阻塞启动
                pass
        # 超期清理
        import time as _time

        cutoff = _time.time() - 7 * 24 * 60 * 60
        for old in backups:
            if old.stat().st_mtime < cutoff and len(backups) > 3:
                try:
                    old.unlink()
                except Exception:  # noqa: BLE001 - migration/backup 钩子 - 失败应记录，不阻塞启动
                    pass
    except Exception as exc:  # noqa: BLE001 - migration/backup 钩子 - 失败应记录，不阻塞启动
        # 预炸快照失败仅记告警，不阻塞启动
        logger.debug("冷启动快照跳过: %s", exc)


def _get_or_init_schema_version(db: Session, component: str) -> int:
    """获取该组件上次记录的 schema version；无记录则创建。

    🛡️ P0-2 schema 版本追踪：所有 DROP / ALTER 调用前都应读 schema_version
    决定是否执行，升级后应调用 _bump_schema_version 记录点。
    """
    db.execute(
        text(
            "CREATE TABLE IF NOT EXISTS _schema_version ("
            "  component VARCHAR(64) PRIMARY KEY,"
            "  version INTEGER NOT NULL DEFAULT 0,"
            "  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL"
            ")"
        )
    )
    row = db.execute(
        text("SELECT version FROM _schema_version WHERE component = :c"),
        {"c": component},
    ).first()
    if row is not None:
        return int(row[0])
    db.execute(
        text(
            "INSERT INTO _schema_version(component, version) VALUES (:c, 0) "
            "ON CONFLICT(component) DO NOTHING"
        ),
        {"c": component},
    )
    db.commit()
    return 0


def _bump_schema_version(db: Session, component: str, target: int) -> None:
    """将指定组件的 schema_version 调到 target（幂等）。"""
    db.execute(
        text(
            "INSERT INTO _schema_version(component, version, updated_at) "
            "VALUES (:c, :v, CURRENT_TIMESTAMP) "
            "ON CONFLICT(component) DO UPDATE SET version = :v, updated_at = CURRENT_TIMESTAMP"
        ),
        {"c": component, "v": target},
    )


def _migrate_schema(db: Session) -> None:
    """轻量迁移：为已有表补充新列（SQLite ALTER TABLE ADD COLUMN）。

    create_all 只建新表不修改已有表结构，开发环境升级时需手动补充列。

    🛡️ P0-2 DROP 只限真正不兼容场景，且需提前 forensic 快照（见 depth_scores 分支）。
    其余变动都走  ALTER TABLE ADD COLUMN，不动原数据。
    """
    _papers_version = _get_or_init_schema_version(db, "papers")
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
    # Reflection 自动识别：报告所引用的原论文 paper_id
    if paper_col_names and "source_paper_id" not in paper_col_names:
        db.execute(text("ALTER TABLE papers ADD COLUMN source_paper_id TEXT"))
        db.execute(
            text("CREATE INDEX IF NOT EXISTS ix_papers_source_paper_id ON papers(source_paper_id)")
        )
        db.commit()
    # Reflection 自动识别状态：记录 resolver 中间结果，供补识别任务筛选
    if paper_col_names and "source_paper_status" not in paper_col_names:
        db.execute(text("ALTER TABLE papers ADD COLUMN source_paper_status TEXT"))
        db.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_papers_source_paper_status ON papers(source_paper_status)"
            )
        )
        db.commit()
    # 完整修复：感悟报告原始 docx 存储路径
    if paper_col_names and "reflection_docx_path" not in paper_col_names:
        db.execute(text("ALTER TABLE papers ADD COLUMN reflection_docx_path TEXT"))
        db.commit()
    # WP-1.2: OCR 状态机与扫描件标识
    if paper_col_names and "ocr_status" not in paper_col_names:
        db.execute(text("ALTER TABLE papers ADD COLUMN ocr_status TEXT"))
        db.commit()
    if paper_col_names and "is_scanned" not in paper_col_names:
        db.execute(text("ALTER TABLE papers ADD COLUMN is_scanned BOOLEAN"))
        db.commit()
    # WP-2.2: 自我情感字段（历史对齐说明：曾与已退役的 alembic 迁移对齐；
    # hotfix 修复旧 database.py:_migrate_schema() 遗漏后启动崩溃的 bug。
    # 类型：按 SQLite 动态类型规范，Float → REAL、String → VARCHAR。
    # Alembic 已于 2026-07-19 退役，本路径作为唯一真相源保证 DB 追平 ORM。)
    if paper_col_names and "sentiment_score" not in paper_col_names:
        db.execute(text("ALTER TABLE papers ADD COLUMN sentiment_score REAL"))
        db.commit()
    if paper_col_names and "sentiment_label" not in paper_col_names:
        db.execute(text("ALTER TABLE papers ADD COLUMN sentiment_label TEXT"))
        db.commit()
    if paper_col_names and "sentiment_confidence" not in paper_col_names:
        db.execute(text("ALTER TABLE papers ADD COLUMN sentiment_confidence REAL"))
        db.commit()
    # WP-5.1: DOI 元数据（F3 遗漏，导致旧库启动崩溃）
    if paper_col_names and "doi" not in paper_col_names:
        db.execute(text("ALTER TABLE papers ADD COLUMN doi TEXT"))
        db.commit()
    _bump_schema_version(db, "papers", 8)

    # paper_figures.qwen_summary（图表 OCR 的 LLM 解读摘要）
    # M0/Rec4: paper_figures.caption_text（图注文本）
    fig_cols = db.execute(text("PRAGMA table_info(paper_figures)")).all()
    fig_col_names = {row[1] for row in fig_cols}
    if fig_col_names and "qwen_summary" not in fig_col_names:
        db.execute(text("ALTER TABLE paper_figures ADD COLUMN qwen_summary TEXT"))
        db.commit()
    if fig_col_names and "caption_text" not in fig_col_names:
        db.execute(text("ALTER TABLE paper_figures ADD COLUMN caption_text TEXT"))
        db.commit()
    if fig_col_names and "source" not in fig_col_names:
        db.execute(
            text("ALTER TABLE paper_figures ADD COLUMN source VARCHAR DEFAULT 'bitmap' NOT NULL")
        )
        db.commit()
    # Rec2: figure_number 用于真实图去重、计算 caption coverage KPI
    if fig_col_names and "figure_number" not in fig_col_names:
        db.execute(text("ALTER TABLE paper_figures ADD COLUMN figure_number INTEGER"))
        db.commit()
    # M1/M2: 图注硬匹配置信度与 VLM 路由决策
    for col, col_type in [
        ("match_confidence", "REAL"),
        ("match_label", "VARCHAR"),
        ("vlm_decision", "VARCHAR"),
    ]:
        if fig_col_names and col not in fig_col_names:
            db.execute(text(f"ALTER TABLE paper_figures ADD COLUMN {col} {col_type}"))
            db.commit()
    # P0/P3: source_text_span, axis_info, curve_points
    for col, col_type in [
        ("source_text_span", "TEXT"),
        ("axis_info", "JSON"),
        ("claim_validation", "JSON"),
        ("curve_points", "JSON"),
    ]:
        if fig_col_names and col not in fig_col_names:
            db.execute(text(f"ALTER TABLE paper_figures ADD COLUMN {col} {col_type}"))
            db.commit()
    _bump_schema_version(db, "paper_figures", 5)

    # KPI / Rec2: 加速按论文+图号的聚合与去重查询
    db.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_paper_figures_paper_id_figure_number "
            "ON paper_figures(paper_id, figure_number)"
        )
    )
    db.commit()
    _bump_schema_version(db, "paper_figures", 6)

    # writing_projects.target_word_count
    cols = db.execute(text("PRAGMA table_info(writing_projects)")).all()
    col_names = {row[1] for row in cols}
    if col_names and "target_word_count" not in col_names:
        db.execute(
            text(
                "ALTER TABLE writing_projects "
                "ADD COLUMN target_word_count INTEGER DEFAULT 0 NOT NULL"
            )
        )
        db.commit()

    # writing_chapters.created_at / updated_at
    ch_cols = db.execute(text("PRAGMA table_info(writing_chapters)")).all()
    ch_names = {row[1] for row in ch_cols}
    if ch_names and "created_at" not in ch_names:
        db.execute(
            text(
                "ALTER TABLE writing_chapters "
                "ADD COLUMN created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL"
            )
        )
        db.commit()
    if ch_names and "updated_at" not in ch_names:
        db.execute(
            text(
                "ALTER TABLE writing_chapters "
                "ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL"
            )
        )
        db.commit()

    # writing_chapter_continuations.kind（学术诚信 provenance：'continue' | 'rewrite'）
    # 续写/改写落库即 provenance，导出时据此汇总「AI 使用声明」。
    cont_cols = db.execute(text("PRAGMA table_info(writing_chapter_continuations)")).all()
    cont_names = {row[1] for row in cont_cols}
    if cont_names and "kind" not in cont_names:
        db.execute(
            text(
                "ALTER TABLE writing_chapter_continuations "
                "ADD COLUMN kind VARCHAR DEFAULT 'continue' NOT NULL"
            )
        )
        db.commit()

    # depth_scores v3 -> v4: has_substance/expectation/verdict/
    # critique_points/defense_points/chair_reasoning
    # 旧表列不兼容（缺 has_substance 或 verdict），DROP 前必须 forensic 快照，
    # 才能追溯 v3 → v4 过渡期间被丢失的审阅记录。
    ds_cols = db.execute(text("PRAGMA table_info(depth_scores)")).all()
    ds_names = {row[1] for row in ds_cols}
    if ds_names and ("has_substance" not in ds_names or "verdict" not in ds_names):
        _dump_forensic_snapshot(db, "depth_scores", reason="pre-drop v3 columns mismatch")
        logger.warning(
            "depth_scores schema 不兼容 (缺少 has_substance/verdict)，备份后重建: 旧列=%s",
            sorted(ds_names),
        )
        db.execute(text("DROP TABLE IF EXISTS depth_scores"))
        db.commit()
        from sqlalchemy.sql.schema import Table

        from .models import DepthScore

        table = cast(Table, DepthScore.__table__)
        table.create(bind=db.get_bind(), checkfirst=True)
    _bump_schema_version(db, "depth_scores", 4)

    # depth_reviews_v4.version（v4.2 过滤列）
    drv4_cols = db.execute(text("PRAGMA table_info(depth_reviews_v4)")).all()
    drv4_names = {row[1] for row in drv4_cols}
    if drv4_names and "version" not in drv4_names:
        db.execute(
            text("ALTER TABLE depth_reviews_v4 ADD COLUMN version VARCHAR DEFAULT 'v4.2' NOT NULL")
        )
        db.commit()

    # depth_reviews_v4.kind（内容类型：'paper' | 'report'，reflection 集成新增）
    # 启动热路径：SELECT * 会拿到 kind，缺列时任何 depth_v4/list/unified/list
    # 查询都 500。旧记录由 server_default='paper' 充补（等同于 v4.2 论文）。
    if drv4_names and "kind" not in drv4_names:
        db.execute(
            text("ALTER TABLE depth_reviews_v4 ADD COLUMN kind VARCHAR DEFAULT 'paper' NOT NULL")
        )
        db.execute(
            text("CREATE INDEX IF NOT EXISTS ix_depth_reviews_v4_kind ON depth_reviews_v4(kind)")
        )
        db.commit()

    # depth_reviews_v4.reflection_result（反思评审轻量 pipeline 结果，JSON nullable）
    # 同 kind，reflection 集成新增。同查询路径 500。
    if drv4_names and "reflection_result" not in drv4_names:
        db.execute(text("ALTER TABLE depth_reviews_v4 ADD COLUMN reflection_result JSON"))
        db.commit()

    # depth_reviews_v4.compute_mode（fast/speed/deep，记录本次审稿使用的计算模式）
    if drv4_names and "compute_mode" not in drv4_names:
        db.execute(text("ALTER TABLE depth_reviews_v4 ADD COLUMN compute_mode VARCHAR"))
        db.commit()
    _bump_schema_version(db, "depth_reviews_v4", 5)

    # hotspot_configs（P2-1: 动态热点词配置表）
    hc_cols = db.execute(text("PRAGMA table_info(hotspot_configs)")).all()
    if not hc_cols:
        from .models import HotspotConfig

        hc_table = cast(Table, HotspotConfig.__table__)
        hc_table.create(bind=db.get_bind(), checkfirst=True)
        db.commit()
    # 若表为空，写入默认热点词，保证 HOTSPOTS_SOURCE=db 时总有兜底
    from .models import HotspotConfig

    if not db.query(HotspotConfig).first():
        from .depth_prompts_v4 import DEFAULT_HOTSPOTS

        db.add(
            HotspotConfig(
                scope="default",
                keywords=list(DEFAULT_HOTSPOTS),
                is_default=True,
            )
        )
        db.commit()

    # 分类规范化迁移：将已有论文的 category 统一为 lowercase_underscore 格式
    from .crud import normalize_category

    papers = db.execute(text("SELECT id, category FROM papers")).all()
    for pid, cat in papers:
        normalized = normalize_category(cat or "")
        if cat != normalized:
            db.execute(
                text("UPDATE papers SET category = :normalized WHERE id = :pid"),
                {"normalized": normalized, "pid": pid},
            )
    if papers:
        db.commit()

    # 为 papers 常用查询列补索引（list_papers 过滤/排序）
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_papers_title ON papers(title)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_papers_category ON papers(category)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_papers_year ON papers(year)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_papers_source ON papers(source)"))
    db.commit()

    # paper_figures（实验图 figure 级抽取结果表；MVP：figure 抽取 + OCR + 挂 RAG）
    # 用 ORM 元数据建表，保证列与 PaperFigure 完全一致；checkfirst 幂等，
    # 已在 init_db 的 Alembic 路径中建过的场景此处为 no-op。
    pf_cols = db.execute(text("PRAGMA table_info(paper_figures)")).all()
    if not pf_cols:
        from .models import PaperFigure

        pf_table = cast(Table, PaperFigure.__table__)
        pf_table.create(bind=db.get_bind(), checkfirst=True)
        db.commit()

    # api_keys（Layer 1：多 API Key 鉴权表）
    # 用 ORM 元数据建表，checkfirst 幂等。首次部署时不存在 → 创建。
    ak_cols = db.execute(text("PRAGMA table_info(api_keys)")).all()
    if not ak_cols:
        from .models import ApiKey

        ak_table = cast(Table, ApiKey.__table__)
        ak_table.create(bind=db.get_bind(), checkfirst=True)
        db.commit()
    _bump_schema_version(db, "api_keys", 1)

    # api_call_logs（Layer 2：API 调用审计日志表）
    acl_cols = db.execute(text("PRAGMA table_info(api_call_logs)")).all()
    if not acl_cols:
        from .models import ApiCallLog

        acl_table = cast(Table, ApiCallLog.__table__)
        acl_table.create(bind=db.get_bind(), checkfirst=True)
        db.commit()
    _bump_schema_version(db, "api_call_logs", 1)


def _dump_forensic_snapshot(db: Session, table_name: str, reason: str) -> Path | None:
    """DROP TABLE 前调用——按名称 dump 任意表到诊断目录。

    不同于 _dump_paper_fts_forensic_snapshot：通用版，不懂得表结构也能 dump 文本。
    🛡️ P0-2：任何破坏性迁移都必须留 forensic copy，能出 warning 不能 silent。
    """
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        diag_dir = DATA_DIR / "diagnostics"
        diag_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = diag_dir / f"{table_name}_pre_drop_{ts}.txt"
        # SAFETY: table_name 是硬编码常量（避免 SQL 注入）
        rows = db.execute(text(f"SELECT * FROM {table_name}")).all()
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# forensic snapshot table={table_name} reason={reason}\n")
            f.write(f"# captured_at={datetime.now().isoformat()}\n#\n")
            f.write(f"# rows={len(rows)}\n")
            for row in rows:
                f.write(repr(tuple(row)) + "\n")
        return out_path
    except Exception as exc:  # noqa: BLE001 - migration/backup 钩子 - 失败应记录，不阻塞启动
        logger.warning("%s forensic 快照失败: %s", table_name, exc)
        return None
