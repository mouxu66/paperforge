#!/usr/bin/env python3
"""Check that mock_api/main.py does not contain inline worker definitions.

Background task workers should live in mock_api/workers/ and be registered
via get_worker(). Inline closures (``def _worker``, ``lambda`` assigned to a
worker variable, or ``lambda`` passed as ``worker_fn``) inside main.py are
forbidden to keep the task architecture consistent.

Usage:
    python scripts/check_no_inline_workers.py [path_to_main.py]

Exit codes:
    0  OK
    1  Inline worker definition found
    2  File not found or other error
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

# Names that strongly indicate an task worker closure.
_WORKER_NAMES = {"_worker", "worker", "worker_fn"}


def _enclosing_function_name(tree: ast.AST, node: ast.AST) -> str | None:
    """Return the name of the innermost function that contains *node*.

    Returns ``None`` if *node* is at module level.
    """
    # Build parent map by walking the tree once.
    parent_map: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent

    current = parent_map.get(node)
    while current is not None:
        if isinstance(current, ast.FunctionDef):
            return current.name
        current = parent_map.get(current)
    return None


def _is_worker_lambda(node: ast.AST) -> bool:
    """Return True if *node* is a lambda that looks like an inline worker."""
    if not isinstance(node, ast.Lambda):
        return False

    # Heuristic 1: lambda is passed as ``worker_fn=...``
    parent = getattr(node, "_parent", None)
    if isinstance(parent, ast.keyword):
        if parent.arg in {"worker_fn", "worker"}:
            return True

    # Heuristic 2: lambda is assigned to a worker-looking name.
    if isinstance(parent, (ast.Assign, ast.AnnAssign)):
        targets = []
        if isinstance(parent, ast.Assign):
            targets = parent.targets
        elif isinstance(parent, ast.AnnAssign):
            targets = [parent.target]
        for target in targets:
            if isinstance(target, ast.Name) and target.id in _WORKER_NAMES:
                return True

    return False


def _find_inline_workers(tree: ast.AST) -> list[tuple[int, str, str]]:
    """Find all inline worker definitions in the AST.

    Returns a list of (lineno, enclosing_function_name, description) tuples.
    """
    offenders: list[tuple[int, str, str]] = []

    # Build parent map so we can inspect context of each node.
    parent_map: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_map[child] = parent
    for child in ast.walk(tree):
        child._parent = parent_map.get(child)  # type: ignore[attr-defined]

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _WORKER_NAMES:
            enclosing = _enclosing_function_name(tree, node)
            if enclosing is not None:
                offenders.append((node.lineno, enclosing, f"def {node.name}"))
            continue

        if _is_worker_lambda(node):
            enclosing = _enclosing_function_name(tree, node)
            if enclosing is not None:
                offenders.append((node.lineno, enclosing, "lambda worker"))

    return offenders


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path("mock_api/main.py")
    if not target.exists():
        print(f"[ERROR] File not found: {target}", file=sys.stderr)
        return 2

    try:
        source = target.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(target))
    except SyntaxError as exc:
        print(f"[ERROR] Syntax error in {target}: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[ERROR] Could not read {target}: {exc}", file=sys.stderr)
        return 2

    offenders = _find_inline_workers(tree)
    if not offenders:
        print(f"[OK] No inline worker definitions found in {target}")
        return 0

    print(
        f"[FAIL] Found {len(offenders)} inline worker definition(s) in {target}:",
        file=sys.stderr,
    )
    for lineno, enclosing, description in offenders:
        print(
            f"  - line {lineno}: '{description}' inside '{enclosing}'",
            file=sys.stderr,
        )
    print(
        "\nBackground workers must live in mock_api/workers/ and be registered "
        "via get_worker().",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
