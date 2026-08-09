"""Protect the Main Agent, Subagent, Skill, Tool, and Harness package seams."""

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


def test_core_runtime_language_has_explicit_global_packages() -> None:
    """Scenario: readers can locate each new core concept without old facades."""
    for package_name in ("agent", "skills", "tools", "harness"):
        package = SOURCE_ROOT / package_name
        assert package.is_dir(), f"missing core package: {package_name}"
        assert (package / "__init__.py").is_file()
    for retired_package in ("capabilities", "supervisor", "testing"):
        assert not (SOURCE_ROOT / retired_package).exists()
        assert importlib.util.find_spec(f"restscope.{retired_package}") is None


def test_only_the_documented_transitional_named_agents_remain() -> None:
    """Scenario: this migration cannot accidentally add another domain Agent."""
    found: set[str] = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name.endswith("Agent")
        )
    assert found == {
        "FailureResolutionAgent",
        "FailureResolutionCompactAgent",
        "ParameterPatchAgent",
        "ParameterPatchReviewAgent",
        "Agent",
    }


def test_transitional_agents_declare_explicit_profiles() -> None:
    """Scenario: every temporary named Agent makes its current access explicit."""
    agent_modules = (
        OPERATION_SMOKE_ROOT / "failure_resolution" / "agent.py",
        OPERATION_SMOKE_ROOT / "failure_resolution" / "compact" / "agent.py",
        OPERATION_SMOKE_ROOT / "parameter_patch" / "agent.py",
        OPERATION_SMOKE_ROOT / "parameter_patch" / "review" / "agent.py",
    )

    for path in agent_modules:
        source = path.read_text(encoding="utf-8")
        assert "AgentProfile(" in source, f"missing Agent Profile: {path}"


def test_owned_tool_specs_live_only_in_global_tool_modules() -> None:
    """Scenario: workflows and Harnesses cannot author private Tool contracts."""
    violations: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if path.is_relative_to(SOURCE_ROOT / "tools") or path == SOURCE_ROOT / "llm" / "schemas.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ToolSpec"
            ):
                violations.append(f"{path.relative_to(REPOSITORY_ROOT)}:{node.lineno}")
    assert violations == []


def test_production_tool_registration_lives_only_in_global_tool_modules() -> None:
    """Scenario: workflows and Harnesses bind Catalog Tools instead of inventing them."""
    violations: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if path.is_relative_to(SOURCE_ROOT / "tools"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == "register" and any(
                keyword.arg == "spec" for keyword in node.keywords
            ):
                violations.append(f"{path.relative_to(REPOSITORY_ROOT)}:{node.lineno}")

    assert violations == []


def test_current_sources_do_not_restore_retired_agent_names_or_paths() -> None:
    """Scenario: the migration has no old imports, names, properties, or aliases."""
    retired_terms = {
        "restscope" + ".capabilities",
        "restscope" + "/capabilities",
        "restscope" + ".testing",
        "restscope" + "/testing",
        "restscope" + ".supervisor",
        "restscope" + "/supervisor",
        "RESTScopeMain" + "Graph",
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


def test_main_agent_replacement_removes_run_harness_and_graph_dependencies() -> None:
    """The blocking Main loop has no legacy FIFO module or graph framework."""
    import restscope
    import restscope.harness as harness
    from restscope import RESTScopeApp

    project = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    production = "\n".join(
        path.read_text(encoding="utf-8") for path in SOURCE_ROOT.rglob("*.py")
    )

    assert not (SOURCE_ROOT / "harness" / "run.py").exists()
    for old_name in (
        "OperationAttempt",
        "RESTScopeRunReport",
        "RESTScopeRunRequest",
        "RunHarness",
    ):
        assert not hasattr(restscope, old_name)
        assert not hasattr(harness, old_name)
    assert not hasattr(RESTScopeApp, "run")
    assert hasattr(RESTScopeApp, "start")
    assert "lang" + "graph" not in project
    assert "lang" + "graph" not in production


def test_workflow_facades_export_only_the_approved_interfaces() -> None:
    """Scenario: callers see workflow interfaces rather than implementation roles."""
    import restscope.api_behavior_monitor as behavior_monitor
    import restscope.operation_smoke as operation_smoke

    assert set(operation_smoke.__all__) == {
        "BehaviorMonitorReferenceValues",
        "OperationSmokeCoordinator",
        "OperationSmokeRequest",
        "OperationSmokeResult",
        "ResolutionItemSummary",
        "ResolutionPatchSummary",
        "SmokeBatchRunner",
        "SmokeRoundSummary",
        "build_operation_smoke_coordinator",
    }
    assert set(behavior_monitor.__all__) == {
        "APIBehaviorMonitorCoordinator",
        "APIBehaviorMonitorError",
        "APIBehaviorMonitorResult",
        "APIBehaviorResponseProcessor",
        "APIBehaviorWarning",
        "ResourceLookupRequest",
        "ResourceLookupResult",
        "ResponseValueSource",
        "build_api_behavior_monitor_coordinator",
    }


def test_top_level_facade_hides_workflow_implementation_types() -> None:
    """Scenario: application users import workflows only when customizing them."""
    import restscope

    internal_names = {
        "APIBehaviorMonitorCoordinator",
        "FailureResolutionAgent",
        "OperationSmokeCoordinator",
        "OperationSmokeRequest",
        "OperationSmokeResult",
        "ParameterPatchAgent",
        "ParameterPatchCoordinator",
        "ParameterPatchReviewAgent",
        "ResourceIdentifierTracker",
        "SmokeEffectAgent",
        "build_api_behavior_monitor_coordinator",
        "build_operation_smoke_coordinator",
    }
    assert all(not hasattr(restscope, name) for name in internal_names)


def test_cross_role_imports_use_the_target_role_facade() -> None:
    """Scenario: one Agent never reaches into another Agent's implementation file."""
    role_names = {
        "failure_resolution",
        "parameter_patch",
    }
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
