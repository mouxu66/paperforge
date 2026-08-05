"""utils 子模块 —— 工具函数集合。

v4.2 从 depth_eval_v4.py 拆分出：
- validators: _clamp_float, _cap_reproducibility
- text_segment: segment_paper_text, _join_sections, _smart_truncate
- tempfiles: managed_tempdir, safe_write, safe_remove
- math_utils: clamp_float (unified)
- paths: _get_uploads_dir
"""

from .math_utils import clamp_float, to_floats  # noqa: F401
from .paths import _get_uploads_dir  # noqa: F401
from .tempfiles import managed_tempdir, safe_remove, safe_write  # noqa: F401
from .text_segment import (
    FALLBACK_CHARS,  # noqa: F401
    MAX_CHARS_FULL,  # noqa: F401
    MAX_CHARS_SHORT,  # noqa: F401
    _join_sections,  # noqa: F401
    _smart_truncate,  # noqa: F401
    segment_paper_text,  # noqa: F401
)
from .validators import _cap_reproducibility, _clamp_float  # noqa: F401

__all__ = [
    # validators
    "_clamp_float",
    "_cap_reproducibility",
    # text_segment
    "segment_paper_text",
    "_join_sections",
    "_smart_truncate",
    "MAX_CHARS_SHORT",
    "MAX_CHARS_FULL",
    "FALLBACK_CHARS",
    # math_utils
    "clamp_float",
    "to_floats",
    # tempfiles
    "managed_tempdir",
    "safe_write",
    "safe_remove",
    # paths
    "_get_uploads_dir",
]
