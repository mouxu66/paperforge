"""Figure semantic summary client (VLM).

Routes a figure's semantic summary to the local multimodal model when a
rendered image is available, falling back to the text-only Qwen endpoint
otherwise.

Vision-first rationale: the 8080 llama-server is text-only, while figure
understanding is delegated to an externally managed Qwen3-VL HTTP endpoint.
The rendered figure is sent there with its caption; no local OCR model is
loaded in the PaperForge process.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import threading
import time
from pathlib import Path

import requests

from mock_api.settings import get_settings

logger = logging.getLogger(__name__)

# TTL memory cache for figure understanding results (seconds). 0 disables cache.
_QWEN_CACHE_TTL = 300
_QWEN_CACHE_MAX_SIZE = 256
_qwen_cache: dict[int, tuple[float, str]] = {}
_qwen_cache_lock = threading.Lock()

# 云端视觉并发信号量（懒初始化，取自 settings.glm_vision_max_concur）。
_CLOUD_VISION_SEM: threading.Semaphore | None = None


def _cloud_vision_sem() -> threading.Semaphore:
    """懒初始化云端视觉并发信号量（默认 4，受 settings.glm_vision_max_concur 控制）。"""
    global _CLOUD_VISION_SEM
    if _CLOUD_VISION_SEM is None:
        try:
            from mock_api.settings import get_settings

            n = max(1, int(get_settings().glm_vision_max_concur or 4))
        except Exception:  # noqa: BLE001 - settings 异常隔离
            n = 4
        _CLOUD_VISION_SEM = threading.Semaphore(n)
    return _CLOUD_VISION_SEM


def _qwen_cache_key(ocr_text: str, figure_path: str | None) -> int:
    """Cache key for ask_qwen results.

    Based on figure file stat (size + mtime) and OCR text, so re-extracted
    figures with identical content hit the cache while regenerated figures
    (different file content) recompute. The result is independent of the text
    Qwen endpoint URL, so the same vision result is cached regardless of base_url.
    """
    stat_part = ""
    if figure_path:
        try:
            stat = Path(figure_path).stat()
            stat_part = f"{stat.st_size}:{stat.st_mtime}"
        except Exception:
            stat_part = figure_path
    raw = f"{ocr_text}:{stat_part}"
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest(), 16) % (2**63)


def _cache_get(key: int) -> str | None:
    if _QWEN_CACHE_TTL <= 0:
        return None
    with _qwen_cache_lock:
        entry = _qwen_cache.get(key)
        if entry is None:
            return None
        ts, content = entry
        if time.monotonic() - ts > _QWEN_CACHE_TTL:
            del _qwen_cache[key]
            return None
        return content


def _cache_set(key: int, content: str) -> None:
    if _QWEN_CACHE_TTL <= 0:
        return
    with _qwen_cache_lock:
        if len(_qwen_cache) >= _QWEN_CACHE_MAX_SIZE:
            oldest = min(_qwen_cache, key=lambda k: _qwen_cache[k][0])
            del _qwen_cache[oldest]
        _qwen_cache[key] = (time.monotonic(), content)


def _request_text_vram() -> None:
    """Notify the VRAM scheduler before invoking the text Qwen endpoint.

    Guarantees text-Qwen holds the VRAM lock. Fail-open: Qwen should still work if
    the scheduler is unavailable.
    """
    try:
        from mock_api.vram_scheduler import get_vram_scheduler

        # request_qwen is the text-Qwen compatibility name; it does not refer to OCR.
        get_vram_scheduler().request_qwen(wait=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to acquire text-Qwen VRAM lock: %s", exc)


def _ask_vision_on_figure(
    figure_path: str,
    caption_text: str = "",
    timeout: int = 120,
) -> str | None:
    """Send the rendered figure image to the local multimodal vision model
    (Qwen3-VL-4B via HTTP) and return
    a semantic summary.

    HTTP-first (2026-07-26): 优先走 ``vision_http_url``（llama-server 服务的
    Qwen3-VL-4B），解决进程内 llama_cpp 未安装导致 qwen_summary 96.7% 缺失问题。
    未配置或失败时返回空，由调用方执行安全的 caption/text fallback。

    Returns ``None`` on any failure (fail-open) so the caller can fall back to
    the text Qwen or leave the summary empty.
    """
    path = Path(figure_path)
    if not path.exists():
        logger.warning("[figure] 图文件不存在，跳过视觉理解: %s", figure_path)
        return None
    try:
        img_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[figure] 读图失败: %s", exc)
        return None

    # Cross-reference with caption and source context, not the OCR text itself,
    # to avoid reinforcing OCR errors (P0-2).
    caption_part = f"图注：{caption_text[:400]}" if caption_text else ""
    prompt = (
        "这是一张论文中的实验图。请尽量全面地用中文描述这张图：\n"
        "1. 图的类型（曲线图/柱状图/散点图/热力图/架构图/流程图等）；\n"
        "2. 横轴与纵轴各自代表什么（含单位）；\n"
        "3. 图例与各组对比的含义；\n"
        "4. 数据随自变量变化的主要趋势；\n"
        "5. 这张图支撑的结论。\n"
        "图中若有文字/标签请一并识别。\n"
        f"{caption_part}"
    )

    # ── HTTP-first: Qwen3-VL-4B via vision_http_url ──────────────
    try:
        from mock_api.settings import get_settings

        vision_url = get_settings().vision_http_url
    except Exception:  # noqa: BLE001
        vision_url = None

    if vision_url:
        _st = get_settings()
        url = f"{vision_url.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": _st.vision_model or "qwen3-vl",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 1024,
            "stream": False,
        }
        try:
            from mock_api.vram_scheduler import vram_guard

            # 关键修复（ADR-013）：vision HTTP 调用纳入 VRAM 仲裁，与 8080 在
            # 8GB 卡上抢显存时串行化；同卡 exclusive 模式下 acquire 会先让出 8080。
            # 重入安全：外层已持 vision 令牌时（vram_bracket_external）自动重入计数。
            headers = {"Content-Type": "application/json"}
            if _st.vision_api_key:
                headers["Authorization"] = f"Bearer {_st.vision_api_key}"
            with vram_guard("vision"):
                resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"].get("content", "")
            if text.strip():
                logger.debug("[figure] Qwen3-VL-4B HTTP 视觉理解成功")
                return text.strip()
            logger.warning("[figure] Qwen3-VL-4B HTTP 返回空内容")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[figure] Qwen3-VL-4B HTTP 调用失败: %s", exc)

    logger.warning("[figure] Qwen3-VL-4B 不可用，返回 None")
    return None


def _ask_cloud_vision_on_figure(
    figure_path: str,
    caption_text: str = "",
    timeout: int = 120,
) -> str | None:
    """云端视觉（GLM/Agnes 等 OpenAI 兼容）优先做图表语义理解。

    走 settings.glm_vision_*（PAPERFORGE_GLM_VISION_ENABLED=1 开启）。
    不占本地显存、不参与 text/vision 同卡互斥（设计见 settings 注释）。
    fail-open：未启用 / 无 key / 调用失败一律返回 None，由 ask_qwen 回落
    本地 Qwen3-VL-4B 或文本 Qwen。
    """
    try:
        from mock_api.settings import get_settings

        st = get_settings()
    except Exception:  # noqa: BLE001 - settings 异常隔离
        return None
    if not st.glm_vision_enabled or not st.glm_vision_api_key:
        return None

    # provider 分支回落：glm→智谱 / agnes→Agnes AI（settings 注释约定，必须按 provider 取默认）
    _PROVIDER_DEFAULTS = {
        "glm": ("https://open.bigmodel.cn/api/paas/v4", "glm-4v-flash"),
        "agnes": ("https://apihub.agnes-ai.com/v1", "agnes-2.5-flash"),
    }
    provider = (st.glm_vision_provider or "glm").lower()
    default_base, default_model = _PROVIDER_DEFAULTS.get(provider, _PROVIDER_DEFAULTS["glm"])
    base_url = (st.glm_vision_base_url or default_base).rstrip("/")
    model = st.glm_vision_model or default_model
    url = f"{base_url}/chat/completions"
    path = Path(figure_path)
    if not path.exists():
        logger.warning("[figure] 云端视觉：图文件不存在，跳过: %s", figure_path)
        return None
    try:
        img_b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[figure] 云端视觉：读图失败: %s", exc)
        return None

    caption_part = f"图注：{caption_text[:400]}" if caption_text else ""
    prompt = (
        "这是一张论文中的实验图。请尽量全面地用中文描述这张图：\n"
        "1. 图的类型（曲线图/柱状图/散点图/热力图/架构图/流程图等）；\n"
        "2. 横轴与纵轴各自代表什么（含单位）；\n"
        "3. 图例与各组对比的含义；\n"
        "4. 数据随自变量变化的主要趋势；\n"
        "5. 这张图支撑的结论。\n"
        "图中若有文字/标签请一并识别。\n"
        f"{caption_part}"
    )
    headers = {"Content-Type": "application/json"}
    if st.glm_vision_api_key:
        headers["Authorization"] = f"Bearer {st.glm_vision_api_key}"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "temperature": 0.0,
        "max_tokens": 1024,
        "stream": False,
    }
    sem = _cloud_vision_sem()
    try:
        with sem:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"].get("content", "")
        if text.strip():
            logger.debug("[figure] 云端视觉理解成功 (model=%s)", model)
            return text.strip()
        logger.warning("[figure] 云端视觉返回空内容")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[figure] 云端视觉调用失败: %s", exc)
    return None


def ask_qwen(
    ocr_text: str,
    figure_path: str | None = None,
    base_url: str = "http://localhost:8080",
    timeout: int = 120,
    *,
    vram_bracket_external: bool = False,
    caption_text: str = "",
) -> str:
    """Generate a semantic summary for a figure.

    Vision-first: if ``figure_path`` is given and the local multimodal model is
    available, the rendered figure image is sent to it for genuine figure
    understanding. Falls back to the text-only Qwen endpoint (old behavior)
    when no image is available or the vision model is unavailable/fails.

    Args:
        ocr_text: OCR text extracted from the figure (fallback input only).
        figure_path: path to the rendered figure PNG. When provided, vision-first.
        base_url: text Qwen endpoint (unused when the vision path succeeds).
        timeout: per-call timeout in seconds. Default 120s for high-res images.
        vram_bracket_external: when True, the caller owns the VRAM bracket. Any
            internal VRAM switch (e.g. text Qwen fallback) is skipped, and vision
            failures return an empty string instead of switching models.
        caption_text: optional figure caption for cross-reference in the vision
            prompt (avoids circular reference with OCR text).

    Returns:
        Semantic summary string; empty string on failure.
    """
    cache_key = _qwen_cache_key(ocr_text, figure_path)
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug("[figure] Qwen cache hit")
        return cached

    if figure_path:
        # 云端视觉优先（更准、不占本地显存）。
        cloud_summary = _ask_cloud_vision_on_figure(
            figure_path, caption_text=caption_text, timeout=timeout
        )
        if cloud_summary:
            _cache_set(cache_key, cloud_summary)
            return cloud_summary
        # 用户明确要求：GLM 视觉开启时不回退本地 Qwen3-VL-4B 视觉，直接走文本兜底。
        try:
            _glm_enabled = get_settings().glm_vision_enabled
        except Exception:  # noqa: BLE001
            _glm_enabled = False
        if not _glm_enabled:
            vision_summary = _ask_vision_on_figure(
                figure_path, caption_text=caption_text, timeout=timeout
            )
            if vision_summary:
                _cache_set(cache_key, vision_summary)
                return vision_summary
        # Vision failed. If caller owns the VRAM bracket, do not switch to text Qwen.
        if vram_bracket_external:
            logger.debug("[figure] vision failed with external VRAM bracket; returning empty")
            return ""
    # Fallback: text-only Qwen on the supplied figure text/caption.
    # Acquire Qwen VRAM lock unless caller owns the bracket.
    if not vram_bracket_external:
        _request_text_vram()
    prompt = f"""下面是从论文实验图中提取到的文字或图注。请根据这些数据，描述：
1. 这张图在展示什么；
2. 横轴和纵轴分别代表什么；
3. 数据随 epoch 变化的趋势；
4. 你能得出的结论。

图表文字：
{ocr_text}
"""
    payload = {
        "model": "qwen",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": 1024,
        "stream": False,
    }
    try:
        resp = requests.post(f"{base_url}/v1/chat/completions", json=payload, timeout=timeout)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        _cache_set(cache_key, content)
        return content
    except Exception as exc:  # noqa: BLE001
        logger.warning("[figure] text Qwen fallback failed: %s", exc)
        return ""
