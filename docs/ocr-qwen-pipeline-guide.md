<!--
本文由用户于 2026-07-15 提供，作为「OCR + Qwen 全链路集成到 PaperForge」的设计/避坑参考。
【2026-08-17 已废弃：OCR(PaddleOCR-VL) 已于 2026-07-27 退役，见正文顶部横幅。】
工程保障团队主理人已对照 PaperForge 实际架构做核对，核心结论速记：

✅ 已具备、勿重做：
- 异步任务 + 实时进度推送：mock_api/tasks.py 的 TaskManager（线程池 + asyncio.Queue SSE）已存在，
  前端走 SSE 而非 WebSocket；OCR 流水线应挂到现有 TaskManager，不要新建 Celery/RQ/WebSocket。
- 低温度控参：depth_eval.py 已用 LLM_TEMPERATURE=0.1（注释「低温度确保输出稳定」）；
  严格评分场景可进一步降到 0（指南建议）。
- LLM 结果缓存：depth_eval_v4.py 已有 TTL 内存缓存（key=hash(prompt+system+temperature+max_tokens)，
  默认 300s，可用 PAPERFORGE_LLM_CACHE_TTL 调），OCR 结果缓存可复用同模式。
- DB 事务/一致性：database.py 已配 WAL + busy_timeout + pool_pre_ping；跨步骤入库用事务包裹即可。
- 结构化错误信封：app.py 全局异常处理器已返回 {error, message, path}，无需另建。

🆕 真正要新建（当前缺失）：
- 图片上传入口（当前仅有 /api/upload-paper|batch|zip 三个 PDF 入口，无图片 OCR 上传）。
- OCR 文本清洗 + 结构化（标题/摘要字段提取）。
- 把 OCR→Qwen 流水线接到现有 TaskManager，复用其 SSE 进度推送。
- OCR 完成即删临时图片（隐私清理）。

⚠️ 不必照搬（与项目约定冲突）：
- Redis：项目刻意不引入（export_tasks.py:12 明确「进度存内存 dict，不引入 Redis」）；
  幂等性(X-Request-ID)与结果缓存用内存 dict / 现有 TTL 缓存即可，别为单机本地工具加 Redis 依赖。
- WebSocket：已有 SSE，不要另起一套。
- Docker/HTTPS/Nginx/AES 加密存储：PaperForge 是本地桌面工具（含教师版 PaperForge_Teacher.exe 分发），
  按需取舍，非必做；若要对外发布再考虑。

🔧 需纠偏/对齐：
- 幂等用内存 X-Request-ID + 10min dict，而非 Redis。
- 重试/熔断用 tenacity（现有 scripts/fetch_arxiv_batch.py 已有指数退避可参考），arXiv 线上路径无退避的债仍待还。
- 「严格顺序加载、taskkill 杀进程树释放显存」完全匹配你真实运行方式（llama.cpp 单实例），保留。
-->

> ## ⚠️ 本文档已废弃（DEPRECATED）
>
> **废弃日期：2026-08-17。** 自 **2026-07-27** 起，独立的 PaddleOCR-VL 引擎已从
> PaperForge 移除（`mock_api/llm/figure_qwen.py` 已删除 PaddleOCR-VL 回退路径），
> 图中文字识别改由本地 **Qwen3-VL-4B** 视觉模型（`PAPERFORGE_VISION_HTTP_URL`，
> 端口 8082）顺带完成，等效 "OCR + 语义摘要"。因此：
> - 本文档所述的 "OCR ↔ Qwen 显存互斥 / taskkill 杀 OCR 进程树 / OCR VRAM bracket"
>   等机制**已不再适用**（见 `docs/adr/010-figure-trigger-discipline.md`、
>   `docs/adr/013-vram-scheduler-text-vision-arbitration.md`）。
> - 现行 figure 视觉链路以 **text-Qwen(8080) ↔ vision-Qwen(8082)** 双模型仲裁，
>   详见 `docs/adr/013-vram-scheduler-text-vision-arbitration.md`。
>
> 本文档仅作历史/避坑参考保留，**请勿按其中 OCR 流程做新开发**。

