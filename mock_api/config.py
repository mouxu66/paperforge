"""PaperForge 全局配置。

模型配置已迁移至 SQLite llm_configs 表（见 models.py / factory.py），
本文件仅保留运行时开关。
"""
from __future__ import annotations

import os

# 是否启用模型切换功能（前端通过 /api/model/current 的 enabled 字段感知）
MODEL_SWITCH_ENABLED = os.getenv("MODEL_SWITCH_ENABLED", "true").lower() == "true"

# 教师版打包接口的安全开关：仅开发环境允许调用
IS_DEV_ENV = os.getenv("ENV", "development") == "development"


# ---------------------------------------------------------------------------
# arXiv 定时拉取配置（子任务 6）
# ---------------------------------------------------------------------------
# 关键词列表：逗号分隔。为空则不启动定时拉取。
# 设置方法：export ARXIV_AUTO_FETCH_KEYWORDS="LoRA,transformer,RLHF"
ARXIV_AUTO_FETCH_KEYWORDS: list[str] = [
    kw.strip()
    for kw in os.getenv("ARXIV_AUTO_FETCH_KEYWORDS", "").split(",")
    if kw.strip()
]

# 拉取间隔（小时），默认 24 小时一次。最小 1 小时，避免过于频繁被 arXiv 限流。
try:
    _interval = int(os.getenv("ARXIV_AUTO_FETCH_INTERVAL", "24"))
except ValueError:
    _interval = 24
ARXIV_AUTO_FETCH_INTERVAL = max(_interval, 1)

# 每个关键词每次拉取的最新论文数量（默认 5）
try:
    ARXIV_AUTO_FETCH_MAX_RESULTS = max(int(os.getenv("ARXIV_AUTO_FETCH_MAX_RESULTS", "5")), 1)
except ValueError:
    ARXIV_AUTO_FETCH_MAX_RESULTS = 5
