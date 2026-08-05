# PaperForge 运维手册（RUNBOOK）

本文档面向 PaperForge 的部署、运维与排障场景。

---

## 1. 启动入口

| 模式 | 命令 | 说明 |
|------|------|------|
| 开发模式 | `python -m mock_api.main` | 后端 8770 端口，前端 `cd web && npm run dev` |
| 教师版 | 双击 `PaperForge_Teacher.exe` | 自动启动后端 + 打开浏览器 |
| Windows 一键 | 双击 `start_paperforge.bat` | 自动启动后端 + 前端，打开浏览器 |
| Uvicorn | `uvicorn mock_api.main:app --reload --port 8770` | 开发热重载 |

**环境变量**：
- `ENV=development`：启用 admin 打包端点（默认 `production`，安全关闭）
- `PAPERFORGE_API_TOKEN`：设置后所有写操作需带 `X-PaperForge-Token` 头
- `PAPERFORGE_ALLOW_REMOTE=1`：放宽 loopback 限制（仅受信任内网使用）
- `PAPERFORGE_DISABLE_RESOURCE_DETECT=1`：禁用动态资源检测（Windows 上 `nvidia-smi`/`psutil` 可能触发 C 级崩溃）
- `PAPERFORGE_DISABLE_INVALID_GATE=1`：降级开关，跳过无效审稿闸口，退回旧逻辑
- `PAPERFORGE_DB_PATH`：自定义数据库路径。**⚠️ 陷阱**：若指向**不存在**的路径，`init_db()` 会**静默创建一个全新空库**（仅含种子论文、0 个模型配置），导致 AI 功能全部 502 "no model" 而用户不知原因。仅当你确实想新建/隔离库时才设；新建成功时启动日志会输出 `⚠️ 创建全新数据库: <path>` 警告。
- `PAPERFORGE_TASK_TIMEOUT`：单任务超时秒数（默认 600）
- `PAPERFORGE_TASK_WORKERS`：线程池大小（默认 min(4, cpu_count)）

---

## 2. 数据库

### 数据库位置
- **教师版（frozen）**：`%APPDATA%/PaperForge/paperforge_mock.db`（Windows）
- **开发者模式**：`mock_api/paperforge_mock.db`

### 备份
- **自动**：每次启动时冷快照到 `DATA_DIR/backups/auto_*.db`（保留 7 天 / 最多 10 个）
- **手动**：调用 `mock_api.database.backup_db("manual")` 或通过 API

### 迁移
- 启动时自动执行 `_migrate_schema()`（ALTER TABLE ADD COLUMN，非破坏性）
- DROP TABLE 前会生成 forensic 快照到 `DATA_DIR/diagnostics/`
- schema 版本记录在 `_schema_version` 表

---

## 3. 健康检查

| 端点 | 用途 | 检查内容 |
|------|------|----------|
| `GET /api/health/live` | Liveness | 仅进程存活（不检查依赖） |
| `GET /api/health/ready` | Readiness | 含 DB 连接检查，不可用返回 503 |
| `GET /api/health` | 向后兼容 | 等同于 `/api/health/ready` |

---

## 4. 常见排障

### 4.1 启动后数据库为空（教师版）
**原因**：旧版 exe 未含 DB 持久化修复，数据写在临时 `_MEIPASS` 目录。
**解决**：使用 2026-07-07 后重新打包的 exe，数据库会持久化到 `%APPDATA%/PaperForge/`。

### 4.2 "database is locked" 错误
**原因**：并发写入冲突。
**解决**：已启用 WAL 模式 + busy_timeout=5000ms。若仍出现，检查是否有多个进程同时写入。

### 4.3 Windows 启动崩溃（C 级 access violation）
**原因**：`nvidia-smi` / `psutil` 在 WindowsApps Store 版 Python 上可能触发 C 级崩溃。
**解决**：默认禁用资源检测。如需启用：`set PAPERFORGE_ENABLE_RESOURCE_DETECT=1`。

### 4.4 SSE 连接不关闭 / 任务卡住
**原因**：旧版单队列多订阅者抢读或终结事件被丢。
**解决**：已改为每订阅者独立队列 + 终结事件必达。检查 `logs/paperforge.log` 中的超时记录。

### 4.5 admin 端点 403 Forbidden
**原因**：`IS_DEV_ENV` 默认为 False（生产模式），admin 端点被关闭。
**解决**：设置 `ENV=development` 并确保从本机访问。

