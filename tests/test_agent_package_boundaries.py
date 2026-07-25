from __future__ import annotations

import ast
import inspect
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parents[1] / "restscope" / "agent"


def test_agent_root_is_facade_and_each_agent_is_a_package() -> None:
    assert {path.name for path in AGENT_ROOT.glob("*.py")} == {"__init__.py"}

    for package_name in (
        "operation_smoke",
        "api_behavior_monitor",
        "supervisor",
    ):
        package = AGENT_ROOT / package_name
        assert package.is_dir(), f"missing Agent package: {package_name}"
        assert (package / "__init__.py").is_file()
        assert (package / "agent.py").is_file() or (package / "graph.py").is_file()
        assert (package / "schemas.py").is_file()


def test_agent_package_and_public_facade_export_same_contracts() -> None:
    from restscope.agent import (
        OperationSmokeAgent,
        APIBehaviorMonitorAgent,
        RESTScopeMainGraph,
    )
    from restscope.agent.operation_smoke import OperationSmokeAgent as PackagedOperationSmokeAgent
    from restscope.agent.api_behavior_monitor import APIBehaviorMonitorAgent as PackagedAPIBehaviorMonitorAgent
    from restscope.agent.supervisor import RESTScopeMainGraph as PackagedMainGraph

    assert OperationSmokeAgent is PackagedOperationSmokeAgent
    assert APIBehaviorMonitorAgent is PackagedAPIBehaviorMonitorAgent
    assert RESTScopeMainGraph is PackagedMainGraph


def test_cross_agent_imports_use_package_facades() -> None:
    package_names = {
        "operation_smoke",
        "api_behavior_monitor",
        "supervisor",
    }
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
def test_retired_operation_test_agent_package_is_absent() -> None:
    assert not list((AGENT_ROOT / "operation_test").rglob("*.py"))


def test_retired_operation_test_contracts_are_not_public_or_in_app_builders() -> None:
    import restscope
    import restscope.agent as agents
    from restscope import RESTScopeApp

    retired = {
        "OperationTestAgent",
        "OperationTestRunner",
        "OperationDependencyAnalyzer",
        "OperationCandidate",
        "OperationTarget",
        "OperationTestRequest",
        "OperationTestReport",
        "Schema" + "thesisOperationRunner",
    }
    assert all(not hasattr(restscope, name) for name in retired)
    assert all(not hasattr(agents, name) for name in retired)
    for builder in (
        RESTScopeApp,
        RESTScopeApp.from_environment,
        RESTScopeApp.from_config,
    ):
        parameters = inspect.signature(builder).parameters
        assert "operation_runner" not in parameters
        assert "dependency_analyzer" not in parameters


def test_retired_openapi_retrieval_agent_is_absent_and_not_public() -> None:
    import restscope
    import restscope.agent as agents

    assert not list((AGENT_ROOT / "openapi_retrieval").rglob("*.py"))
    retired = {
        "OpenAPIRetrievalAgent",
        "OpenAPIRetrievalRequest",
        "OpenAPIRetrievalResult",
        "ParameterValueProducerQuery",
        "build_openapi_retrieval_agent",
        "register_openapi_retrieval_tool",
    }
    assert all(not hasattr(restscope, name) for name in retired)
    assert all(not hasattr(agents, name) for name in retired)