# PaperForge + OCR + Qwen 全链路集成指南（对齐版）

> ~~用户提供的设计/避坑参考文档，2026-07-15。本版已对照 PaperForge 实际架构做修正，开发 OCR + Qwen 集成模块时以此为准。~~
> **（已废弃，见上方横幅。现行实现见 ADR-013。）**

## 一、 核心架构与稳定性设计
*解决单机资源瓶颈与高并发崩溃问题。*

### 异步任务队列：复用 TaskManager + SSE（必做）
- **问题**：OCR + Qwen 推理耗时较长（可能超 1 分钟），同步阻塞会导致 API 超时。
- **现状**：`mock_api/tasks.py` 的 `TaskManager`（线程池 + `asyncio.Queue` SSE）已存在，前端走 SSE 而非 WebSocket。
- **方案**：OCR 流水线直接挂到现有 `TaskManager`，不要新建 Celery/RQ/WebSocket。
  - 提交任务：`TaskManager.submit("ocr_qwen", params, worker_fn)`
  - 进度更新：`TaskManager.update_progress(task_id, progress, message)`
  - 前端订阅：`/api/tasks/{task_id}/stream`
  - 参考：`mock_api/tasks.py` 中的 `TaskManager` 类与 SSE 实现。

### 重试与熔断机制
- **问题**：模型加载失败、网络抖动导致单次请求失败。
- **方案**：使用 Python `tenacity` 库实现指数退避重试（如重试 3 次，间隔 1s/2s/4s）。当服务端连续失败时触发熔断，直接返回降级提示。
- **参考**：`scripts/fetch_arxiv_batch.py` 中的 `fetch_with_retry` 已实现指数退避，可直接复用。

### API 幂等性设计
- **问题**：前端因网络卡顿疯狂点击重试，导致同一张图片被重复评估入库。
- **方案**：前端生成全局唯一 `X-Request-ID`，服务端用**内存 dict** 记录该 ID。若 10 分钟内收到相同 ID，直接返回缓存结果，不重复执行流水线。
- **注意**：项目刻意不引入 Redis（`export_tasks.py:12` 明确「进度存内存 dict，不引入 Redis」），幂等性与结果缓存均用内存实现。

## 二、 硬件资源与显存管理
*针对 16GB 内存 + 8GB 显存的保命策略。*

### 严格顺序加载（轮流坐庄）
- 绝不让 PaddleOCR 和 Qwen 同时占用 GPU。用 Python `subprocess` 启动 A 服务，用完后 `taskkill /F /T` 彻底杀掉进程树，确认显存释放后再启动 B 服务。
- **这与 PaperForge 真实的 llama.cpp 单实例运行方式完全一致**，保留。

### 模型量化与分层加载
- 选用 Q4_K_M 或 Q5_K_S 量化版本的 GGUF 模型，减小体积。
- 显存吃紧时，将 `-ngl 99`（全显存）降级为 `-ngl 80`（最后 20 层跑 CPU），用时间换空间。

### 上下文窗口控制
- Qwen 的 `-c 4096` 会预分配显存。若只做简短问答，可降至 `-c 2048`，节省约 0.5GB 显存。

## 三、 业务逻辑与数据一致性
*确保 OCR 结果能被 PaperForge 准确消费。*

### OCR 文本清洗与结构化
- **问题**：OCR 输出的 Markdown 包含大量冗余换行、乱码。
- **方案**：在传给 Qwen 或 PaperForge 前，用正则表达式清洗无关字符；若 PaperForge 需要分字段，用正则提取"标题：""摘要："等结构化数据。
- **注意**：这是当前缺失模块，需要新建。

### 数据库事务管理
- **问题**：论文入库成功，但感悟评价写入失败，导致数据残缺。
- **方案**：将"论文入库 + 评估结果入库"包裹在数据库事务（Transaction）中，任一步骤失败则全部回滚。
- **现状**：`database.py` 已配 WAL + `busy_timeout` + `pool_pre_ping`，跨步骤入库用事务包裹即可。