### 4.6 任务超时但仍在运行
**原因**：Python 无法安全中断线程，超时后 worker 线程仍在后台运行。
**解决**：已引入协作式 `cancel_event`，worker 应在长循环中检查 `get_cancel_event(task_id).is_set()`。

### 4.7 启动后数据库为空 / AI 功能全 502 "no model"（PAPERFORGE_DB_PATH 陷阱）
**原因**：设置了 `PAPERFORGE_DB_PATH` 指向一个**不存在**的路径。启动时 `_resolve_db_path()` 会按该路径新建一个空库（仅 24 篇种子论文 + 0 个模型），而非复用你已有的库（如 `mock_api/paperforge_mock.db` / `%APPDATA%/PaperForge/`）。聊天等 AI 端点因无模型配置返回 502，UI 可能表现为"挂起"。
**识别**：启动日志里若出现 `⚠️ 创建全新数据库: <你设置的路径>` 即为该陷阱触发。
**解决**：
- 想用已有库 → **不要设置** `PAPERFORGE_DB_PATH`（默认指向 `mock_api/paperforge_mock.db`）；或把它改回正确路径后重启。
- 想隔离测试 → 确认该路径就是你要的新库，然后在「模型管理」页添加模型配置。
- 误建了空库 → 删掉该文件，用正确路径重启即可。

---

## 5. 日志

- **控制台**：INFO 级别，启动时自动配置
- **文件**：`logs/paperforge.log`（RotatingFileHandler，单文件 5MB，保留 3 个备份）
- **级别**：DEBUG 排障时设 `logging.getLogger().setLevel(logging.DEBUG)`

---

## 6. 安全注意事项

1. **默认安全**：`IS_DEV_ENV` 默认 False，admin 端点关闭
2. **Loopback 限制**：默认仅允许 127.0.0.1 / ::1 / localhost 访问
3. **API Token**：设置 `PAPERFORGE_API_TOKEN` 后所有写操作需认证
4. **SSRF 防护**：PDF 代理仅允许 arXiv 主机白名单 + DNS 解析后 IP 黑名单
5. **CSP 安全头**：已配置 Content-Security-Policy / X-Frame-Options / nosniff
6. **vision llama-server（Qwen3-VL-4B @8082）必须 `127.0.0.1` 启动**：
   figure 视觉走外部 llama-server（见 `PAPERFORGE_VISION_HTTP_URL`，默认 `http://127.0.0.1:8082`），
   该服务**不受 PaperForge 调度器管理、且无认证**。llama.cpp 的 `llama-server` 默认绑定 `0.0.0.0`，
   若按默认启动会把无认证的推理端点暴露给同网段。启动命令须显式加 `--host 127.0.0.1`：
   ```
   llama-server -m Qwen3VL-4B-Instruct-Q4_K_M.gguf --mmproj mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf --host 127.0.0.1 --port 8082 -ngl 35 -c 4096 --jinja
   ```
   这与文本 Qwen（`llama_server_host` 默认已改 127.0.0.1，见 `settings.py` / `llama_server_manager.py`）形成一致安全基线：
   两路推理端点都只监听本机，跨机访问须经反向代理 + 认证。仅在受信任内网且已自管防火墙时，
   才考虑改为 `0.0.0.0`，并务必外加认证层。

---

## 7. 进程假设

PaperForge 设计为**单进程**运行：
- SSE 事件队列在内存中（跨进程不可用）
- TaskManager 线程池在进程内
- 若需多进程部署，需引入 Redis/外部队列替代内存 SSE

---

## 8. 路由架构（v3.0 PR1-PR15 重构后）

> 2026-07-22：main.py 从 3467 行瘦至 77 行，所有业务路由迁至 `mock_api/routers/`。

### 8.1 路由器清单

