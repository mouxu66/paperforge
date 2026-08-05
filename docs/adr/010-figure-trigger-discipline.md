# ADR-010: figure-trigger 铁律（PAPERFORGE_DISABLE_FIGURE_TRIGGER）

**状态**: Accepted（2026-07-31 重评定稿：保留铁律，理由变更；原 2026-07-30 版本为 `Proposed` 待重评）
**日期**: 2026-07-30（铁律确立于 415 篇批量评估事故后）；2026-07-31 重评定稿

## 背景（Context）
- 415 篇批量 DEPTH 评估（`batch_eval_415`）曾因 worker 内联加载 PaddleOCR-VL 与文本 Qwen 互斥而触发 Segfault，直接拖垮整个批量审稿进程。`mock_api/depth_tasks.py` 中记有「批量 DEPTH 跑须设 `PAPERFORGE_DISABLE_FIGURE_TRIGGER=1`」，figure 视觉作为 bulk 之外的独立 pass 触发（`routers/figures.py`）。
- **原根因（已消失）**：当时 OCR 以进程内 llama.cpp 子进程加载 PaddleOCR-VL，与常驻文本 Qwen 在同一 worker 进程内争抢显存/加载路径，互斥加载即崩溃（参见 ADR-007 OCR 子进程隔离、ADR-008 VRAM 互斥调度器）。
- **2026-07-27 进展**：OCR（PaddleOCR-VL）已退役，figure 视觉改走 `PAPERFORGE_VISION_HTTP_URL`（Qwen3-VL-4B，外部 llama-server HTTP 端点，端口 8082）。`mock_api/llm/figure_qwen.py` 已移除 PaddleOCR-VL 回退路径——即原「进程内 PaddleOCR-VL 与 Qwen 互斥」的 **Segfault 根因已于 2026-07-27 退役 OCR 后消失**。
- **重评结论（2026-07-31）**：原 Segfault 根因虽已消失，但新版拓扑引入新的显存争抢风险，铁律仍应**保留**，理由变更如下：
  - 新拓扑：文本 Qwen（`llama_server_host` @8080，受 `vram_scheduler` 管理）与视觉 Qwen3-VL-4B（`vision_http_url` @8082，**外部 llama-server，不受 PaperForge 调度器管理**）共享同一张 8GB 显存。
  - 代码现状核验：`mock_api/depth_tasks.py:311` 的 bulk DEPTH 路径**仅在 `PAPERFORGE_DISABLE_FIGURE_TRIGGER=1` 时跳过 figure 自动派发，并非代码硬禁用**；独立 figure 任务（`workers/figures.py` → `llm/figure_qwen.py` 的 `_ask_vision_on_figure`）走外部 vision HTTP，**不经过 VRAM 调度器，也无任何串行化锁**（仅 cache 用 `threading.Lock`）。
  - 因而 bulk DEPTH（常驻占用文本 Qwen）与独立 figure 任务（常驻占用 vision Qwen）并发时，两个外部进程在 8GB 卡上争抢显存，仍可能导致推理退化（高延迟 / OOM / 摘要缺失），只是表现不再是 segfault。

## 决策（Decision）
- **保留** `PAPERFORGE_DISABLE_FIGURE_TRIGGER` 开关（`=1` 时批量 DEPTH 跳过 figure 视觉 pass，figure 改作 bulk 之外的独立 pass），作为回归逃生舱与显存隔离手段。
- **铁律理由变更**：从「防止进程内 PaddleOCR-VL 与 Qwen 互斥 segfault」改为「**防止 bulk DEPTH（文本 Qwen @8080）与独立 figure 任务（视觉 Qwen3-VL-4B @8082）并发争抢同一 8GB 显存导致推理退化**」。
- **运维默认**：批量/确定性评估（如 `batch_eval_415`、回填、校准回归）须设 `PAPERFORGE_DISABLE_FIGURE_TRIGGER=1`，将 figure 视觉隔离为独立的低并发 pass；交互/单篇场景可不设，获得 figure 增强。
- 原 `Proposed` 待重评状态关闭：经代码现状核验（bulk 未硬禁用、vision 无串行化保护、外部服务不受调度器管理），铁律维持为 `Accepted`。

## 后果（Consequences）
- 收益：保留显式逃生舱；批量评估与交互场景的 figure 视觉能力可分离，避免 8GB 卡上 text/vision Qwen 显存争抢导致退化；即便未来 vision 服务纳入调度或有更大显存，开关仍在，可随时回退或放宽。
- 代价：批量 DEPTH 默认不触发 figure 视觉增强（现走 HTTP，不再 segfault，但仍受显存争抢影响），需以独立 pass 补齐 figure 摘要。

## 备选方案（Alternatives considered）
- 完全移除该开关：回归未测试路径风险高，保留更安全，否决。
- 交由 VRAM 互斥调度器（ADR-008）自动协调 bulk 与 figure 的显存切换：当前 vision 是**外部服务、不受调度器管理**（`figure_qwen._ask_vision_on_figure` 直接 HTTP 调用，无 `request_qwen`/`request_ocr` 包裹），调度器无法覆盖，故不可行；若未来 vision 服务纳入进程内调度，可重议。

## 相关文件
- `mock_api/depth_tasks.py`（`PAPERFORGE_DISABLE_FIGURE_TRIGGER` / `figure_coverage=="missing"` 派发逻辑 @311）
- `mock_api/workers/figures.py`、`mock_api/routers/figures.py`
- `mock_api/llm/figure_qwen.py`（`_ask_vision_on_figure` 走 `vision_http_url` HTTP，无 VRAM 调度包裹）
- `mock_api/settings.py`（`vision_http_url`、`llama_server_host` 默认 127.0.0.1）
- ADR-007（OCR 子进程隔离，已退役）、ADR-008（VRAM 互斥调度器）
- 安全部署注记见 RUNBOOK §6（vision llama-server 须 `--host 127.0.0.1` 启动）
