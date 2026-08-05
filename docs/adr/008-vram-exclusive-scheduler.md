# ADR-008: VRAM 互斥调度器（8GB 卡刚需）

**状态**: Accepted
**日期**: 2026-07-21（Accepted 2026-07-23）

## 背景（Context）
- 8GB 显存无法同时容纳 Qwen（主+草稿 ~8.5G）与 OCR（~3-4G）。
- 二者同驻必触发 OOM / CUDA 上下文崩溃，且切换时机不确定。

## 决策（Decision）
- 进程内状态机仲裁同一时刻仅有其一在显存：`IDLE / QWEN_ACTIVE / OCR_ACTIVE`（`mock_api/vram_scheduler.py`）。
- 默认 `vram_exclusive=True` 严格互斥；Qwen 常驻，OCR 临时抢占，`ocr_finished()` 后自动拉回 Qwen（`qwen_autostart`）。
- 切换即时、无空闲超时；`request_qwen()` 不抛异常，缺失时记 `loading/switching` 事件供前端 `/api/qwen/status` 轮询；DEPTH 连接失败按原逻辑降级空串。

## 后果（Consequences）
- 收益：彻底消除 VRAM OOM；前端可展示「模型加载中」避免误判卡死。
- 代价：OCR 触发一次显存切换延迟；Qwen 与 OCR 无法真正并行；开关关闭即退回互不感知的现状。

## 备选方案（Alternatives considered）
- 更大显存 GPU：用户硬件不可控，否决。
- 纯 CPU OCR（tesseract 已作兜底）：精度/速度差，仅作降级而非主方案。
- 双模型量化到同驻：质量损失且脆弱，否决。
