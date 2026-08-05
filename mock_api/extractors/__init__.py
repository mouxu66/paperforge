"""extractors 子模块 —— v4.2 从 depth_eval_v4.py 拆分出的提取/解析函数。

转发导入以保持 depth_eval_v4.* 的外部兼容性。
"""

from .evidence import (  # noqa: F401
    _ABLATION_PATTERNS,
    _REPO_RELEASE_PATTERNS,
    _detect_ablation_evidence,
    _detect_code_release,
)
from .figures import (  # noqa: F401
    _MAX_AXIS_TICKS,
    _MAX_LEGEND_ITEMS,
    _clamp_list,
    _format_axis_info,
    _format_evidence_pool_text,
)
from .text import (  # noqa: F401
    SECTION_PATTERNS,
    _extract_key_terms,
    _extract_llm_fields,
    _extract_objective_features,
    _extract_section,
    _find_section_boundaries,
    _strip_cot,
)

__all__ = [
    # evidence
    "_ABLATION_PATTERNS",
    "_REPO_RELEASE_PATTERNS",
    "_detect_ablation_evidence",
    "_detect_code_release",
    # figures
    "_MAX_AXIS_TICKS",
    "_MAX_LEGEND_ITEMS",
    "_clamp_list",
    "_format_axis_info",
    "_format_evidence_pool_text",
    # text
    "SECTION_PATTERNS",
    "_extract_key_terms",
    "_extract_llm_fields",
    "_extract_objective_features",
    "_extract_section",
    "_find_section_boundaries",
    "_strip_cot",
]
