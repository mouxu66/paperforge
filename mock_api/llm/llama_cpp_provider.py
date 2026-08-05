"""本地 llama.cpp 提供商实现。

通过 llama-cpp-python 加载本地 GGUF 模型，支持离线推理。
可与 OpenAI / Zhipu / DeepSeek 等在线 Provider 并存，
由 LLMFactory 根据配置自动选择。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from pathlib import Path

from ..retry_utils import llm_retry
from .base import BaseLLMProvider, ChatMessage, ChatResult

logger = logging.getLogger(__name__)


class LlamaCppProvider(BaseLLMProvider):
    """本地 llama.cpp / GGUF 模型提供商。

    参数:
        model_path: GGUF 文件绝对路径。
        mmproj_path: 多模态投影文件路径（视觉模型需要），可选。
        n_ctx: 上下文长度。
        n_threads: CPU 线程数，None 时自动。
        n_gpu_layers: GPU 层数，-1 表示全部卸载到 GPU。
        temperature: 采样温度。
        max_tokens: 最大生成 token 数。
        verbose: 是否打印 llama.cpp 内部日志。
        seed: 采样随机种子，None 时由 llama.cpp 自行决定（默认随机）。
            设为固定值（如 0）配合 temperature=0 可获得可复现输出。
    """

    provider_name = "llama_cpp"

    def __init__(
        self,
        model_path: str,
        mmproj_path: str | None = None,
        n_ctx: int | None = None,
        n_threads: int | None = None,
        n_gpu_layers: int | None = None,
        temperature: float = 0.1,
        max_tokens: int = 512,
        verbose: bool = False,
        seed: int | None = None,
    ) -> None:
        self.model_path = model_path
        self.mmproj_path = mmproj_path
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.verbose = verbose
        self.seed = seed

        # 优先使用 compute_mode 预设参数，未指定时回退到默认值
        from ..compute_mode import to_kwargs

        preset_kwargs = to_kwargs()
        self.n_ctx = n_ctx if n_ctx is not None else preset_kwargs.get("n_ctx", 4096)
        # n_threads 优先用显式参数，否则取算力预设（预设已按机器核数精调，
        # 例如 RTX5060 预设 threads=4、低内存预设 threads=2）。
        # 原实现把 n_threads 直接丢弃，导致 llama.cpp 默认占满全部物理核心，
        # 在 CPU 推理机上会饿死 Web/DB/OCR 进程。
        self.n_threads = n_threads if n_threads is not None else preset_kwargs.get("n_threads")
        self.n_gpu_layers = (
            n_gpu_layers if n_gpu_layers is not None else preset_kwargs.get("n_gpu_layers", 0)
        )
        # 透传性能相关预设（原实现只保留 n_ctx/n_gpu_layers，丢弃了 n_batch/n_threads，
        # 导致 CPU 推理 batch 退回默认 512、占满全部核心）。
        # 仅透传 Llama() 明确支持的构造参数，避免未知 kwarg 触发加载失败。
        _FORWARD_KEYS = ("n_batch", "n_ubatch", "seed", "use_mlock")
        self._preset_kwargs = {
            k: v for k, v in preset_kwargs.items() if k in _FORWARD_KEYS and v is not None
        }
        # n_threads 已在上文单独设置，避免与透传集合重复
        self._preset_kwargs.pop("n_threads", None)
        # seed 仅在显式指定时透传（默认 None 保持原随机行为；
        # 严格可复现由调用方设置 seed，配合温度=0）。
        if seed is not None:
            self._preset_kwargs["seed"] = seed

        self._llama: object | None = None
        self._load_lock = threading.Lock()

    def _load(self):
        """延迟加载 Llama 实例（线程安全）。"""
        if self._llama is not None:
            return self._llama

        with self._load_lock:
            if self._llama is not None:
                return self._llama

            try:
                from llama_cpp import Llama
            except ImportError as e:
                raise RuntimeError("llama-cpp-python 未安装，无法使用本地模型") from e

            if not Path(self.model_path).exists():
                raise FileNotFoundError(f"GGUF 模型文件不存在: {self.model_path}")

            kwargs: dict = {
                "model_path": self.model_path,
                "n_ctx": self.n_ctx,
                "n_gpu_layers": self.n_gpu_layers,
                "verbose": self.verbose,
            }
            kwargs.update(self._preset_kwargs)
            if self.n_threads is not None:
                kwargs["n_threads"] = self.n_threads
            if self.mmproj_path:
                if not Path(self.mmproj_path).exists():
                    raise FileNotFoundError(f"mmproj 文件不存在: {self.mmproj_path}")
                kwargs["clip_model_path"] = self.mmproj_path

            self._llama = Llama(**kwargs)
            logger.info("llama.cpp 模型加载完成: %s", self.model_path)
            return self._llama

    @llm_retry
    def chat(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> ChatResult:
        llama = self._load()

        # 转换为 llama-cpp-python 的 chat 格式
        chat_messages = [m.to_dict() for m in messages]
        temp = temperature if temperature is not None else self.temperature
        max_tok = max_tokens if max_tokens is not None else self.max_tokens

        response = llama.create_chat_completion(
            messages=chat_messages,
            temperature=temp,
            max_tokens=max_tok,
            **kwargs,
        )

        content = response["choices"][0]["message"].get("content", "")
        usage = response.get("usage", {})
        return ChatResult(
            content=content,
            model=Path(self.model_path).name,
            provider=self.provider_name,
            usage=usage,
        )

    def chat_stream(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs,
    ) -> Iterator[str]:
        llama = self._load()

        chat_messages = [m.to_dict() for m in messages]
        temp = temperature if temperature is not None else self.temperature
        max_tok = max_tokens if max_tokens is not None else self.max_tokens

        stream = llama.create_chat_completion(
            messages=chat_messages,
            temperature=temp,
            max_tokens=max_tok,
            stream=True,
            **kwargs,
        )
        for chunk in stream:
            delta = chunk["choices"][0]["delta"].get("content", "")
            if delta:
                yield delta
