"""重试工具：为 LLM 等耗时、易抖动的调用提供 tenacity 指数退避装饰器。

使用方式:
    from mock_api.retry_utils import llm_retry

    @llm_retry
    def chat(...):
        ...
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .llm.base import LocalLLMTimeout

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# 不重试的确定性异常：缺少依赖、文件不存在、输入非法、已加密/损坏 PDF 等
#
# LocalLLMTimeout 属于「重试有害」而非「确定性失败」：本机单实例 llama-server
# 慢速生成时重试只会继续霸占容量为 1 的并发信号量，把后续请求全堵死
# （2026-08-05 孤儿级联事故，详见 llm/base.py::LocalLLMTimeout 文档）。
# 远程 API 超时不在此列，仍照常重试。
_NON_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ImportError,
    FileNotFoundError,
    ValueError,
    NotImplementedError,
    PermissionError,
    LocalLLMTimeout,
)


def _log_retry_attempt(retry_state: RetryCallState) -> None:
    """tenacity 每次重试前的回调：记录警告日志。"""
    exc = retry_state.outcome.exception() if retry_state.outcome else None  # type: ignore[union-attr]
    logger.warning(
        "[%s] 第 %d 次尝试失败，%s 后重试: %s",
        retry_state.fn.__name__ if retry_state.fn else "<unknown>",
        retry_state.attempt_number,
        f"{retry_state.next_action.sleep:.2f}s" if retry_state.next_action else "?",
        exc,
    )


def _make_retry(
    *,
    max_attempts: int,
    min_wait: float,
    max_wait: float,
    multiplier: float,
) -> Callable[[F], F]:
    """构造通用 tenacity 重试装饰器。

    重试条件：
    - 捕获所有 Exception，但排除 _NON_RETRYABLE_EXCEPTIONS 中的确定性异常。
    - 若异常链中包含非重试异常，也不重试（避免对 ImportError/FileNotFoundError 包装后重试）。
    """

    def _should_retry(exc: BaseException) -> bool:
        """判断某个异常是否值得重试。"""
        if isinstance(exc, _NON_RETRYABLE_EXCEPTIONS):
            return False
        # 检查异常链中是否包含非重试异常
        cause = exc.__cause__
        while cause is not None:
            if isinstance(cause, _NON_RETRYABLE_EXCEPTIONS):
                return False
            cause = cause.__cause__
        return True

    def decorator(func: F) -> F:
        @retry(
            reraise=True,
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=multiplier, min=min_wait, max=max_wait),
            retry=retry_if_exception(_should_retry),
            before_sleep=_log_retry_attempt,
        )
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


# LLM/Qwen 重试策略：本地或在线 API 网络抖动
# 3 次尝试，1s/2s/4s 指数退避，最长等 8s
llm_retry = _make_retry(max_attempts=3, min_wait=1, max_wait=8, multiplier=1)
