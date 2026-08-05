# ADR-013: VRAM 调度重构 — 从 qwen↔ocr 互斥到 text-Qwen↔vision-Qwen 双模型仲裁

**状态**: Proposed
**日期**: 2026-07-31

## 背景（Context）

2026-07-27 OCR（PaddleOCR-VL）正式退役，figure 视觉理解改走外部 HTTP
视觉服务 `vision_http_url`（Qwen3-VL-4B，OpenAI 兼容的 llama-server，
HTTP-first）。现状与冲突：

- `vram_scheduler.py` 仍只建模 `qwen ↔ ocr` 互斥（`VRAMState.OCR_ACTIVE` /
  `QWEN_ACTIVE`）。`ocr_subprocess` 已是僵尸（实际无 OCR 抽取），但调度器仍在
  `request_ocr()/ocr_finished()` 里 shutdown OCR、autostart Qwen。
- 视觉服务是**外部 HTTP**，完全不受调度器管理；8GB 卡上 text-Qwen(8080) 与
  vision-Qwen(vision_http_url) 实际会抢同一张卡显存，但调度器**不仲裁**
  → OOM / 互杀风险。实证缺口：`figure_qwen._ask_vision_on_figure()` 直接
  `requests.post` 视觉端点，**完全不碰调度器**，无任何串行化。
- async/sync 双路径互不协调：`_async_acquire` 用 `asyncio.Queue(1)` 令牌桶，
  `_sync_acquire` 用 `threading.Lock`，二者共享 `_state` 但**各自独立互斥**
  → 一个 async 调用方和一个 sync 调用方可同时进入，等于没有仲裁。
- `settings.vram_exclusive` 描述仍是「OCR 与 Qwen 显存互斥」，语义过时。

## 决策（Decision）

将仲裁拓扑从 `qwen ↔ ocr` 重构为 `text-Qwen(8080) ↔ vision-Qwen(vision_http_url)`，
核心原则：**对本地 8080 的占用做生命周期协调 + 对 vision 调用做串行化令牌**
（vision 进程不可由本调度器 kill，故只能协调、不能强杀）。

1. **单一仲裁原语（消灭双路径互杀）**
   调度器内部权威状态机只由一把 `threading.Lock`（`_lock`）+ 一个 `Condition`
   守护；令牌（GPU 模型槽位）容量=1，跨 kind 互斥，同 kind 可变重入。
   async API（`VRAMContext`、`_async_acquire/_async_release`）一律通过
   `loop.run_in_executor(None, self._core_acquire/Release, ...)` 复用**同一把锁**
   的同步核心实现。不再存在第二套独立互斥。

2. **kind 枚举**：`"text"`（= 原 qwen，8080 托管 llama-server）与
   `"vision"`（= 原 ocr 的语义位，外部 HTTP Qwen3-VL-4B）。

3. **text 侧（可托管）**：与旧 Qwen 一致——`acquire("text")` 确保 8080 就绪
   （`llama_server_manager.ensure_started`），`vram_exclusive` 下与对侧互斥切换。

4. **vision 侧（不可托管，仅协调）**：
   - `acquire("vision")` **不**拉起/杀死 vision 进程；它只取串行化令牌、标记
     `VISION_ACTIVE`，并对内做单飞（令牌容量 1，多个 figure 并发调用被串行）。
   - 当 `vram_exclusive=True` 且判定 vision 与 8080 **同卡**（见第 6 点），
     `acquire("vision")` 会先 `llama_server_manager.shutdown()` 释放本端 8080
     显存（对称于旧「OCR 抢占时 shutdown Qwen」），把 GPU 让给 vision；
     `release("vision")` 后按 `qwen_autostart` 自动拉回 8080。
   - vision HTTP 调用失败/超时仍 fail-open 返回 `None`，不抛异常。

5. **vram_exclusive=False 语义**：**无保护模式**，明确文档化——任一 kind 直接
   放行，不取互斥令牌、不做 8080/视觉切换协调（仅 text 在需要且配置时仍
   `ensure_started`）；调用方自行承担并发 OOM 风险。保留该开关以兼容多卡/大显存
   用户与灰度回退。

6. **同卡探测（关键开关）**：解析 `vision_http_url` 的 host 与
   `llama_server_host` 比较。
   - **同机**（如 `127.0.0.1`/`localhost` 指向同一 GPU）：启用跨 kind 互斥切换
     （第 4 点逻辑生效）。
   - **异机/异卡**：二者无显存竞争 → vision 仅做自身串行化，text 不受影响，
     **不**再 shutdown 8080、不做跨 kind 等待。该探测让把 vision 放第二张卡/独立
     机器的用户自动消除 OOM 风险，无需改代码。

