"""PaperForge CRUD submodules.

This package splits the former monolithic ``mock_api/crud.py`` into focused
submodules while preserving the public API. Existing imports such as
``from mock_api.crud import get_papers`` continue to work unchanged.
"""

# ruff: noqa: F403, F401
from .analysis import *
from .embeddings import *
from .notes import *
from .papers import *

# 部分以下划线开头的内部辅助函数被测试直接引用，显式重新导出以保持兼容。
from .papers import (
    _detect_document_type,
    _enrich_async,
    _find_duplicate_by_title_authors,
    _reflection_review_async,
    _review_async,
)
from .search import *
from .stats import *
from .writing import *
from .writing import _count_words
