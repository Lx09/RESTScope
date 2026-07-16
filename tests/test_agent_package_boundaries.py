from __future__ import annotations

import ast
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parents[1] / "restscope" / "agent"


def test_agent_root_is_facade_and_each_agent_is_a_package() -> None:
    assert {path.name for path in AGENT_ROOT.glob("*.py")} == {"__init__.py"}

    for package_name in ("operation_test", "supervisor"):
        package = AGENT_ROOT / package_name
        assert package.is_dir(), f"missing Agent package: {package_name}"
        assert (package / "__init__.py").is_file()
        assert (package / "agent.py").is_file() or (package / "graph.py").is_file()
        assert (package / "schemas.py").is_file()


def test_agent_package_and_compatibility_facade_export_same_contracts() -> None:
    from restscope.agent import (
        OperationTestAgent,
        RESTScopeMainGraph,
    )
    from restscope.agent.operation_test import OperationTestAgent as PackagedOperationTestAgent
    from restscope.agent.supervisor import RESTScopeMainGraph as PackagedMainGraph

    assert OperationTestAgent is PackagedOperationTestAgent
    assert RESTScopeMainGraph is PackagedMainGraph


def test_cross_agent_imports_use_package_facades() -> None:
    package_names = {"operation_test", "supervisor"}
    violations: list[str] = []

    for package_name in package_names:
        for path in (AGENT_ROOT / package_name).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                parts = node.module.split(".")
                if node.level >= 2 and parts[0] in package_names and len(parts) > 1:
                    violations.append(f"{path.name}:{node.lineno} imports {node.module}")
                if parts[:2] == ["restscope", "agent"] and len(parts) > 3:
                    violations.append(f"{path.name}:{node.lineno} imports {node.module}")

    assert violations == []