### 模型输出的确定性
- **问题**：Qwen temperature > 0 时会"胡言乱语"，导致同一篇论文每次评分不同。
- **方案**：在评估论文等严谨场景，严格设置 `temperature=0`，确保输出具备确定性和可复现性。
- **现状**：`depth_eval.py` 已用 `LLM_TEMPERATURE=0.1`（注释「低温度确保输出稳定」）；严格评分场景可进一步降到 0。

## 四、 性能优化与加速
*让系统响应更快。*

### 多级缓存策略
- **结果缓存**：对相同图片的 OCR 结果、相同文本的评估结果，以 Hash 值为 Key 存入**内存 dict**（TTL 10 分钟），避免重复计算。
- **现状**：`depth_eval_v4.py` 已有 TTL 内存缓存（key=hash(prompt+system+temperature+max_tokens)，默认 300s，可用 `PAPERFORGE_LLM_CACHE_TTL` 调整），OCR 结果缓存可复用同模式。
- **注意**：不要用 Redis，项目约定不引入。

### 数据库优化
- 为 `papers` 和 `reflections` 表的常用查询字段（如 `user_id`, `paper_id`）添加索引。
- 避免在循环中单条插入数据，改用批量插入（`INSERT INTO ... VALUES (), ()`）。

## 五、 用户体验与交互
*让用户用得安心。*

### 实时进度推送
- **问题**：等待 1 分钟毫无反馈，用户以为系统死机。
- **方案**：复用现有 SSE 向前端推送状态："🔍 正在识别图片…" → "🧠 Qwen 正在分析…" → "✅ 评估完成"。
- **注意**：不要另起 WebSocket。

### 分级错误提示
- 将底层的报错翻译成用户能看懂的话。如底层报 CUDA out of memory，前端应提示"当前系统排队人数较多，请稍后重试"，而不是抛出一堆英文代码。
- **现状**：`app.py` 全局异常处理器已返回 `{error, message, path}`，无需另建。

## 六、 运维、监控与安全
*防患于未然。*

### 结构化日志持久化
- 不要只用 `print()`。用 Python `logging` 将日志写入文件（`paperforge.log`），包含时间、请求 ID、耗时、报错堆栈，方便事后追查。
- **注意**：`logs/` 目前无轮转，可顺手加 `RotatingFileHandler`。

### 关键指标监控
- 本地桌面工具无需 Prometheus + Grafana。
- 可在日志中记录：GPU 显存水位、API 错误率、单次 OCR/Qwen 推理耗时。

### 数据隐私与安全
- **传输**：本地桌面工具无需 Nginx/HTTPS。
- **存储**：本地 SQLite 已足够，无需 AES 加密存储。
- **清理**：OCR 提取完成后，立即删除服务器上的临时图片，减少隐私泄露风险。

### 版本锁定
- 版本炸弹：llama.cpp 升级可能导致旧版 GGUF 不兼容。用 `requirements.txt` 锁死 Python 依赖，记录 llama.cpp 的确切版本号。
- **注意**：Docker 容器化按需取舍，非必做。

## 七、 真正需要新建的模块
*当前 PaperForge 缺失、必须从零建设的部分。*

### 1. 图片上传入口
- 当前仅有 `/api/upload-paper`、`/api/upload-paper/batch`、`/api/upload-paper/zip` 三个 PDF 入口，**没有任何图片 OCR 上传入口**。
- 需要新增：`/api/ocr/upload` 或类似端点，支持 PNG/JPG/TIFF 等图片格式。

### 2. OCR 文本清洗 + 结构化
- 对 OCR 输出进行清洗（去乱码、去冗余换行）。
- 提取结构化字段：标题、作者、摘要、关键词等。
- 输出格式需与 PaperForge 现有论文模型兼容。

### 3. 把 OCR→Qwen 流水线接到现有 TaskManager
- 复用 `TaskManager.submit()` 提交异步任务。
- 在 worker 中执行：图片 OCR → 文本清洗 → Qwen 分析 → 结果入库。
- 通过 `TaskManager.update_progress()` 推送 SSE 进度。

### 4. OCR 完成即删临时图片
- 在 worker 最后清理临时图片文件，保护隐私。
- 即使任务失败，也应在 `finally` 块中尝试清理。

