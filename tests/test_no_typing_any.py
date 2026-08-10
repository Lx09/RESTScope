"""Enforce the repository rule that Python code never uses ``typing.Any``."""

from __future__ import annotations

import ast
from pathlib import Path


def test_python_sources_do_not_import_or_reference_typing_any() -> None:
    """Reject direct, aliased, and module-qualified forms across code and tests."""
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    for source_root in (root / "restscope", root / "tests"):
        for path in source_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            typing_aliases = {
                alias.asname or alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
                for alias in node.names
                if alias.name in {"typing", "typing_extensions"}
            }
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module in {"typing", "typing_extensions"}
                ):
                    if any(alias.name in {"Any", "*"} for alias in node.names):
                        violations.append(f"{path.relative_to(root)}:{node.lineno}: import")
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "Any"
                    and isinstance(node.value, ast.Name)
                    and node.value.id in typing_aliases
                ):
                    violations.append(f"{path.relative_to(root)}:{node.lineno}: qualified")
    assert violations == []
