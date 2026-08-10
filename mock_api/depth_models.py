"""
Typed pipeline models for DEPTH v4.2 DAG communication.

v4.2 incremental typing (2026-07-27):
- ``PaperContext``: frozen shared input for all DAG nodes.
- ``DAGOutputs``: typed container replacing ``dict[str, Any]``.
  Fields are now typed with actual node result types (Q0Result, Q1Result, …)
  using ``TYPE_CHECKING`` to avoid circular imports with ``depth_eval_v4.py``.
- ``QEOutput``: typed wrapper for the QE node's dict return.
- ``NodeOutput[T]``: typed DAG node output wrapper enabling status-aware
  scheduling (success / failed / skipped).

All existing Pydantic result models (Q0Result, Q1Result, QEResult, …)
remain in ``depth_eval_v4.py`` and are referenced via ``TYPE_CHECKING``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from .depth_eval_v4 import (
        Q0Result,
        Q1Result,
        Q2Result,
        Q3Result,
        Q4Result,
        Q5aResult,
        Q5bResult,
        QFResult,
    )

T = TypeVar("T")


# ═══════════════════════════════════════════════════════════════════
# NodeOutput[T] — typed DAG node output wrapper
# ═══════════════════════════════════════════════════════════════════


class NodeOutput(BaseModel, Generic[T]):
    """Typed wrapper for DAG node executor return values.

    Enables the DAG scheduler to inspect ``status`` and decide whether to
    circuit-break, retry, or propagate a failure downstream — without
    losing type information about the payload.

    **Usage inside a node executor**::

        def _run_q0(self, ctx: PaperContext) -> NodeOutput[Q0Result]:
            ...
            if raw is None:
                return NodeOutput(node_id="Q0", status="failed",
                                  error="LLM returned None")
            return NodeOutput(node_id="Q0", status="success",
                              data=Q0Result(...))

    **Unwrapped by the DAG executor factory** before storing into
    ``DAGOutputs`` — downstream nodes still see raw ``Q0Result`` etc.
    """

    node_id: str = ""
    status: str = Field(default="success", pattern=r"^(success|failed|skipped)$")
    data: T | None = None  # None when status != "success"
    error: str | None = None

    model_config = {"frozen": False}  # mutable for convenience during construction


# ──────────────────────────────────────────────────────────────────
# PaperContext — shared read‑only input for all DAG nodes
# ──────────────────────────────────────────────────────────────────


class PaperContext(BaseModel):
    """Immutable input snapshot passed to every DAG node executor.

    Populated once by ``DepthReviewer.review_async_dag()`` after text
    segmentation.  Node executors read ``ctx.paper_abstract_intro`` etc.
    instead of reaching into an untyped ``results`` dict.
    """

    paper_id: str
    title: str
    full_text: str
    abstract: str = ""

    # segmented views (set after segment_paper_text)
    paper_abstract_intro: str = ""
    paper_full_text: str = ""
    paper_abstract_conclusion: str = ""

    # metadata
    paper_meta: dict[str, Any] = Field(default_factory=dict)
    hotspots: list[str] = Field(default_factory=list)

    # cached figures (populated by QE wrapper if DEPTH_FIGURE_EVIDENCE_ENABLED)
    figures: list[dict[str, Any]] = Field(default_factory=list)

    # ADR-014 P9: 全文覆盖层补充文本（全局摘要 + 采样原文块），默认空串零开销。
    # 由 review/review_async_dag 在分段后注入，供 QE/Q234 等节点追加到 {paper} 视图。
    fulltext_supplement: str = ""
    # P9 扩展：按节点名 → 专属补充文本。当 fulltext_supplement 非空时，
    # _paper_view 优先取节点专属补充（若有），回退至 fulltext_supplement。
    # 默认空 dict = 与旧行为完全一致。
    fulltext_node_supplements: dict[str, str] = Field(default_factory=dict)

    model_config = {"frozen": True}  # nodes should not mutate the context


# ──────────────────────────────────────────────────────────────────
# DAGOutputs — typed container for upstream node results
# ──────────────────────────────────────────────────────────────────


class QEOutput(BaseModel):
    """Typed wrapper for the QE executor's return dict.

    The QE executor returns a plain dict ``{"qe_result": ..., "ev_pool": ...,
    "claim_severity_penalty": ...}`` rather than a single Pydantic model.
    This wrapper provides attribute access (``results.QE.ev_pool``) and
    dict access (``results.QE["ev_pool"]``) — both work because Pydantic
    models support ``__getitem__``.

    **Note**: ``results.QE.get("claim_severity_penalty", 0.0)`` must be
    replaced with ``results.QE.claim_severity_penalty`` since Pydantic
    models don't have a ``.get()`` method.
    """

    qe_result: Any = Field(default=None)  # QEResult (circular import → Any)
    ev_pool: dict[str, str] = Field(default_factory=dict)
    claim_severity_penalty: float = 0.0

    model_config = {"extra": "allow"}


# ── Q5c tuple return type alias ──────────────────────────────────
# Q5c executor returns (q5c_res, balanced_cp, base_score, weights)
# Keep as Any for now since the tuple structure is consumed immediately
# by review_async_dag and never accessed via attribute paths in executors.
_Q5cTuple = Any  # placeholder: tuple[Q5cResult, list[CritiquePoint], float, dict[str, float]]


class DAGOutputs(BaseModel):
    """Typed container passed to DAG node executors — drop‑in replacement
    for the untyped ``results: dict[str, Any]`` dict.

    Supports both attribute access (``results.Q1``) and dict‑style access
    (``results["Q1"]``, ``"Q1" in results``, ``results.get("Q1")``).
    ``None`` means the node hasn't completed yet (or was cancelled).

    v4.2 typed fields (2026-07-27):
    - Q0-Q5c fields now carry actual node return types via ``TYPE_CHECKING``.
    - QE uses ``QEOutput`` (typed dict wrapper) instead of bare ``Any``.
    - Q234 uses ``tuple[Q2Result, Q3Result, Q4Result] | None``.
    - Legacy Q2/Q3/Q4 for split-mode (non-merged) paths.

    Example::

        results: DAGOutputs = ...
        q1 = results.Q1                         # Q1Result | None
        ev = results.QE.ev_pool                 # attribute access on QEOutput
        penalty = results.QE.claim_severity_penalty  # float
        q2, q3, q4 = results.Q234               # tuple unpack: typed
    """

    Q0: Q0Result | None = None
    Q1: Q1Result | None = None
    QE: QEOutput | None = None
    QF: QFResult | None = None
    Q2: Q2Result | None = None
    Q3: Q3Result | None = None
    Q4: Q4Result | None = None
    Q234: tuple[Q2Result, Q3Result, Q4Result] | None = None
    Q5a: Q5aResult | None = None
    Q5b: Q5bResult | None = None
    Q5c: _Q5cTuple = None  # tuple[Q5cResult, list[CritiquePoint], float, dict]

    model_config = {"extra": "allow"}

    # ── dict‑compat shims ────────────────────────────────────────

    def __getitem__(self, key: str) -> Any:
        """``results["Q1"]`` — backward compat with dict access.

        Returns ``None`` for missing/not-yet-completed nodes (matching
        original ``dict`` semantics), raises ``KeyError`` only for
        truly unknown keys.
        """
        if key in self.model_fields or self.model_config.get("extra") == "allow":
            return getattr(self, key, None)
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        """``"Q1" in results`` — backward compat."""
        return getattr(self, key, None) is not None

    def get(self, key: str, default: Any = None) -> Any:
        """``results.get("Q1")`` — backward compat."""
        val = getattr(self, key, None)
        return val if val is not None else default


# v4.2: ``model_rebuild(force=True)`` is called from ``depth_eval_v4.py``
# after all result types (Q0Result, Q1Result, …) are defined.
# It CANNOT be called here — importing from depth_eval_v4 at module
# load time always triggers a circular import (depth_eval_v4 imports us).
