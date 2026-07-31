"""Protect workflow package locality and the deliberately small public facades."""

from __future__ import annotations

import ast
import importlib.util
import inspect
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "restscope"
OPERATION_SMOKE_ROOT = SOURCE_ROOT / "operation_smoke"
REPOSITORY_ROOT = SOURCE_ROOT.parent
CURRENT_DOCUMENTS = (
    REPOSITORY_ROOT / "AGENTS.md",
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "docs" / "code-reading-guide.md",
    REPOSITORY_ROOT / "tests" / "README.md",
)


def test_workflows_replace_the_retired_agent_category_package() -> None:
    """Scenario: runtime workflows own their code and no category package remains."""
    retired_package = "restscope" + ".agent"
    assert not (SOURCE_ROOT / "agent").exists()
    assert importlib.util.find_spec(retired_package) is None

    for package_name in (
        "operation_smoke",
        "api_behavior_monitor",
        "supervisor",
    ):
        package = SOURCE_ROOT / package_name
        assert package.is_dir(), f"missing workflow package: {package_name}"
        assert (package / "__init__.py").is_file()


def test_operation_smoke_llm_roles_keep_independent_internal_seams() -> None:
    """Scenario: each LLM role remains a named package inside its workflow."""
    for package_name in (
        "failure_dedup",
        "failure_solver",
        "parameter_patch",
    ):
        package = OPERATION_SMOKE_ROOT / package_name
        assert package.is_dir(), f"missing Operation Smoke role: {package_name}"
        assert (package / "__init__.py").is_file()
        assert (package / "agent.py").is_file()
        assert (package / "schemas.py").is_file()
    assert not (OPERATION_SMOKE_ROOT / "effect").exists()
    assert not (OPERATION_SMOKE_ROOT / "plan").exists()
    assert not (OPERATION_SMOKE_ROOT / "prompt_context" / "__init__.py").exists()


def test_current_sources_do_not_restore_retired_agent_names_or_paths() -> None:
    """Scenario: the migration has no old imports, names, properties, or aliases."""
    retired_terms = {
        "restscope" + ".agent",
        "restscope" + "/agent",
        "OperationSmoke" + "Agent",
        "operation_smoke_" + "agent",
        "build_operation_smoke_" + "agent",
        "APIBehaviorMonitor" + "Agent",
        "api_behavior_monitor_" + "agent",
        "build_api_behavior_monitor_" + "agent",
    }
    current_files = [
        *SOURCE_ROOT.rglob("*.py"),
        *(REPOSITORY_ROOT / "tests").glob("*.py"),
        *CURRENT_DOCUMENTS,
    ]
    violations: list[str] = []

    for path in current_files:
        text = path.read_text(encoding="utf-8")
        for retired_term in retired_terms:
            if retired_term in text:
                violations.append(f"{path.relative_to(REPOSITORY_ROOT)}: {retired_term}")

    assert violations == []


def test_workflow_facades_export_only_the_approved_interfaces() -> None:
    """Scenario: callers see workflow interfaces rather than implementation roles."""
    import restscope.api_behavior_monitor as behavior_monitor
    import restscope.operation_smoke as operation_smoke

    assert set(operation_smoke.__all__) == {
        "BehaviorMonitorReferenceValues",
        "OperationSmokeCoordinator",
        "OperationSmokeRequest",
        "OperationSmokeResult",
        "PatchAttemptSummary",
        "SmokeBatchRunner",
        "SmokeRoundSummary",
        "TodoRunSummary",
        "build_operation_smoke_coordinator",
    }
    assert set(behavior_monitor.__all__) == {
        "APIBehaviorMonitorCoordinator",
        "APIBehaviorMonitorError",
        "APIBehaviorMonitorResult",
        "APIBehaviorResponseProcessor",
        "APIBehaviorWarning",
        "RESOURCE_LOOKUP_TOOL_NAME",
        "ResourceLookupRequest",
        "ResourceLookupResult",
        "ResponseValueSource",
        "build_api_behavior_monitor_coordinator",
        "register_resource_lookup_tool",
    }


def test_top_level_facade_hides_workflow_implementation_types() -> None:
    """Scenario: application users import workflows only when customizing them."""
    import restscope

    internal_names = {
        "APIBehaviorMonitorCoordinator",
        "FailureSolveAgent",
        "OperationSmokeCoordinator",
        "OperationSmokeRequest",
        "OperationSmokeResult",
        "ParameterPatchAgent",
        "ResourceIdentifierTracker",
        "SmokeEffectAgent",
        "FailureDedupAgent",
        "build_api_behavior_monitor_coordinator",
        "build_operation_smoke_coordinator",
    }
    assert all(not hasattr(restscope, name) for name in internal_names)


def test_cross_role_imports_use_the_target_role_facade() -> None:
    """Scenario: one Agent never reaches into another Agent's implementation file."""
    role_names = {"failure_dedup", "failure_solver", "parameter_patch"}
    violations: list[str] = []

    for path in OPERATION_SMOKE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            parts = node.module.split(".")
            if parts[:2] != ["restscope", "operation_smoke"]:
                continue
            if len(parts) > 3 and parts[2] in role_names:
                violations.append(f"{path.name}:{node.lineno} imports {node.module}")

    assert violations == []


def test_retired_operation_test_contracts_remain_absent() -> None:
    """Scenario: package movement does not revive the retired test-planning design."""
    import restscope
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
    for builder in (
        RESTScopeApp,
        RESTScopeApp.from_environment,
        RESTScopeApp.from_config,
    ):
        parameters = inspect.signature(builder).parameters
        assert "operation_runner" not in parameters
        assert "dependency_analyzer" not in parameters


def test_retired_openapi_retrieval_contracts_remain_absent() -> None:
    """Scenario: workflow movement does not restore the retired retrieval Agent."""
    import restscope

    retired = {
        "OpenAPIRetrievalAgent",
        "OpenAPIRetrievalRequest",
        "OpenAPIRetrievalResult",
        "ParameterValueProducerQuery",
        "build_openapi_retrieval_agent",
        "register_openapi_retrieval_tool",
    }
    assert all(not hasattr(restscope, name) for name in retired)
