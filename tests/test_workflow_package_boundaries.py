"""Protect the Main Agent, Subagent, Skill, Tool, and Harness package seams."""

from __future__ import annotations

import ast
import importlib.util
import inspect
import os
from pathlib import Path
import subprocess
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "restscope"
REPOSITORY_ROOT = SOURCE_ROOT.parent
CURRENT_DOCUMENTS = (
    REPOSITORY_ROOT / "AGENTS.md",
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "docs" / "code-reading-guide.md",
    REPOSITORY_ROOT / "tests" / "README.md",
)


def test_root_contains_only_the_app_facade_composition_and_config() -> None:
    """Scenario: unrelated domains cannot drift back into the package root."""
    assert {path.name for path in SOURCE_ROOT.glob("*.py")} == {
        "__init__.py",
        "app.py",
        "config.py",
    }


def test_root_facade_is_exact_and_import_has_no_process_side_effects(
    tmp_path: Path,
) -> None:
    """Scenario: a clean package import neither configures logging nor writes files."""
    probe = """
import json
import logging
from pathlib import Path
before = tuple(id(handler) for handler in logging.getLogger().handlers)
import restscope
after = tuple(id(handler) for handler in logging.getLogger().handlers)
print(json.dumps({
    "exports": restscope.__all__,
    "handlers_unchanged": before == after,
    "files": sorted(str(path.relative_to(Path.cwd())) for path in Path.cwd().rglob("*")),
}))
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(REPOSITORY_ROOT)
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    import json

    observed = json.loads(completed.stdout)
    assert observed == {
        "exports": ["RESTScopeApp", "RESTScopeConfig"],
        "handlers_unchanged": True,
        "files": [],
    }


def test_retired_root_and_broad_owner_modules_are_absent() -> None:
    """Scenario: direct replacements have no compatibility module or alias."""
    retired = (
        "bootstrap.py",
        "http_transport.py",
        "logging_config.py",
        "operations.py",
        "randomness.py",
        "redaction.py",
        "request_inputs.py",
        "response_fields.py",
        "restscope_config.py",
    )
    assert all(not (SOURCE_ROOT / name).exists() for name in retired)
    assert not (SOURCE_ROOT / "catalog").exists()
    assert not (SOURCE_ROOT / "operation_smoke").exists()
    assert not (SOURCE_ROOT / "harness" / "testing").exists()
    assert not (SOURCE_ROOT / "skills" / "registry.py").exists()
    assert not (SOURCE_ROOT / "api_behavior_monitor" / "prompts.py").exists()
    assert not (SOURCE_ROOT / "tools" / "openapi" / "lookup.py").exists()
    assert not (SOURCE_ROOT / "tools" / "test_case" / "runtime.py").exists()
    assert not (SOURCE_ROOT / "tools" / "test_case" / "specs.py").exists()
    assert not (SOURCE_ROOT / "tools" / "test_case" / "bindings.py").exists()
    assert not (SOURCE_ROOT / "request_generation" / "patch_models.py").exists()
    assert not (SOURCE_ROOT / "request_generation" / "patch_validation.py").exists()

    for expected in (
        SOURCE_ROOT / "tools" / "openapi" / "input_queries.py",
        SOURCE_ROOT / "tools" / "openapi" / "response_queries.py",
        SOURCE_ROOT / "tools" / "openapi" / "observed_queries.py",
        SOURCE_ROOT / "tools" / "test_case" / "run_batch.py",
        SOURCE_ROOT / "api_behavior_monitor" / "resource_identity.py",
    ):
        assert expected.is_file(), f"missing focused owner: {expected}"


def test_parameter_patch_is_one_request_generation_runtime_capability() -> None:
    """Harness and Tool adapters cannot assemble Patch state or reach its Store."""
    patch_package = SOURCE_ROOT / "request_generation" / "parameter_patch"
    assert {path.name for path in patch_package.glob("*.py")} == {
        "__init__.py",
        "compiler.py",
        "errors.py",
        "models.py",
        "projection.py",
        "runtime.py",
    }
    harness_source = (SOURCE_ROOT / "harness" / "runtime.py").read_text(
        encoding="utf-8"
    )
    tool_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            SOURCE_ROOT / "tools" / "request_generation" / "runtime.py",
            SOURCE_ROOT / "tools" / "parameter_patch" / "apply.py",
        )
    )
    assert "RequestGenerationPatchRuntime(" not in harness_source
    assert "runtime.store" not in tool_sources
    assert "runtime._store" not in tool_sources


def test_parameter_patch_uses_one_concrete_reference_collaborator() -> None:
    """Patch reads and staged writes enter through one complete integration."""

    from restscope.request_generation import BehaviorMonitorReferences
    from restscope.request_generation.parameter_patch.runtime import (
        RequestGenerationPatchRuntime,
    )

    parameters = inspect.signature(RequestGenerationPatchRuntime).parameters
    facade = __import__("restscope.request_generation", fromlist=["__all__"])
    runtime_source = (
        SOURCE_ROOT / "request_generation" / "parameter_patch" / "runtime.py"
    ).read_text(encoding="utf-8")

    assert BehaviorMonitorReferences.__name__ == "BehaviorMonitorReferences"
    assert "BehaviorMonitorReferences" in facade.__all__
    assert "BehaviorMonitorReferenceValues" not in facade.__all__
    assert "references" in parameters
    assert "reference_values" not in parameters
    assert "reference_binding_stager" not in parameters
    assert "_ReferenceBindingStager" not in runtime_source
    assert "hasattr(provider" not in runtime_source


def test_behavior_coordinator_uses_the_concrete_resource_tracker() -> None:
    """Resource derivation has one owner rather than a duplicate Protocol name."""

    from restscope.api_behavior_monitor.coordinator import (
        APIBehaviorMonitorCoordinator,
    )
    from restscope.api_behavior_monitor.resource_monitor import (
        ResourceResponseTracker,
    )

    annotation = inspect.signature(APIBehaviorMonitorCoordinator).parameters[
        "resource_tracker"
    ].annotation
    coordinator_source = (
        SOURCE_ROOT / "api_behavior_monitor" / "coordinator.py"
    ).read_text(encoding="utf-8")

    assert "ResourceResponseTracker" in str(annotation)
    assert "class ResourceResponseTracker(Protocol)" not in coordinator_source
    assert "from .resource_monitor import ResourceResponseTracker" in coordinator_source
    assert ResourceResponseTracker.__module__.endswith("resource_monitor")


def test_app_retains_the_concrete_ui_service_without_a_host_protocol() -> None:
    """The optional viewer lifecycle uses its sole concrete implementation."""

    app_source = (SOURCE_ROOT / "app.py").read_text(encoding="utf-8")

    assert "_UIServiceHost" not in app_source
    assert "from restscope.ui import UIService, start_ui_service" in app_source
    assert "ui_service: UIService | None" in app_source


def test_protocol_inventory_contains_only_reviewed_real_seams() -> None:
    """Every retained Protocol has multiple adapters or third-party isolation."""

    retained: set[tuple[str, str]] = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if any(
                isinstance(base, ast.Name) and base.id == "Protocol"
                for base in node.bases
            ):
                retained.add((str(path.relative_to(SOURCE_ROOT)), node.name))

    assert retained == {
        ("agent/ports.py", "AgentToolExecutor"),
        ("agent/ports.py", "AgentTreeControlPort"),
        ("agent/ports.py", "BudgetChargeView"),
        ("api_behavior_monitor/catalog.py", "_APIBehaviorRepository"),
        ("api_behavior_monitor/catalog.py", "_APIBehaviorUnitOfWork"),
        ("api_behavior_monitor/resource_identity.py", "SystemAgentRunner"),
        ("request_generation/ports.py", "ReferenceValueProvider"),
        ("skills/loader.py", "_Traversable"),
        ("target_api/observation.py", "TargetResponseProcessor"),
        ("tools/openapi/observed_queries.py", "ObservedResponseReader"),
        ("tools/test_case/run_batch.py", "BatchExecutionBackend"),
        ("ui/server.py", "_ASGIApplication"),
        ("ui/server.py", "_CursorRequest"),
        ("ui/server.py", "_ServerHandle"),
    }


def test_top_level_production_dependencies_are_acyclic() -> None:
    """Scenario: ownership moves cannot recreate a mutually dependent package set."""
    graph: dict[str, set[str]] = {}

    for path in SOURCE_ROOT.rglob("*.py"):
        relative = path.relative_to(SOURCE_ROOT)
        source = relative.parts[0] if len(relative.parts) > 1 else "<root>"
        graph.setdefault(source, set())
        package_parts = list(relative.parts[:-1])
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            target: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if parts[0] == "restscope" and len(parts) > 1:
                        graph[source].add(parts[1])
                continue
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.level:
                retained = package_parts[: len(package_parts) - node.level + 1]
                parts = [*retained, *(node.module or "").split(".")]
                parts = [part for part in parts if part]
                target = parts[0] if parts else source
            elif node.module and node.module.startswith("restscope."):
                target = node.module.split(".")[1]
            if target and target != source:
                graph[source].add(target)

    def reaches(start: str, target: str) -> bool:
        """Return whether package edges lead from start back to target."""
        pending = [start]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(graph.get(current, ()))
        return False

    cyclic_edges = sorted(
        (source, target)
        for source, targets in graph.items()
        for target in targets
        if reaches(target, source)
    )
    assert cyclic_edges == []


def test_production_docstrings_do_not_use_audited_generated_templates() -> None:
    """Scenario: empty generated prose cannot replace domain documentation."""
    templates = (
        "The annotated arguments and return type",
        "The class owns any required collaborators",
        "Read the public methods as the supported lifecycle",
        "Carry validated ",
        "Define the collaborator contract",
        "This private helper keeps one transformation or policy decision explicit",
        " as part of deterministic request generation",
        " as part of API response monitoring",
        " as part of bounded, redacted tracing",
    )
    violations = [
        f"{path.relative_to(REPOSITORY_ROOT)}: {template}"
        for path in SOURCE_ROOT.rglob("*.py")
        for template in templates
        if template in path.read_text(encoding="utf-8")
    ]
    assert violations == []


def test_core_runtime_language_has_explicit_global_packages() -> None:
    """Scenario: readers can locate each new core concept without old facades."""
    for package_name in ("agent", "skills", "tools", "harness"):
        package = SOURCE_ROOT / package_name
        assert package.is_dir(), f"missing core package: {package_name}"
        assert (package / "__init__.py").is_file()
    for retired_package in ("capabilities", "supervisor", "testing"):
        assert not (SOURCE_ROOT / retired_package).exists()
        assert importlib.util.find_spec(f"restscope.{retired_package}") is None


def test_target_api_is_the_only_target_request_foundation() -> None:
    """Readers find one top-level target Client and no retired transport path."""

    import restscope.target_api as target_api

    assert importlib.util.find_spec("restscope.target_http") is None
    assert (SOURCE_ROOT / "target_api" / "client.py").is_file()
    assert (SOURCE_ROOT / "target_api" / "request.py").is_file()
    assert (SOURCE_ROOT / "target_api" / "media_type.py").is_file()
    assert not hasattr(target_api, "TargetHTTPTransport")
    assert not hasattr(target_api, "TargetHTTPTransportError")
    assert not hasattr(target_api, "TargetHTTPTimeout")

    batch_source = (
        SOURCE_ROOT / "harness" / "operation_testing" / "service.py"
    ).read_text(encoding="utf-8")
    assert "has_response_processor" not in batch_source
    assert ".run_observer" not in batch_source

    media_sources = [
        path
        for path in SOURCE_ROOT.rglob("*.py")
        if "def normalize_media_type(" in path.read_text(encoding="utf-8")
        or "def is_json_media_type(" in path.read_text(encoding="utf-8")
    ]
    assert media_sources == [SOURCE_ROOT / "target_api" / "media_type.py"]


def test_app_uses_the_concrete_harness_runtime_without_a_duplicate_protocol() -> None:
    """Readers find one concrete Harness type and no duck-typed App seam."""

    from restscope.harness import HarnessRuntime, build_harness

    runtime = build_harness()
    app_source = (SOURCE_ROOT / "app.py").read_text(encoding="utf-8")
    harness_source = (SOURCE_ROOT / "harness" / "runtime.py").read_text(
        encoding="utf-8"
    )

    assert type(runtime) is HarnessRuntime
    assert runtime.http_request_tool is not None
    assert "_AppHarnessRuntime" not in app_source
    assert "_StartableRuntimeLoop" not in app_source
    assert "_ClosableHost" not in app_source
    assert "getattr(self.harness_runtime" not in app_source
    assert "target_http_tool" not in harness_source


def test_only_the_generic_agent_remains() -> None:
    """Scenario: retired workflow roles cannot recreate named Agent classes."""
    found: set[str] = set()
    for path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name.endswith("Agent")
        )
    assert found == {"Agent"}


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

    assert importlib.util.find_spec("restscope.operation_smoke") is None
    assert set(behavior_monitor.__all__) == {
        "APIBehaviorCatalog",
        "APIBehaviorMonitorCoordinator",
        "APIBehaviorMonitorError",
        "APIBehaviorMonitorResult",
        "APIBehaviorResponseProcessor",
        "APIBehaviorWarning",
        "build_api_behavior_monitor_coordinator",
    }


def test_api_behavior_monitor_owns_its_complete_persistence_navigation() -> None:
    """Readers find one Catalog and no retired peer Audit or Monitor seams."""

    import restscope.api_behavior_monitor as behavior_monitor
    import restscope.db as database

    assert importlib.util.find_spec("restscope.openapi_audit") is None
    assert not (SOURCE_ROOT / "api_behavior_monitor" / "response_contracts").exists()
    assert not (SOURCE_ROOT / "api_behavior_monitor" / "resource_identifiers").exists()
    assert not (SOURCE_ROOT / "db" / "adapters" / "openapi_audit.py").exists()
    assert not (SOURCE_ROOT / "db" / "adapters" / "response_monitor.py").exists()
    assert not (SOURCE_ROOT / "db" / "orm" / "openapi_orm.py").exists()
    assert not (SOURCE_ROOT / "db" / "orm" / "response_monitor_orm.py").exists()
    assert hasattr(behavior_monitor, "APIBehaviorCatalog")
    assert hasattr(database, "SqlAlchemyAPIBehaviorUnitOfWork")
    for retired_name in (
        "OpenAPIAudit",
        "OpenAPIRepository",
        "OpenAPIUnitOfWork",
        "ResponseMonitorCatalog",
        "ResponseMonitorRepository",
        "ResponseMonitorUnitOfWork",
        "SqlAlchemyOpenAPIUnitOfWork",
        "SqlAlchemyResponseMonitorUnitOfWork",
    ):
        assert not hasattr(behavior_monitor, retired_name)
        assert not hasattr(database, retired_name)


def test_request_generation_facade_exposes_only_integration_entries() -> None:
    """The package doorway points readers to four cross-Module entry points."""

    import restscope.request_generation as request_generation

    assert set(request_generation.__all__) == {
        "BehaviorMonitorReferences",
        "RequestGenerationConfigStore",
        "RequestGenerationPatchRuntime",
        "SeededRandom",
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


def test_new_subject_facades_expose_only_the_approved_shared_interfaces() -> None:
    """Scenario: broad root modules are replaced by precise package doorways."""
    import restscope.operation_references as references
    import restscope.tools as tools

    assert set(references.__all__) == {
        "RequestInputLocation",
        "RequestInputReference",
        "ResponseFieldReference",
    }
    assert set(tools.__all__) == {
        "AgentToolbox",
        "ToolBinding",
        "ToolCatalog",
        "ToolDefinition",
        "ToolFailure",
        "ToolSubject",
        "builtin_tool_catalog",
    }


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
