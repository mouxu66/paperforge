"""LLM 工厂：从 SQLite llm_configs 表加载配置，动态创建 Provider 实例。

设计要点：
- 所有模型配置来自 DB（llm_configs 表），不再使用硬编码 AVAILABLE_MODELS。
- Provider 类型由 api_url 关键词自动识别：
    含 "openai.com"     → openai
    含 "bigmodel/zhipu" → zhipu
    含 "deepseek"       → deepseek
    其他                → openai（OpenAI 兼容协议，覆盖 vLLM/Ollama/各类代理）
- 运行时切换：switch(config_id) 从 DB 重新加载配置并重建 Provider。
- 线程安全：切换操作加锁。
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional

from .base import BaseLLMProvider
from .deepseek_provider import DeepSeekProvider
from .openai_provider import OpenAIProvider
from .zhipu_provider import ZhipuProvider

# provider 关键词映射表（按优先级从高到低匹配）
_PROVIDER_KEYWORDS: List[tuple] = [
    ("openai.com", "openai"),
    ("bigmodel", "zhipu"),
    ("zhipu", "zhipu"),
    ("deepseek", "deepseek"),
]

_PROVIDER_CLASSES: Dict[str, type] = {
    "openai": OpenAIProvider,
    "zhipu": ZhipuProvider,
    "deepseek": DeepSeekProvider,
}


def detect_provider(api_url: str) -> str:
    """根据 API 地址关键词自动识别 Provider 类型。

    不匹配任何关键词时，默认返回 "openai"（OpenAI 兼容协议），
    覆盖 vLLM / Ollama / OneAPI / 各类 OpenAI 兼容代理。
    """
    url_lower = (api_url or "").lower()
    for keyword, provider in _PROVIDER_KEYWORDS:
        if keyword in url_lower:
            return provider
    return "openai"


def _mask_api_key(key: str) -> str:
    """脱敏 API Key，只保留前 4 位和后 4 位。"""
    if not key or len(key) <= 8:
        return "***" if key else ""
    return f"{key[:4]}***{key[-4:]}"


class LLMFactory:
    """LLM Provider 工厂（单例风格，进程内共享）。

    从 DB 加载配置，支持运行时切换。首次调用时懒加载。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Optional[BaseLLMProvider] = None
        self._current_config_id: Optional[int] = None
        self._current_label: str = ""

    def _load_configs_from_db(self, enabled_only: bool = False) -> list:
        """从 DB 加载模型配置列表。"""
        from ..database import SessionLocal
        from ..models import LLMConfig

        db = SessionLocal()
        try:
            query = db.query(LLMConfig)
            if enabled_only:
                query = query.filter(LLMConfig.enabled == True)  # noqa: E712
            return query.order_by(LLMConfig.id).all()
        finally:
            db.close()

    def _load_config_by_id(self, config_id: int):
        """从 DB 加载单条配置。"""
        from ..database import SessionLocal
        from ..models import LLMConfig

        db = SessionLocal()
        try:
            return db.query(LLMConfig).filter(LLMConfig.id == config_id).first()
        finally:
            db.close()

    def _build_provider(self, config) -> BaseLLMProvider:
        """根据 DB 配置行构建 Provider 实例。"""
        provider_name = detect_provider(config.api_url)
        cls = _PROVIDER_CLASSES.get(provider_name, OpenAIProvider)
        return cls(
            api_key=config.api_key or "",
            model=config.model_id,
            base_url=config.api_url,
        )

    def _init_provider(self, config_id: int) -> None:
        """从 DB 加载指定 config_id 的配置并初始化 Provider。"""
        config = self._load_config_by_id(config_id)
        if config is None:
            raise ValueError(f"模型配置不存在: id={config_id}")
        if not config.enabled:
            raise ValueError(f"模型已禁用: {config.display_name}")
        provider = self._build_provider(config)
        self._current = provider
        self._current_config_id = config.id
        self._current_label = config.display_name

    def get_provider(self) -> BaseLLMProvider:
        """获取当前 Provider。若未初始化，自动选择第一个启用的配置。"""
        with self._lock:
            if self._current is None:
                configs = self._load_configs_from_db(enabled_only=True)
                if not configs:
                    raise RuntimeError(
                        "未配置任何大模型，请在「模型管理」页面添加模型配置"
                    )
                self._init_provider(configs[0].id)
            assert self._current is not None
            return self._current

    def switch(self, config_id_str: str) -> BaseLLMProvider:
        """运行时切换到指定 config_id 的模型。"""
        with self._lock:
            old_id = self._current_config_id
            try:
                config_id = int(config_id_str)
                self._init_provider(config_id)
                return self._current  # type: ignore[return-value]
            except Exception:
                self._current_config_id = old_id
                if old_id is not None and self._current is None:
                    try:
                        self._init_provider(old_id)
                    except Exception:
                        pass
                raise

    def current_model_value(self) -> str:
        """返回当前配置的 id（字符串形式）。"""
        with self._lock:
            return str(self._current_config_id) if self._current_config_id else ""

    def current_label(self) -> str:
        """返回当前模型的展示名称。"""
        with self._lock:
            return self._current_label

    def available_models(self) -> List[dict]:
        """返回所有已启用的模型列表（供前端下拉菜单使用）。"""
        configs = self._load_configs_from_db(enabled_only=True)
        return [
            {
                "value": str(c.id),
                "label": c.display_name,
                "provider": detect_provider(c.api_url),
                "model": c.model_id,
            }
            for c in configs
        ]


_factory_instance: Optional[LLMFactory] = None
_factory_lock = threading.Lock()


def get_factory() -> LLMFactory:
    """获取进程级 LLMFactory 单例。"""
    global _factory_instance
    if _factory_instance is None:
        with _factory_lock:
            if _factory_instance is None:
                _factory_instance = LLMFactory()
    return _factory_instance
