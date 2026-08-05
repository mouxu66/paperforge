"""跨域共享并发锁（供 depth / reflection 等多个 router 共用）。

这些锁原本定义在 mock_api/main.py 模块级，抽取到此处以避免各 router
反向依赖 main.py 造成循环导入。

约定：
- depth_review_lock: threading.Lock — 序列化 DEPTH v4.1 审稿与 reflection
  评审的 dedup + self-heal + TaskManager.submit 逻辑。
- reflection_file_lock: threading.RLock — 防止同一 paper_id 的并发文件上传
  产生重复入库与重复 reflection 任务。

FastAPI 后台运行在 Uvicorn 的单进程线程池中，故 threading.Lock 足够，
无需 asyncio.Lock。
"""

from __future__ import annotations

import threading

# DEPTH v4.1 dedup 自愈：并发 POST 防双重重覆提交（同 paperId 同时刷两次）
# 被 depth 域（_submit_single_v4_review）和 reflection 域
# （/reflection/text, /reflection/run）共用。
depth_review_lock = threading.Lock()

# reflection 文件上传去重锁：防止同一 paper_id 的并发上传产生重复任务。
# 与 depth_review_lock 分离，避免 reflection 上传与 DEPTH v4.1 审稿互相阻塞。
# 使用 RLock 以支持同一线程在嵌套 helper 中重入。
reflection_file_lock = threading.RLock()