## 八、 落地建议（优先级排序）
如果你准备开始实施，建议按以下顺序推进：

1. **第一步（跑通）**：先写好 `.bat` 脚本和 Python 调度，确保单机下 OCR → 关闭 → Qwen 的流程能稳定循环跑通。
2. **第二步（整合）**：将 Qwen 替换为调用 PaperForge 的 LLM Provider（`mock_api/llm`），结果挂 `TaskManager` SSE；论文入库 + 评价入库用事务包裹。
3. **第三步（异步与体验）**：挂现有 `TaskManager`（别新建队列），前端 SSE 收"识别中→分析中→完成"。
4. **第四步（缓存与日志）**：复用 `depth_eval_v4.py` 的 TTL 缓存模式；日志走 `logging` 写 `paperforge.log`（顺手加 `RotatingFileHandler`）。

> 把这份文档保存下来，在开发到对应模块时对照着检查，能帮你避开 90% 的坑！

## 九、Qwen/llama-server 托管模式与桌面 bat 冲突说明（2026-07-20）

PaperForge 现已内建 **llama-server 生命周期托管**（`mock_api/llama_server_manager.py` +
`mock_api/vram_scheduler.py`），对 8GB 显存卡实现 OCR ↔ Qwen 严格互斥。

### 行为
- **启动自启**：`qwen_autostart=True`（默认）时，PaperForge 启动即后台拉起 llama-server
  （固化桌面 `start-llama-dflash-wsl.bat` 的启动参数：DFlash 草稿模型、`-ngl 35`、
  ctx=8192、`--reasoning off` 等）。
- **手动兼容**：若 8080 已被外部 llama-server 占用（你手动开了 bat），PaperForge 复用、
  不重复拉起（port pre-check）。
- **互斥切换**：用 OCR 时自动 kill llama-server 释放显存；OCR 完成后自动拉回 Qwen。
- **冷启动提示**：首次拉起 CUDA kernel 编译需 3-5 分钟，期间 `/api/qwen/status` 返回
  `event.kind=loading/switching`，前端据此展示「模型加载中，预计 X 分钟」避免误判卡死。

### ⚠️ 桌面 bat 的 watchdog 冲突
`start-llama-dflash-wsl.bat` 自带 watchdog（`tasklist` 每 5 秒探活、崩溃自动重启）。
若你同时**双击该 bat 常开** + **PaperForge 也托管拉起**，会：
1. 两个 llama-server 抢 8080（后者因 port pre-check 自动退化为复用，不会双开——但
   一旦你手杀其中一个，watchdog 会立刻拉起，与 PaperForge 的托管意图打架）；
2. 显存被常驻占满，互斥调度失效（PaperForge shutdown 后 watchdog 又拉起）。

**建议（二选一）：**
- **方案 A（推荐）**：停止双击桌面 bat，完全交给 PaperForge 托管。要手动用 Qwen 时
  直接启动 PaperForge 即可（它会自启 llama-server）。
- **方案 B（兼容）**：保留 bat 手动开，但设 `qwen_autostart=False` 让 PaperForge 不托管
  自启；此时 PaperForge 检测到 8080 已开则复用，互斥切换时仍会 kill 该实例（bat watchdog
  可能又拉起——接受此行为或临时关掉 bat 窗口）。

### 配置（`.env` 可覆盖）
```
PAPERFORGE_QWEN_AUTOSTART=true        # 启动自启 llama-server
PAPERFORGE_VRAM_EXCLUSIVE=true        # OCR/Qwen 显存互斥（8GB 卡必开）
PAPERFORGE_LLAMA_SERVER_EXE=D:\llama-dflash-win\build-vs\bin\llama-server.exe
PAPERFORGE_LLAMA_SERVER_MODEL=D:\Qwen3.5-9B-Q3_K_M.gguf
PAPERFORGE_LLAMA_SERVER_DRAFT=D:\llama-b9878-bin-win-cuda-13.3-x64\qwen3.5-9b-dflash-Q5_K_M.gguf
PAPERFORGE_LLAMA_SERVER_PORT=8080
PAPERFORGE_LLAMA_SERVER_COLD_GRACE=360  # 冷启动宽限秒数
```