| 路由器 | 文件 | 路由数 | 职责 |
|--------|------|--------|------|
| health | `routers/health.py` | 3 | `/api/health/live` + `/ready` + `/health` 兼容 |
| models | `routers/models.py` | 6 | LLM 配置 CRUD + 切换 |
| qwen | `routers/qwen.py` | 1 | `/api/qwen/status` llama-server 状态 |
| compute | `routers/compute.py` | 2 | 计算模式 speed/deep 切换 |
| chat | `routers/chat.py` | 4 | `/api/ask` RAG 问答 + `/api/summarize` |
| upload | `routers/upload.py` | 3 | PDF/ZIP 上传 |
| arxiv | `routers/arxiv.py` | 2 | arXiv 检索 + 导入 |
| tasks | `routers/tasks.py` | 4 | TaskManager 创建/查询/SSE 订阅 |
| zotero | `routers/zotero.py` | 3 | Zotero 导入 + 文件夹监听 |
| depth | `routers/depth.py` | 13 | DEPTH v4.1 审稿 |
| reflection | `routers/reflection.py` | 6 | 感悟/读后报告评审 |
| reports | `routers/reports.py` | 2 | 报告分析/提交列表 |
| writing | `routers/writing.py` | 36 | 写作项目/章节/模板/导出 |
| papers | `routers/papers.py` | 50+ | 论文 CRUD/去重/标签/收藏/笔记/批注/检索 |
| admin | `admin/package.py` | — | 打包/下载管理 |

### 8.2 最终装配

```
create_app()
  ├─ admin_router       (admin/package.py)
  ├─ api_keys_router     (admin/api_keys.py)
  ├─ health_router       (routers/health.py)
  ├─ papers_router       (routers/papers.py)
  ├─ models_router       ...
  ├─ qwen_router         ...
  ├─ compute_router      ...
  ├─ chat_router         ...
  ├─ upload_router       ...
  ├─ arxiv_router        ...
  ├─ tasks_router        ...
  ├─ zotero_router       ...
  ├─ depth_router        ...
  ├─ reports_router      ...
  ├─ reflection_router   ...
  └─ writing_router      ...
```

### 8.3 共享基础设施

- `concurrency.py`：`depth_review_lock` / `reflection_file_lock` 全局锁
- `routers/common.py`：`sse_event()` / `safe_dict()` / `_as_dict()` 工具

---

## 9. VRAM 互斥调度

> 8GB 显存无法同时容纳 Qwen（~8.5G）与 OCR（~3-4G）。`vram_scheduler.py` 维护进程内状态机。

### 9.1 状态机

```
IDLE ──request_qwen──▶ QWEN_ACTIVE
IDLE ──request_ocr───▶ OCR_ACTIVE
QWEN_ACTIVE ─request_ocr──▶ (shutdown qwen) ▶ OCR_ACTIVE
OCR_ACTIVE  ─request_qwen──▶ (shutdown ocr)  ▶ QWEN_ACTIVE
OCR_ACTIVE  ─ocr_finished──▶ (autostart qwen) ▶ QWEN_ACTIVE
```

### 9.2 配置

- `vram_exclusive=True`（默认）：严格互斥
- `qwen_autostart=True`（默认）：OCR 用完自动拉回 Qwen
- 关闭互斥 → `vram_exclusive=False`（各管各的，需 ≥12GB）

### 9.3 关键修复

- **P0 (2026-07-22)**：`qwen_autostart=False` 时 `ocr_finished()` 也必须 shutdown OCR，否则 state=IDLE 但 OCR 仍占显存，下次 `request_qwen` 看到 IDLE 不释放 OCR 就拉 Qwen → 8GB 卡 OOM
- **SWITCHING_TO_QWEN 中间态**：防止 `ocr_finished` 后的后台自启期间，`request_ocr` 看到 IDLE 而漏释放 Qwen

### 9.4 排障

- Qwen 和 OCR 同时 OOM → 检查 `vram_exclusive` 是否为 True
- OCR 完成后 Qwen 未自动恢复 → 检查 `qwen_autostart`
- `/api/qwen/status` 返回 `event.kind` 字段查看当前切换状态

---

## 10. OCR 子进程管理

> OCR（PaddleOCR-VL via llama.cpp）在独立子进程中运行，经 `ocr_subprocess.py` 隔离。

### 10.1 调用边界

```
运行时路由 → ocr_subprocess.extract_text(png_images)  ← 公共 API
    │
    ├─ routers/reflection.py  (OCR 降级扫描件)
    └─ routers/papers.py      (reocr 重跑)

pdf_parser 内部 → _ocr_extract_text(content)            ← 私有实现
    │
    └─ process_one_pdf (入库管线)
```

**重要**：路由层调用 `ocr_subprocess.extract_text` 前必须包裹 VRAM 调度：
```python
get_vram_scheduler().request_ocr()
try:
    ocr_text = ocr_extract(png_images)
finally:
    try:
        get_vram_scheduler().ocr_finished()
    except Exception:
        pass
```