7. **API 重命名（向后兼容别名保留一轮）**：
   - 新增 `acquire(kind, timeout)` / `release(kind)`、`VRAMContext(kind)`、
     `vram_guard(kind)`、`request_text(wait)`、`request_vision()`。
   - 旧 `request_qwen/request_ocr/ocr_finished` 与 `VRAMContext("qwen"|"ocr")`
     保留为**已废弃别名**（`request_qwen→request_text`、`request_ocr→request_vision`、
     `ocr_finished→vision_finished`），映射一层后下一轮删除。
   - `VRAMState` 改为 `IDLE / TEXT_ACTIVE / VISION_ACTIVE / SWITCHING_TO_TEXT`，
     旧 `OCR_ACTIVE/SWITCHING_TO_QWEN` 保留别名。
   - `get_status()` 事件 kind 增加 `vision`；`settings.vram_exclusive` 描述更新。

## 后果（Consequences）

- 收益：彻底把「抢显存的 vision 调用」纳入仲裁；同卡场景下 text 与 vision
  不再并发占用 GPU；双路径互杀 bug 根除；异卡部署零仲裁开销。
- 代价：调度器需理解 vision 端点（仅元数据，不接管进程）；`acquire("vision")`
  在 co-located 下会 shutdown 本端 8080，带来一次 8080 冷启动延迟（与旧 OCR
  行为对称，可接受）。
- **残存风险（必须文档化）**：若 vision 服务在**任意括号之外**被常驻拉起且
  同卡，调度器无法 kill 它，后续 `acquire("text")` 启动 8080 仍可能 OOM。调度器
  只能保证「括号内的并发不冲突」，不能消除「括号外的被动双驻留」。缓解（非本 ADR
  核心范围，列为后续候选）：
  (a) 运维把 vision 放独立 GPU/机器（推荐，自动触发异卡免仲裁）；
  (b) 后续 ADR 引入 `vision_start_cmd/vision_stop_cmd` 钩子，使调度器对 vision
      也可托管启停（届时本 ADR 的「不可强杀」前提可被推翻，届时升级为进程级互斥）。
- 兼容性：旧别名保留一轮，调用点可分批迁移；`vram_exclusive=False` 提供即时回退。

## 备选方案（Alternatives considered）

- 进程级强杀 vision（同 OCR 旧做法）：与背景约束「vision 是外部服务、调度器
  无法 kill 其进程」冲突，且会误杀用户自管服务，否决（留作未来钩子 ADR）。
- 把 vision 合并进 8080 同一 llama-server：text-Qwen 为 text-only 无 mmproj，
  且合并会与 DEPTH 推理争槽位，否决。
- 仅对 vision 做客户端串行化、完全不碰 8080：无法阻止 co-located 下 8080 与
  vision 同驻 OOM，防护不完整，否决。
- 保留 async/sync 两套锁但加桥接：仍有两个事实权威，易回归，否决（统一到单锁）。

## 调用点迁移清单（供 Cody 实施）

- `mock_api/llm/figure_qwen.py`：`_request_qwen_vram()` → `request_text`；
  `_ask_vision_on_figure()` 必须在 vision HTTP 调用外包裹 `acquire("vision")/release("vision")`
  （当前完全未纳入调度）。
- `mock_api/workers/figures.py:117`：`vram_guard("ocr")` → `vram_guard("vision")`
  （figure 理解即 vision 括号）。
- `mock_api/workers/figure_understanding.py:74`：`_wait_for_vram_available` 的
  busy 状态集合更新为 `VISION_ACTIVE/SWITCHING_TO_TEXT`。
- `mock_api/depth_eval_v4.py:575`：`request_qwen(wait=False)` → `request_text(wait=False)`。
- `mock_api/pdf_parser.py:543,623` 与 `mock_api/routers/papers.py:297,302`、
  `mock_api/routers/reflection.py:305,310`：OCR 已退役，其为僵尸括号；本 ADR
  仅要求改调新别名（`request_ocr`→`request_vision` 或随 OCR 端点废弃移除），
  具体「rerun OCR」端点是否下线属独立决策（见人工确认点）。
- `mock_api/llama_server_manager.py:11` 注释「与 OCR 的互斥…」更新为「text↔vision」。
- `scripts/pipeline_figure_understanding.py:62` 的 `vram_bracket` 已自述废弃无操作，
  括号责任在 worker/figure 层（上列）。

## 灰度与回滚

- 灰度：先合 ADR 与别名（旧 API 仍可用），逐个调用点迁移；通过
  `vram_exclusive=False` 即时关闭仲裁做功能开关。
- 回滚：保留旧 `request_qwen/request_ocr/ocr_finished` 别名与 `VRAMState` 旧值
  一轮；若新仲裁异常，可临时设 `PAPERFORGE_VRAM_EXCLUSIVE=0` 退回无保护现状，
  或 revert ADR-013 单一调用点改动。下一轮发版再删别名。
