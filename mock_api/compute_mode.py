"""
算力模式与运行时配置 —— 资源检测 + 预设匹配 + 运行时覆盖 + DEPTH 参数访问器。

v4.2 合并范围（2026-07-27）：
- 硬件资源检测（GPU 显存 / 系统内存 / nvidia-smi / torch / psutil）
- 预设匹配（high_performance / balanced / low_memory + 内置兜底）
- llama.cpp 参数生成（get_llama_params / to_kwargs）
- 算力模式单例（get_compute_mode / set_compute_mode / COMPUTE_MODES）
- 动态资源感知预设缓存（get_dynamic_preset / reload_dynamic_preset）
- DEPTH 运行时覆盖访问器（14 个 get_depth_* 函数）
- Q5c delta 区间解析（get_delta_bounds）
- 从 config.py 迁出的全局状态（原 config.py 动态函数全部迁入）

设计原则：
- 自动检测 GPU 显存（nvidia-smi / torch.cuda）和系统内存（psutil）
- 基于检测结果匹配预设（high_performance / balanced / low_memory）
- 生成 llama.cpp 兼容的 CLI 参数，避免 OOM
- 预设配置从 compute_presets.json 加载，支持运行时覆盖
- 所有检测函数在资源不可用时静默降级（返回默认值）
- 运行时覆盖优先级：PAPERFORGE_* 环境变量 > 运行时 API 设置 > settings.py 默认值

典型用法：
    from .compute_mode import detect_resources, resolve_preset, get_compute_mode_config
    resources = detect_resources()
    preset = resolve_preset(resources)
    params = preset.get_llama_params()
    mode_cfg = get_compute_mode_config()  # 合并动态预设 + 静态 COMPUTE_MODES
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime_settings import get_runtime_override
from .settings import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public API (used by compute_config.py wildcard re-export shim)
# ---------------------------------------------------------------------------
__all__ = [
    "detect_resources",
    "SystemResources",
    "ComputePreset",
    "resolve_preset",
    "get_llama_params",
    "to_kwargs",
    "get_current_preset",
    "reload_presets",
    "COMPUTE_MODES",
    "get_compute_mode",
    "set_compute_mode",
    "get_compute_mode_config",
    "get_dynamic_preset",
    "reload_dynamic_preset",
    "get_depth_severity_fallback_threshold",
    "get_depth_severity_fatal_weight",
    "get_depth_severity_minor_weight",
    "get_depth_q5c_claim_severity_factor",
    "get_depth_claim_validation_penalty_per_claim",
    "get_depth_claim_validation_penalty_max",
    "get_depth_claim_validation_bonus_enabled",
    "get_depth_claim_validation_bonus_max",
    "get_depth_claim_validation_bonus_per_claim",
    "get_depth_claim_validation_bonus_min_valid",
    "get_depth_delta_default_min",
    "get_depth_delta_default_max",
    "get_depth_delta_bounds_overrides",
    "get_delta_bounds",
]


# ---------------------------------------------------------------------------
# 预设配置路径（优先集中式 settings，否则同目录下 JSON）
# ---------------------------------------------------------------------------
def _get_presets_path() -> str:
    return get_settings().compute_presets_path or str(
        Path(__file__).resolve().parent / "compute_presets.json"
    )


_presets_cache: list[dict] | None = None
_presets_lock = threading.Lock()


# =============================================================================
# 数据结构
# =============================================================================


@dataclass
class SystemResources:
    """系统资源快照。"""

    gpu_vram_mb: int = 0  # 可用 GPU 显存（MB），0 = 无 GPU 或检测失败
    gpu_vram_total_mb: int = 0  # GPU 总显存
    system_ram_mb: int = 0  # 可用系统内存（MB）
    system_ram_total_mb: int = 0  # 系统总内存
    gpu_count: int = 0  # GPU 数量
    detected_at: str = ""  # ISO 时间戳


@dataclass
class ComputePreset:
    """单个算力预设。"""

    id: str = ""  # high_performance / balanced / low_memory
    name: str = ""  # 显示名称
    description: str = ""
    # 匹配条件
    min_gpu_vram_mb: int = 0
    min_system_ram_mb: int = 0
    # LLM 推理参数（llama.cpp CLI 风格）
    ctx_size: int = 4096  # --ctx-size
    batch_size: int = 512  # --batch-size
    threads: int = 4  # --threads
    gpu_layers: int = 0  # --n-gpu-layers（0 = 全部在 CPU，-1 = 全部在 GPU）
    # cache_type_k / cache_type_v 在 JSON 中使用 snake_case 内部字段名，
    # get_llama_params() / to_kwargs() 负责转换为 llama.cpp CLI 标志（--cache-type-k / cache_type_k）。
    cache_type_k: str = ""  # → --cache-type-k
    cache_type_v: str = ""  # --cache-type-v
    flash_attn: bool = False  # --flash-attn
    paged_attn: bool = False  # --paged-attn（llama.cpp 分页注意力，避免长上下文 OOM）
    cont_batching: int = 0  # --cont-batching（连续批处理合并数，0=禁用）
    backend: str = ""  # --backend（cuda / vulkan / metal / cpu）
    # DEPTH 流水线参数
    max_chars_full: int = 32000  # 全文最大字符数
    max_chars_short: int = 4000  # 短视图最大字符数
    temperature: float = 0.0
    max_tokens: int = 512
    parallel: bool = True  # 是否启用 DAG 并行


# =============================================================================
# 资源检测
# =============================================================================


def detect_gpu_memory() -> tuple[int, int, int]:
    """检测 GPU 显存。

    Returns:
        (available_mb, total_mb, gpu_count)。
        无 GPU 时返回 (0, 0, 0)。

    【重要】下面的 try/except 只能捕获 Python 层异常（FileNotFoundError、
    TimeoutExpired、ValueError 等），无法拦截 C 级访问冲突（access violation）。
    如果解释器是 WindowsApps Store 版 Python 3.13，CreateProcess 到 nvidia-smi
    可能在原生层崩溃并直接杀死进程。detect_resources() 中的 Windows Store Python
    检测（层级 2）才是真正的安全网，确保不会走到这里。
    """
    import shutil

    # 集中式配置一键关闭（默认关闭开发环境/CI）
    if get_settings().disable_gpu_detect:
        logger.debug("disable_gpu_detect=True, 跳过 GPU 检测")
    else:
        nvidia_smi_path = shutil.which("nvidia-smi")
        if not nvidia_smi_path:
            logger.debug("nvidia-smi 不在 PATH, 跳过 subprocess 检测")
        else:
            # 方法 1：nvidia-smi（已验证可执行文件路径存在）
            # 【AI-04 硬化】添加 creationflags=CREATE_NO_WINDOW（防 Windows 上
            # 创建控制台窗口触发 C 级访问冲突）、close_fds=True（防句柄泄漏）、
            # startupinfo（隐藏窗口）。这些 flags 在非 Windows 平台被忽略。
            try:
                _subprocess_kwargs: dict[str, Any] = {
                    "capture_output": True,
                    "text": True,
                    "timeout": 5,
                    "close_fds": True,
                }
                if hasattr(subprocess, "CREATE_NO_WINDOW"):
                    _subprocess_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "STARTUPINFO"):
                    _si = subprocess.STARTUPINFO()
                    _si.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
                    _si.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
                    _subprocess_kwargs["startupinfo"] = _si
                result = subprocess.run(
                    [
                        nvidia_smi_path,
                        "--query-gpu=memory.free,memory.total,count",
                        "--format=csv,noheader,nounits",
                    ],
                    **_subprocess_kwargs,
                )
                if result.returncode == 0 and result.stdout.strip():
                    parts = result.stdout.strip().split(",")
                    if len(parts) >= 2:
                        free_mb = int(parts[0].strip())
                        total_mb = int(parts[1].strip())
                        count = int(parts[2].strip()) if len(parts) >= 3 else 1
                        logger.info(
                            "GPU 检测 (nvidia-smi): free=%dMB total=%dMB count=%d",
                            free_mb,
                            total_mb,
                            count,
                        )
                        return free_mb, total_mb, count
            except (FileNotFoundError, OSError, subprocess.TimeoutExpired, ValueError) as e:
                logger.debug("nvidia-smi 调用失败: %s", e)
            except Exception as e:  # noqa: BLE001 - compute mode 切换 - 失败时回退基础模式
                # 最后一层防御：某些环境会抛出未预到的异常类型（如 subprocess.SubprocessError 子类）。
                logger.debug("nvidia-smi 调用出现意外异常: %s", e)

    # 方法 2：torch.cuda（使用 mem_get_info 获取系统级空闲显存）
    try:
        import torch  # noqa: F811

        if torch.cuda.is_available():
            free_mb = 0
            total_mb = 0
            for i in range(torch.cuda.device_count()):
                # mem_get_info 返回 (free_bytes, total_bytes) — 系统级准确值
                if hasattr(torch.cuda, "mem_get_info"):
                    free_bytes, total_bytes = torch.cuda.mem_get_info(i)
                    free_mb += free_bytes // (1024 * 1024)
                    total_mb += total_bytes // (1024 * 1024)
                else:
                    # 旧版 PyTorch 回退
                    props = torch.cuda.get_device_properties(i)
                    total_mb += props.total_memory // (1024 * 1024)
            count = torch.cuda.device_count()
            logger.info(
                "GPU 检测 (torch): free=%dMB total=%dMB count=%d",
                free_mb,
                total_mb,
                count,
            )
            return free_mb, total_mb, count
    except ImportError:
        logger.debug("torch 未安装，跳过 GPU 检测")

    return 0, 0, 0


def detect_system_memory() -> tuple[int, int]:
    """检测系统内存。

    Returns:
        (available_mb, total_mb)。

    【重要】下面的 try/except 只能捕获 Python 层异常（ImportError、OSError 等），
    无法拦截 C 级访问冲突。psutil 和 ctypes 均为原生调用，在 WindowsApps Store
    版 Python 环境下可能触发不可恢复的进程崩溃。detect_resources() 中的
    Windows Store Python 检测（层级 2）才是真正的安全网。
    """
    try:
        import psutil

        mem = psutil.virtual_memory()
        available_mb = mem.available // (1024 * 1024)
        total_mb = mem.total // (1024 * 1024)
        logger.info(
            "内存检测 (psutil): available=%dMB total=%dMB (%.0f%%)",
            available_mb,
            total_mb,
            mem.percent,
        )
        return available_mb, total_mb
    except ImportError:
        logger.debug("psutil 未安装，使用 os 估算内存")
    except Exception as e:  # noqa: BLE001 - compute mode 切换 - 失败时回退基础模式
        logger.debug("psutil 调用失败（原生 endpoint 可能受阻）: %s", e)
        return 0, 0

    try:
        # Windows ctypes fallback
        import ctypes

        kernel32 = ctypes.windll.kernel32
        if hasattr(kernel32, "GlobalMemoryStatus"):
            mem_status = type(
                "MEMORYSTATUS",
                (ctypes.Structure,),
                {
                    "_fields_": [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("dwTotalPhys", ctypes.c_size_t),
                        ("dwAvailPhys", ctypes.c_size_t),
                    ]
                },
            )()
            mem_status.dwLength = ctypes.sizeof(mem_status)
            kernel32.GlobalMemoryStatus(ctypes.byref(mem_status))
            total_mb = mem_status.dwTotalPhys // (1024 * 1024)
            available_mb = mem_status.dwAvailPhys // (1024 * 1024)
            return available_mb, total_mb
    except Exception as e:  # noqa: BLE001 - compute mode 切换 - 失败时回退基础模式
        logger.debug("ctypes 内存检测失败: %s", e)
    return 0, 0


def detect_resources() -> SystemResources:
    """全面检测系统资源。

    【防御层级】
    1. PAPERFORGE_DISABLE_RESOURCE_DETECT=1 → 立即返回零值，不触碰任何原生调用。
    2. Windows Store Python（sys.base_prefix 含 WindowsApps）→ 该解释器的
       CreateProcess 到外部二进制会触发 C 级 access violation，Python try/except
       无法拦截。直接返回零值，彻底跳过 subprocess/psutil/ctypes 等原生调用。
    3. detect_gpu_memory / detect_system_memory 内部有各自的 try/except 兜底，
       但对 C 级崩溃无效（Python 异常机制无法捕获原生 access violation）。
       因此层级 2 是真正的安全网。
    """
    import sys as _sys
    from datetime import datetime

    # ── 层级 1：顶级 opt-out（测试 / CI 环境一键禁用）──
    if get_settings().disable_resource_detect:
        logger.debug("disable_resource_detect=True, 返回零值预设")
        return SystemResources(
            gpu_vram_mb=0,
            gpu_vram_total_mb=0,
            system_ram_mb=0,
            system_ram_total_mb=0,
            gpu_count=0,
            detected_at=datetime.now().isoformat(),
        )

    # ── 层级 2：Windows Store Python 检测（AI-05）──
    # WindowsApps Store 版 Python 的 CreateProcess 到外部二进制会触发 C 级
    # access violation，Python 的 try/except 完全无法拦截。因此必须在任何
    # 原生调用之前检测并跳过，这是真正的安全网。
    if _sys.platform == "win32" and "WindowsApps" in (_sys.base_prefix or ""):
        logger.info(
            "检测到 Windows Store Python (base_prefix=%s)，"
            "该解释器调用外部二进制可能触发 C 级崩溃，跳过所有原生资源检测",
            _sys.base_prefix,
        )
        return SystemResources(
            gpu_vram_mb=0,
            gpu_vram_total_mb=0,
            system_ram_mb=0,
            system_ram_total_mb=0,
            gpu_count=0,
            detected_at=datetime.now().isoformat(),
        )

    try:
        gpu_free, gpu_total, gpu_count = detect_gpu_memory()
    except Exception as e:  # noqa: BLE001 - compute mode 切换 - 失败时回退基础模式
        logger.debug("detect_gpu_memory 意外异常: %s", e)
        gpu_free, gpu_total, gpu_count = 0, 0, 0

    try:
        ram_free, ram_total = detect_system_memory()
    except Exception as e:  # noqa: BLE001 - compute mode 切换 - 失败时回退基础模式
        logger.debug("detect_system_memory 意外异常: %s", e)
        ram_free, ram_total = 0, 0

    return SystemResources(
        gpu_vram_mb=gpu_free,
        gpu_vram_total_mb=gpu_total,
        system_ram_mb=ram_free,
        system_ram_total_mb=ram_total,
        gpu_count=gpu_count,
        detected_at=datetime.now().isoformat(),
    )


# =============================================================================
# 预设加载与匹配
# =============================================================================


def load_presets() -> list[dict]:
    """从 compute_presets.json 加载预设配置。"""
    global _presets_cache
    with _presets_lock:
        if _presets_cache is not None:
            return _presets_cache

    presets_path = _get_presets_path()
    path = Path(presets_path)
    if not path.exists():
        logger.warning("预设配置文件不存在: %s，使用内置默认值", presets_path)
        _presets_cache = _builtin_presets()
        return _presets_cache

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        presets = data.get("presets", [])
        if not presets:
            _presets_cache = _builtin_presets()
        else:
            _presets_cache = presets
        logger.info("从 %s 加载了 %d 个预设", presets_path, len(_presets_cache))
        return _presets_cache
    except Exception as e:  # noqa: BLE001 - compute mode 切换 - 失败时回退基础模式
        logger.warning("预设配置加载失败: %s，使用内置默认值", e)
        _presets_cache = _builtin_presets()
        return _presets_cache


def _builtin_presets() -> list[dict]:
    """内置默认预设（兜底）。"""
    return [
        {
            "id": "high_performance",
            "name": "高性能模式",
            "description": "资源充足（≥12GB 显存）时使用，全量 context + GPU 全层加速",
            "min_gpu_vram_mb": 10000,
            "min_system_ram_mb": 24000,
            "ctx_size": 8192,
            "batch_size": 512,
            "threads": 8,
            "gpu_layers": -1,
            "cache_type_k": "",
            "cache_type_v": "",
            "flash_attn": True,
            "paged_attn": True,
            "cont_batching": 0,
            "backend": "cuda",
            "max_chars_full": 32000,
            "max_chars_short": 4000,
            "temperature": 0.0,
            "max_tokens": 512,
            "parallel": True,
        },
        {
            "id": "rtx5060_9b",
            "name": "RTX 5060 优化（9B 模型）",
            "description": "RTX 5060 8GB + 9B Q4_K 优化：35 GPU 层、分页注意力、连续批处理、CUDA 后端",
            "min_gpu_vram_mb": 6000,
            "min_system_ram_mb": 12000,
            "ctx_size": 2048,
            "batch_size": 4,
            "threads": 4,
            "gpu_layers": 35,
            "cache_type_k": "",
            "cache_type_v": "",
            "flash_attn": False,
            "paged_attn": True,
            "cont_batching": 4,
            "backend": "cuda",
            "max_chars_full": 16000,
            "max_chars_short": 4000,
            "temperature": 0.0,
            "max_tokens": 512,
            "parallel": True,
        },
        {
            "id": "balanced",
            "name": "均衡模式",
            "description": "资源适中（4-8GB 显存），截断 context + 部分 GPU 层 + KV 量化",
            "min_gpu_vram_mb": 3000,
            "min_system_ram_mb": 8000,
            "ctx_size": 4096,
            "batch_size": 512,
            "threads": 4,
            "gpu_layers": 16,
            "cache_type_k": "q4_0",
            "cache_type_v": "q4_0",
            "flash_attn": False,
            "paged_attn": False,
            "cont_batching": 0,
            "backend": "",
            "max_chars_full": 16000,
            "max_chars_short": 4000,
            "temperature": 0.0,
            "max_tokens": 256,
            "parallel": True,
        },
        {
            "id": "low_memory",
            "name": "低内存模式",
            "description": "资源紧张（<4GB 显存）时使用，大幅截断 + CPU only + 串行执行",
            "min_gpu_vram_mb": 0,
            "min_system_ram_mb": 0,
            "ctx_size": 2048,
            "batch_size": 256,
            "threads": 2,
            "gpu_layers": 0,
            "cache_type_k": "q4_0",
            "cache_type_v": "q4_0",
            "flash_attn": False,
            "paged_attn": False,
            "cont_batching": 0,
            "backend": "",
            "max_chars_full": 8000,
            "max_chars_short": 2000,
            "temperature": 0.0,
            "max_tokens": 128,
            "parallel": False,
        },
    ]


def resolve_preset(
    resources: SystemResources | None = None,
    preset_id: str | None = None,
) -> ComputePreset:
    """基于系统资源自动匹配最佳预设。

    Args:
        resources: 系统资源快照。None 时自动检测。
        preset_id: 指定预设 ID（'high_performance' / 'balanced' / 'low_memory'），
                   覆盖自动匹配结果。None 时自动匹配。

    Returns:
        匹配到的 ComputePreset。
    """
    if resources is None:
        resources = detect_resources()

    presets = load_presets()

    # 若显式指定，直接查找
    if preset_id:
        for p in presets:
            if p.get("id") == preset_id:
                return _dict_to_preset(p)
        logger.warning("指定预设 '%s' 不存在，回退到自动匹配", preset_id)

    # 自动匹配：选择满足资源条件的最佳预设
    # 先按资源需求降序排列（GPU + RAM 总和），确保高需求预设优先匹配
    sorted_presets = sorted(
        presets,
        key=lambda p: p.get("min_gpu_vram_mb", 0) + p.get("min_system_ram_mb", 0),
        reverse=True,
    )
    matched = None
    for p in sorted_presets:
        min_gpu = p.get("min_gpu_vram_mb", 0)
        min_ram = p.get("min_system_ram_mb", 0)
        if resources.gpu_vram_mb >= min_gpu and resources.system_ram_mb >= min_ram:
            matched = p
            break

    if matched is None:
        # 兜底：使用最后一个（最低需求）预设
        matched = presets[-1] if presets else _builtin_presets()[-1]

    result = _dict_to_preset(matched)
    logger.info(
        "预设匹配: %s (GPU=%dMB RAM=%dMB → %s)",
        result.name,
        resources.gpu_vram_mb,
        resources.system_ram_mb,
        result.id,
    )
    return result


def _dict_to_preset(data: dict) -> ComputePreset:
    """字典 → ComputePreset。"""
    return ComputePreset(
        id=data.get("id", ""),
        name=data.get("name", ""),
        description=data.get("description", ""),
        min_gpu_vram_mb=data.get("min_gpu_vram_mb", 0),
        min_system_ram_mb=data.get("min_system_ram_mb", 0),
        ctx_size=data.get("ctx_size", 4096),
        batch_size=data.get("batch_size", 512),
        threads=data.get("threads", 4),
        gpu_layers=data.get("gpu_layers", 0),
        cache_type_k=data.get("cache_type_k", ""),
        cache_type_v=data.get("cache_type_v", ""),
        flash_attn=data.get("flash_attn", False),
        paged_attn=data.get("paged_attn", False),
        cont_batching=data.get("cont_batching", 0),
        backend=data.get("backend", ""),
        max_chars_full=data.get("max_chars_full", 32000),
        max_chars_short=data.get("max_chars_short", 4000),
        temperature=data.get("temperature", 0.0),
        max_tokens=data.get("max_tokens", 512),
        parallel=data.get("parallel", True),
    )


# =============================================================================
# llama.cpp 参数生成
# =============================================================================


def get_llama_params(
    resources: SystemResources | None = None,
    preset_id: str | None = None,
    extra_args: list[str] | None = None,
) -> list[str]:
    """生成 llama.cpp CLI 参数字符串列表。

    自动检测资源 + 匹配预设，生成可直接传给 subprocess 的参数列表。
    对于需要通过 llama-cpp-python 等库使用的场景，可用 to_kwargs()。

    Args:
        resources: 系统资源快照（None = 自动检测）。
        preset_id: 预设 ID（None = 自动匹配）。
        extra_args: 用户额外参数（追加在末尾，覆盖自动参数）。

    Returns:
        llama.cpp CLI 参数列表，如：
        ["--ctx-size", "4096", "--batch-size", "512", "--threads", "4",
         "--n-gpu-layers", "16", "--cache-type-k", "q4_0", "--cache-type-v", "q4_0"]

    示例用法：
        params = get_llama_params()
        subprocess.run(["llama-cli", "-m", "model.gguf", *params, "-p", "hello"])
    """
    preset = resolve_preset(resources, preset_id)
    args: list[str] = []

    args.extend(["--ctx-size", str(preset.ctx_size)])
    args.extend(["--batch-size", str(preset.batch_size)])
    args.extend(["--threads", str(preset.threads)])

    if preset.gpu_layers != 0:
        args.extend(["--n-gpu-layers", str(preset.gpu_layers)])

    if preset.cache_type_k:
        args.extend(["--cache-type-k", preset.cache_type_k])
    if preset.cache_type_v:
        args.extend(["--cache-type-v", preset.cache_type_v])

    if preset.flash_attn:
        args.append("--flash-attn")

    if preset.paged_attn:
        args.append("--paged-attn")

    if preset.cont_batching > 0:
        args.extend(["--cont-batching", str(preset.cont_batching)])

    if preset.backend:
        args.extend(["--backend", preset.backend])

    if extra_args:
        args.extend(extra_args)

    logger.info("llama.cpp 参数: %s", " ".join(args))
    return args


def to_kwargs(
    resources: SystemResources | None = None,
    preset_id: str | None = None,
) -> dict[str, Any]:
    """生成 llama-cpp-python 库的 kwargs 字典。

    Returns:
        可直接解包给 Llama(**kwargs) 的字典，如：
        {"n_ctx": 4096, "n_batch": 512, "n_threads": 4, "n_gpu_layers": 16, ...}
    """
    preset = resolve_preset(resources, preset_id)
    kwargs: dict[str, Any] = {
        "n_ctx": preset.ctx_size,
        "n_batch": preset.batch_size,
        "n_threads": preset.threads,
        "n_gpu_layers": preset.gpu_layers,
    }
    if preset.flash_attn:
        kwargs["flash_attn"] = True
    if preset.cache_type_k:
        kwargs["cache_type_k"] = preset.cache_type_k
    if preset.cache_type_v:
        kwargs["cache_type_v"] = preset.cache_type_v
    if preset.paged_attn:
        kwargs["paged_attn"] = True
    if preset.cont_batching > 0:
        kwargs["cont_batching"] = preset.cont_batching
    if preset.backend:
        kwargs["backend"] = preset.backend
    return kwargs


# =============================================================================
# 便捷函数
# =============================================================================


def get_current_preset(resources: SystemResources | None = None) -> ComputePreset:
    """获取当前最优预设（快捷方式）。"""
    return resolve_preset(resources)


def reload_presets() -> None:
    """强制重新加载预设配置文件（清除缓存）。"""
    global _presets_cache
    with _presets_lock:
        _presets_cache = None
    load_presets()
    logger.info("预设配置已重新加载")


# ═══════════════════════════════════════════════════════════════════════
# v4.2 migration: runtime overrides & compute-mode singleton
# (merged from compute_config.py, 2026-07-27)
# ═══════════════════════════════════════════════════════════════════════

_settings = get_settings()  # snapshot for runtime-override accessor defaults

# ---------------------------------------------------------------------------
# Runtime override helper (import at top: from .runtime_settings) – shared by all get_depth_* accessors
# ---------------------------------------------------------------------------


def _depth_runtime_value(name: str, fallback: Any) -> Any:
    """Return the runtime override if set, otherwise the settings default."""
    value = get_runtime_override(name)
    return value if value is not None else fallback


# ---------------------------------------------------------------------------
# DEPTH severity / claim-validation runtime accessors
# ---------------------------------------------------------------------------
def get_depth_severity_fallback_threshold() -> float:
    return float(
        _depth_runtime_value(
            "depth_severity_fallback_threshold", _settings.depth_severity_fallback_threshold
        )
    )


def get_depth_severity_fatal_weight() -> float:
    return float(
        _depth_runtime_value("depth_severity_fatal_weight", _settings.depth_severity_fatal_weight)
    )


def get_depth_severity_minor_weight() -> float:
    return float(
        _depth_runtime_value("depth_severity_minor_weight", _settings.depth_severity_minor_weight)
    )


def get_depth_q5c_claim_severity_factor() -> float:
    return float(
        _depth_runtime_value(
            "depth_q5c_claim_severity_factor", _settings.depth_q5c_claim_severity_factor
        )
    )


def get_depth_claim_validation_penalty_per_claim() -> float:
    return float(
        _depth_runtime_value(
            "depth_claim_validation_penalty_per_claim",
            _settings.depth_claim_validation_penalty_per_claim,
        )
    )


def get_depth_claim_validation_penalty_max() -> float:
    return float(
        _depth_runtime_value(
            "depth_claim_validation_penalty_max", _settings.depth_claim_validation_penalty_max
        )
    )


def get_depth_claim_validation_bonus_enabled() -> bool:
    return bool(
        _depth_runtime_value(
            "depth_claim_validation_bonus_enabled",
            _settings.depth_claim_validation_bonus_enabled,
        )
    )


def get_depth_claim_validation_bonus_max() -> float:
    return float(
        _depth_runtime_value(
            "depth_claim_validation_bonus_max", _settings.depth_claim_validation_bonus_max
        )
    )


def get_depth_claim_validation_bonus_per_claim() -> float:
    return float(
        _depth_runtime_value(
            "depth_claim_validation_bonus_per_claim",
            _settings.depth_claim_validation_bonus_per_claim,
        )
    )


def get_depth_claim_validation_bonus_min_valid() -> int:
    return int(
        _depth_runtime_value(
            "depth_claim_validation_bonus_min_valid",
            _settings.depth_claim_validation_bonus_min_valid,
        )
    )


def get_depth_delta_default_min() -> float:
    return float(_depth_runtime_value("depth_delta_default_min", _settings.depth_delta_default_min))


def get_depth_delta_default_max() -> float:
    return float(_depth_runtime_value("depth_delta_default_max", _settings.depth_delta_default_max))


def get_depth_delta_bounds_overrides() -> dict:
    return dict(
        _depth_runtime_value("depth_delta_bounds_overrides", _settings.depth_delta_bounds_overrides)
        or {}
    )


def get_delta_bounds(paper_type: str, compute_mode: str) -> tuple[float, float]:
    """Resolve the Q5c default delta interval by paper_type / compute_mode.

    Priority (highest first):
    1. DEPTH_DELTA_BOUNDS_OVERRIDES[paper_type]  {"min": float, "max": float}
    2. DEPTH_DELTA_BOUNDS_OVERRIDES[compute_mode]
    3. Global defaults DELTA_DEFAULT_MIN / DELTA_DEFAULT_MAX

    Returned values are clamped to the hard guardrails [DELTA_HARD_MIN, DELTA_HARD_MAX].
    """
    # Hard guardrails – also available as config.py module-level constants
    DELTA_HARD_MIN: float = -0.25
    DELTA_HARD_MAX: float = 0.25
    DEFAULT_MIN = _settings.depth_delta_default_min
    DEFAULT_MAX = _settings.depth_delta_default_max

    overrides = get_depth_delta_bounds_overrides()
    for key in (paper_type, compute_mode):
        cfg = overrides.get(key) if isinstance(overrides, dict) else None
        if not cfg:
            continue
        try:
            min_val = float(cfg.get("min", DEFAULT_MIN))
            max_val = float(cfg.get("max", DEFAULT_MAX))
            return (
                max(DELTA_HARD_MIN, min_val),
                min(DELTA_HARD_MAX, max_val),
            )
        except (TypeError, ValueError):
            pass
    default_min = get_depth_delta_default_min()
    default_max = get_depth_delta_default_max()
    return (
        max(DELTA_HARD_MIN, default_min),
        min(DELTA_HARD_MAX, default_max),
    )


# ---------------------------------------------------------------------------
# Compute mode singleton – DEPTH v4.2 DAG concurrency + parameter presets
# ---------------------------------------------------------------------------
COMPUTE_MODES: dict[str, dict] = {
    "speed": {
        "name": "极速模式",
        "description": "串行执行，小参数，快速返回结果",
        "temperature": 0.0,
        "max_tokens": 2048,
        "parallel": False,
        "llm_config_id": None,
    },
    "deep": {
        "name": "深度思考",
        "description": "DAG 并行执行，大参数，极致质量",
        "temperature": 0.0,
        "max_tokens": 4096,
        "parallel": True,
        "llm_config_id": None,
    },
}
_current_compute_mode: str = "deep"  # default
_compute_mode_lock = threading.Lock()

# Dynamic resource-based preset (lazy-init on first access)
_dynamic_preset: dict | None = None
_dynamic_preset_lock = threading.Lock()
_cached_resources: dict | None = None  # separate from preset dict to avoid pollution


def get_compute_mode() -> str:
    """Return the current compute-mode identifier ("speed" / "deep")."""
    return _current_compute_mode


def set_compute_mode(mode: str) -> bool:
    """Switch compute mode.  Returns True on success."""
    global _current_compute_mode
    mode = mode.strip().lower()
    if mode not in COMPUTE_MODES:
        return False
    with _compute_mode_lock:
        _current_compute_mode = mode
    return True


def get_compute_mode_config() -> dict:
    """Return the full config dict for the current compute mode.

    Dynamic preset fields (temperature, max_tokens, ctx_size, …) are
    merged on top of the static COMPUTE_MODES entry.
    """
    base = dict(COMPUTE_MODES.get(_current_compute_mode, COMPUTE_MODES["deep"]))
    dynamic = get_dynamic_preset()
    if dynamic:
        for key in (
            "temperature",
            "max_tokens",
            "parallel",
            "ctx_size",
            "max_chars_full",
            "max_chars_short",
            "paged_attn",
            "cont_batching",
            "backend",
        ):
            if key in dynamic:
                base[key] = dynamic[key]
    else:
        # Fallback: resolve static preset JSON (no resource detection)
        try:
            preset = resolve_preset(SystemResources())
            base["ctx_size"] = preset.ctx_size
            base["max_chars_full"] = preset.max_chars_full
            base["max_chars_short"] = preset.max_chars_short
            base["paged_attn"] = preset.paged_attn
            base["cont_batching"] = preset.cont_batching
            base["backend"] = preset.backend
        except Exception:
            pass  # caller .get() defaults cover missing keys
    return base


def get_dynamic_preset(force_refresh: bool = False) -> dict | None:
    """System-resource-aware preset (cached with change detection).

    On first call, detects GPU/RAM and matches the best preset from
    ``compute_presets.json``.  Subsequent calls return the cached result
    unless resources changed significantly (>10 % GPU, >20 % RAM).

    On Windows, resource detection is disabled by default to avoid C-level
    crashes (nvidia-smi / psutil).  Set ``PAPERFORGE_ENABLE_RESOURCE_DETECT=1``
    to enable.
    """
    global _dynamic_preset, _cached_resources
    settings = get_settings()
    if sys.platform == "win32" and not settings.enable_resource_detect:
        logger.debug(
            "Windows platform dynamic resource detection disabled by default; "
            "set PAPERFORGE_ENABLE_RESOURCE_DETECT=1 to enable"
        )
        return None
    with _dynamic_preset_lock:
        try:
            if not force_refresh and _dynamic_preset and _cached_resources:
                resources = detect_resources()
                gpu_delta = abs(resources.gpu_vram_mb - _cached_resources.get("gpu_mb", 0))
                ram_delta = abs(resources.system_ram_mb - _cached_resources.get("ram_mb", 0))
                gpu_threshold = max(_cached_resources.get("gpu_mb", 1) * 0.1, 256)
                ram_threshold = max(_cached_resources.get("ram_mb", 1) * 0.2, 512)
                if gpu_delta < gpu_threshold and ram_delta < ram_threshold:
                    return _dynamic_preset
            resources = detect_resources()
            preset = resolve_preset(resources)
            _dynamic_preset = {
                "id": preset.id,
                "name": preset.name,
                "description": preset.description,
                "temperature": preset.temperature,
                "max_tokens": preset.max_tokens,
                "parallel": preset.parallel,
                "ctx_size": preset.ctx_size,
                "max_chars_full": preset.max_chars_full,
                "max_chars_short": preset.max_chars_short,
                "paged_attn": preset.paged_attn,
                "cont_batching": preset.cont_batching,
                "backend": preset.backend,
            }
            _cached_resources = {
                "gpu_mb": resources.gpu_vram_mb,
                "ram_mb": resources.system_ram_mb,
                "detected_at": resources.detected_at,
            }
            logger.info(
                "Dynamic compute preset: %s (GPU=%dMB RAM=%dMB ctx=%d)",
                _dynamic_preset["name"],
                resources.gpu_vram_mb,
                resources.system_ram_mb,
                _dynamic_preset.get("ctx_size", 0),
            )
            return _dynamic_preset
        except (ImportError, FileNotFoundError, OSError) as e:
            logger.debug(
                "Dynamic preset detection failed (missing dep/IO): %s; fallback to COMPUTE_MODES", e
            )
            _dynamic_preset = {}
            return None
        except Exception:
            logger.warning(
                "Dynamic preset detection error, fallback to COMPUTE_MODES", exc_info=True
            )
            _dynamic_preset = {}
            return None


def reload_dynamic_preset() -> None:
    """Force re-detect system resources and re-match the preset."""
    global _dynamic_preset, _cached_resources
    with _dynamic_preset_lock:
        _dynamic_preset = None
        _cached_resources = None
    get_dynamic_preset()
