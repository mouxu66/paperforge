# ADR-006: Alembic 作为 Schema 迁移唯一事实源

**状态**: Superseded（已被 F3 于 2026-07-19 推翻）

**日期**: 2026-07-14

> ⚠️ **2026-07-19 反转（F3 修复）**：本 ADR 的「Alembic 作为唯一事实源」决策已撤销。
> 实践表明 `alembic/` 的 6 个迁移与 `_migrate_schema()` 持续漂移，误执行
> `alembic upgrade head` 会建出缺列（如 `source_paper_id` / `ocr_status` /
> `sentiment_*`）的残缺库。现行策略：**彻底退役 Alembic**，以 `_migrate_schema()`
> 为 schema 唯一真相源，并加 CI schema 断言（`tests/test_database_migration.py`）
> 守卫 `models.py` 与 `_migrate_schema` 一致。详见 `mock_api/database.py::init_db`。

## 背景

PaperForge 早期使用 `Base.metadata.create_all()` + 手写的 `_migrate_schema()` 双轨制来管理数据库 schema。这种方式在开发初期运行良好，但随着表数量增加到 16 张、字段变更频繁，出现了以下问题：

1. **无版本溯源**：`_migrate_schema()` 通过 `_schema_version` 表记录组件版本，但无法与代码变更一一对应，回滚困难。
2. **重复劳动**：新增列需要同时修改 `models.py`、`_migrate_schema()`、以及可能的 `alembic/versions/`。
3. **legacy 库升级风险**：既有数据库（有表无 `alembic_version`）接入 Alembic 时，需要 `stamp baseline + upgrade` 的特殊路径，此前缺乏测试覆盖。
4. **双轨并行容易遗漏**：部分字段只改了 `models.py` 却忘记同步 `_migrate_schema()`，导致 fresh DB 与 legacy DB 结构不一致。

## 决策

1. **Alembic 作为 schema 变更的唯一事实源**。所有表结构变更必须通过 `alembic revision --autogenerate` 生成迁移脚本。
2. **逐步废弃 `_migrate_schema()` 中的字段新增逻辑**。`_migrate_schema()` 仅保留以下职责：
   - 为 PyInstaller frozen 模式或 alembic 不可用的环境做兜底；
   - 处理 Alembic 无法表达的虚拟表/FTS5 相关逻辑；
   - 作为 idempotent 的补偿层，确保极端情况下表结构完整。
3. **运行期 `create_all()` 仅用于 fresh DB 或 legacy DB 的首次接入**，后续 schema 演进完全由 Alembic 接管。
4. **`alembic/env.py` 始终以项目运行时 `DB_URL` 为准**，避免 `alembic.ini` 中硬编码 URL 导致迁移错库。
5. **新增 legacy 升级冒烟测试**：覆盖「有表无 `alembic_version` → stamp baseline → upgrade to head」路径。

## 后果（收益 / 权衡）

### 收益

- **单一事实源**：schema 变更只改一处，降低遗漏风险。
- **可回滚**：每个 migration 都有 `upgrade()` / `downgrade()`，便于问题排查。
- **测试覆盖**：legacy 升级路径有自动化测试，避免回归。
- **CI 友好**：`alembic upgrade head` 可作为部署前置检查。

### 权衡

- **开发流程增加一步**：修改 `models.py` 后必须运行 `alembic revision --autogenerate -m "xxx"`。
- **frozen 模式仍需兜底**：PyInstaller 打包环境可能缺少 alembic，保留 `_migrate_schema()` 作为安全网。
- **FTS5 虚拟表仍由代码管理**：Alembic 不管理 `paper_fts`，继续由 `_ensure_fts5()` 负责。

## 操作指南

### 新增字段

```bash
# 1. 修改 mock_api/models.py 中的 ORM 定义
# 2. 生成迁移脚本
alembic revision --autogenerate -m "add xxx column to papers"

# 3. 检查生成的脚本，确保 idempotent（使用 IF NOT EXISTS / batch_alter）
# 4. 应用迁移
alembic upgrade head
```

### 手动迁移

若 autogenerate 无法覆盖（如复杂数据迁移），可手写 migration：

```bash
alembic revision -m "manual migration"
```

### 环境变量

- `PAPERFORGE_DB_PATH`：指定数据库文件路径。
- `ALEMBIC_DATABASE_URL`：（可选）生成迁移时覆盖默认 DB_URL，用于对临时库生成迁移。

## 相关文件

- `alembic/env.py`
- `alembic/versions/c7c2b3372715_initial_schema.py`
- `mock_api/database.py`
- `tests/test_database_migration.py`
