# ADR-007: OCR 子进程隔离（llama.cpp 不进主进程）

**状态**: Accepted
**日期**: 2026-07-21（Accepted 2026-07-23）

## 背景（Context）
- llama.cpp 视觉 OCR 模型（~3-4GB 显存）以原生 C 扩展加载，历史上在主进程内偶发 Segfault，直接拖垮 Web 服务。
- 并发首请求会在主进程内多次加载该模型，导致多份 GB 级权重常驻显存、OOM。
- 主进程常驻 Qwen（~8.5G），与 OCR 同进程加载更易互相干扰。

## 决策（Decision）
- 把 llama.cpp OCR 的加载与推理隔离到独立子进程（`mock_api/ocr_subprocess.py`）。
- 主进程**永不** `import llama_cpp`，仅通过 `multiprocessing.Pipe` 与子进程收发图片/文本。
- `OCRManager` 单例：懒启动、锁序列化 send/recv（线程安全）、子进程崩溃（Segfault/OOM）自愈重启、SIGTERM 优雅退出。
- 异常以 `OCRWorkerError` 上报，上层降级到 tesseract。

## 后果（Consequences）
- 收益：主进程对 C 层崩溃免疫；模型仅一份；OCR 故障可隔离恢复。
- 代价：跨进程通信延迟；Pipe 单通道需加锁串行；运维需理解子进程生命周期。

## 备选方案（Alternatives considered）
- 线程级隔离：无法防御 C 层 Segfault，进程仍会崩，否决。
- 独立微服务/容器：隔离更彻底但引入端口管理与部署复杂度，对单机桌面过重。
- 主进程直接加载并规避：即现状问题，否决。
