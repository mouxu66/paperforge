"""PaperForge 统一 LLM 网关。

通过 BaseLLMProvider 抽象不同大模型提供商（OpenAI / 智谱 / DeepSeek 等），
上层业务接口（/ask、/generate、/search/semantic）只依赖 factory.get_provider()，
与具体提供商解耦，支持运行时切换。
"""

from .base import BaseLLMProvider, ChatMessage, ChatResult
from .errors import LLMError, classify_llm_error
from .factory import LLMFactory, get_factory, reset_provider_for_testing, set_provider_for_testing

__all__ = [
    "BaseLLMProvider",
    "ChatMessage",
    "ChatResult",
    "LLMError",
    "classify_llm_error",
    "LLMFactory",
    "get_factory",
    "set_provider_for_testing",
    "reset_provider_for_testing",
]