### 10.2 排障

- 扫描版 PDF 无文本 → 检查 `ocr_status` 字段（`done`/`failed`）
- OCR 子进程不响应 → 检查 `logs/paperforge.log` 中的 `[ocr-subprocess]` 前缀日志
- 子进程重启 → 检查 `qwen_autostart` 是否误触发 VRAM 互斥

---

## 11. llama-server 自愈重启

> `llama_server_manager.py` 后台守护线程：llama-server 崩溃后自动重启（最多 5 分钟内 3 次）。

### 11.1 重启策略

- `_max_restarts = 3`：5 分钟内崩溃 3 次后放弃自愈
- **时间窗 replenish**（2026-07-22 修复）：健康运行 5 分钟后，计数器自动归零
- 首次启动失败（从未 ready）不自动重启（配置错误保护）
- 外部复用实例（端口已被占用）不托管，不杀

### 11.2 排障

- 反复重启 → 检查模型文件完整性、显存是否被其他进程占用
- 停止自愈 → 日志 `停止自愈重启`，手动检查后重启 PaperForge
- 端口冲突 → 若 8080 已被外部 llama-server 占用则自动复用

---

## 12. API Key 多租户与限流

> Layer 1（多 API Key）+ Layer 2（per-key 令牌桶限流 + 异步审计）。

### 12.1 鉴权层

- `AuthMiddleware`：全局中间件，仅健康检查白名单放行
- 超级管理员（global/loopback）不限流
- `/api/admin/*` 额外要求 `ENV=development`
- SSE stream 路径支持 `?token=` query param（EventSource 无法自定义请求头）

### 12.2 限流

- per-key 令牌桶，默认 `api_key_rate_limit_default`/分钟
- 超限返回 429 + `Retry-After` 头
- 超级管理员不限流

### 12.3 审计

- 异步写审计日志（不阻塞请求）
- 字段：key_id / method / path / status_code / duration_ms / client_ip
- 可通过 `api_key_audit_enabled=False` 关闭

---

## 13. 启动幽灵任务清理

> 上次进程异常中断（kill -9 / 崩溃）留下的 `pending`/`running` 任务记录。

### 13.1 机制

- `_cleanup_ghost_tasks()`：启动时扫描 `tasks` 和 `depth_reviews_v4` 表
- 更新超过 10 分钟的 `pending`/`running` 记录为 `failed`
- 错误信息标注 `服务重启导致中断`

### 13.2 排障

- 启动日志出现 `启动清理幽灵任务: tasks=N, depth_reviews_v4=M` → 上次异常退出
- 启动卡在清理 → 检查 SQLite 是否被其他进程独占

---

## 14. 浏览器扩展

> PaperForge Clipper v0.3.0（Manifest V3），支持 arXiv / CNKI / Google Scholar。知网 PDF 一键导入（借浏览器登录态）需在扩展设置中开启「允许导入知网 PDF」开关。

### 14.1 安装

1. Chrome → `chrome://extensions` → 开发者模式
2. 加载已解压的扩展 → 选择 `extension/` 目录
3. 固定到工具栏

### 14.2 排障

- 保存失败且无错误 → 检查 popup 中 API Base URL 是否为 `http://localhost:8770`（P0：非本地地址会被 MV3 拦截）
- 按钮一直 "Saving..." → 可能 SW 休眠导致响应丢失，P0-2 已加 35s 超时兜底
- 学术网站改版后采不到字段 → CNKI 选择器脆弱（适配器注释中有最后验证日期）
- 标签/集合功能 → popup 中 Tags 字段，逗号分隔

---

## 15. 备份与恢复

### 备份

```bash
# 备份数据库
copy %APPDATA%\PaperForge\paperforge_mock.db %USERPROFILE%\backups\paperforge_%DATE%.db

# 备份上传的 PDF
xcopy uploads %USERPROFILE%\backups\uploads_%DATE% /E /I
```

### 恢复

```bash
copy %USERPROFILE%\backups\paperforge_YYYY-MM-DD.db %APPDATA%\PaperForge\paperforge_mock.db
```

恢复后重启 PaperForge。

### 自动备份

每次启动时冷快照到 `DATA_DIR/backups/auto_*.db`（保留 7 天 / 最多 10 个）。
