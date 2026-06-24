"""Build role-specific ContextSection objects from MemoryPackage."""

from __future__ import annotations

from typing import Any

from restscope.memory import MemoryPackage

from .context_policy import ContextPolicy
from .schemas import ContextSection, ContextSectionKind, OutputContract, SourceRef


def build_sections(
    *,
    policy: ContextPolicy,
    memory_package: MemoryPackage,
    output_contract: OutputContract,
) -> list[ContextSection]:
    builders = {
        "role_instruction": _role_instruction,
        "task_state": _task_state,
        "test_goal": _test_goal,
        "budget": _budget,
        "operation_targets": _operation_targets,
        "operation_risk_profile": _operation_risk_profile,
        "historical_observations": _historical_observations,
        "campaign_history": _campaign_history,
        "current_campaign_result": _current_campaign_result,
        "recent_events": _recent_events,
        "tool_affordances": _tool_affordances,
        "execution_assumptions": _execution_assumptions,
        "output_contract": lambda mp: _output_contract(mp, output_contract),
    }
    sections: list[ContextSection] = []
    for section_policy in policy.section_policies:
        builder = builders[section_policy.kind]
        section = builder(memory_package)
        sections.append(
            section.model_copy(
                update={
                    "required": section_policy.required,
                    "priority": section_policy.priority,
                }
            )
        )
    return sections


def _role_instruction(memory_package: MemoryPackage) -> ContextSection:
    role = memory_package.role
    if role == "planner":
        content = (
            "You are the Planner. Select the next Schemathesis campaign, target operations, "
            "testing hypothesis, rationale, expected learning, and stop conditions. "
            "Do not claim tests were executed or modify task state."
        )
    elif role == "result_analyst":
        content = (
            "You are the Result Analyst. Analyze the current campaign result, identify new or "
            "duplicate observations, and classify flake/spec/environment issues. Do not write DB state."
        )
    else:
        content = (
            "You are the Decision Maker. Choose the next workflow action from the context. "
            "Do not execute tests or update task state."
        )
    return _section("role_instruction", "Role", content)


def _task_state(memory_package: MemoryPackage) -> ContextSection:
    working = memory_package.working_memory[0] if memory_package.working_memory else None
    structured = working.structured if working else {}
    content = "\n".join(
        [
            f"- Task ID: {memory_package.task_id}",
            f"- State: {structured.get('state', 'unknown')}",
            f"- Cycle index: {structured.get('cycle_index', 0)}",
            "- Current hypotheses:",
            *[f"  - {item}" for item in structured.get("current_hypotheses", [])],
        ]
    )
    return _section("task_state", "Current task state", content, structured=structured)


def _test_goal(memory_package: MemoryPackage) -> ContextSection:
    structured = memory_package.working_memory[0].structured if memory_package.working_memory else {}
    goal = structured.get("goal", {})
    content = "\n".join(
        [
            "Goal:",
            f"- {goal.get('goal', 'Explore REST API behavior using Schemathesis.')}",
            f"- Target: {goal.get('target', 'live test environment')}",
        ]
    )
    return _section("test_goal", "Test goal", content, structured=goal)


def _budget(memory_package: MemoryPackage) -> ContextSection:
    structured = memory_package.working_memory[0].structured if memory_package.working_memory else {}
    budget = structured.get("budget", {})
    lines = [f"- {key}: {value}" for key, value in budget.items()] or ["- No explicit budget."]
    return _section("budget", "Budget", "\n".join(lines), structured=budget)


def _operation_targets(memory_package: MemoryPackage) -> ContextSection:
    lines = []
    refs = []
    for item in memory_package.operation_memory:
        lines.extend(
            [
                f"### {item.title}",
                f"- Operation ID: {item.operation_id}",
                f"- Mutability: {item.structured.get('mutability')}",
                f"- Static risk: {item.structured.get('static_risk_score')}",
                f"- Dynamic risk: {item.structured.get('dynamic_risk_score')}",
                f"- Recommended checks: {', '.join(item.structured.get('recommended_checks', [])) or 'none'}",
            ]
        )
        refs.append(_source_ref(item.source_table, item.source_id))
    return _section("operation_targets", "Candidate operations", "\n".join(lines), source_refs=refs)


def _operation_risk_profile(memory_package: MemoryPackage) -> ContextSection:
    lines = []
    refs = []
    for index, item in enumerate(memory_package.operation_memory, start=1):
        lines.append(
            f"{index}. {item.title} - dynamic_risk_score={item.structured.get('dynamic_risk_score')}, "
            f"failure_density={item.structured.get('failure_density')}, risk_score={item.risk_score}"
        )
        refs.append(_source_ref(item.source_table, item.source_id))
    return _section("operation_risk_profile", "Operation risk profile", "\n".join(lines), source_refs=refs)


def _historical_observations(memory_package: MemoryPackage) -> ContextSection:
    lines = []
    refs = []
    for item in memory_package.observation_memory:
        lines.extend(
            [
                f"### {item.title}",
                f"- Observation ID: {item.observation_id}",
                f"- Severity: {item.structured.get('severity')}",
                f"- Confidence: {item.structured.get('confidence')}",
                f"- Occurrence count: {item.structured.get('occurrence_count')}",
                f"- Request summary: {item.structured.get('request_summary')}",
                f"- Response summary: {item.structured.get('response_summary')}",
                f"- Reproducer artifact: {item.structured.get('reproducer_artifact_id')}",
            ]
        )
        refs.append(_source_ref(item.source_table, item.source_id))
    return _section("historical_observations", "Historical observations", "\n".join(lines), source_refs=refs)


def _campaign_history(memory_package: MemoryPackage) -> ContextSection:
    lines = [
        f"- {item.title}: {item.structured.get('summary', {})}, artifact_bundle_uri={item.structured.get('artifact_bundle_uri')}"
        for item in memory_package.campaign_memory
    ]
    refs = [_source_ref(item.source_table, item.source_id) for item in memory_package.campaign_memory]
    return _section("campaign_history", "Recent campaign history", "\n".join(lines), source_refs=refs)


def _current_campaign_result(memory_package: MemoryPackage) -> ContextSection:
    return _section(
        "current_campaign_result",
        "Current campaign result",
        _campaign_history(memory_package).content or "No current campaign summary available.",
        source_refs=_campaign_history(memory_package).source_refs,
    )


def _recent_events(memory_package: MemoryPackage) -> ContextSection:
    lines = [f"- {item.title}: {item.content}" for item in memory_package.episodic_memory]
    refs = [_source_ref(item.source_table, item.source_id) for item in memory_package.episodic_memory]
    return _section("recent_events", "Recent task events", "\n".join(lines), source_refs=refs)


def _tool_affordances(memory_package: MemoryPackage) -> ContextSection:
    del memory_package
    content = "\n".join(
        [
            "The system can execute Schemathesis campaigns against the live test target.",
            "Supported campaign types: smoke, broad_contract, risk_targeted_fuzzing, stateful_workflow, regression_retest, check_validation.",
            "Supported checks: not_a_server_error, status_code_conformance, response_schema_conformance, content_type_conformance.",
        ]
    )
    return _section("tool_affordances", "Available testing capabilities", content)


def _execution_assumptions(memory_package: MemoryPackage) -> ContextSection:
    del memory_package
    content = "\n".join(
        [
            "This system runs with unrestricted live testing against a dedicated test environment.",
            "GET, POST, PUT, PATCH, and DELETE are all valid testing targets.",
            "Destructive operations are part of the test scope.",
            "The Planner should not avoid mutating operations solely because they have side effects.",
            "The Planner must still respect task budget, runner capability, and known operation IDs.",
        ]
    )
    return _section("execution_assumptions", "Execution assumptions", content)


def _output_contract(memory_package: MemoryPackage, output_contract: OutputContract) -> ContextSection:
    del memory_package
    content = "\n".join(
        [
            f"Return only a JSON object matching {output_contract.name}.",
            "Do not include prose outside JSON.",
            f"Schema: {output_contract.json_schema}",
        ]
    )
    return _section("output_contract", "Required output", content, structured=output_contract.model_dump(mode="json"))


def _section(
    kind: ContextSectionKind,
    title: str,
    content: str,
    *,
    structured: dict[str, Any] | None = None,
    source_refs: list[SourceRef] | None = None,
) -> ContextSection:
    return ContextSection(
        kind=kind,
        title=title,
        content=content or "No relevant context available.",
        structured=structured or {},
        source_refs=source_refs or [],
    )


def _source_ref(source_table: str, source_id: str) -> SourceRef:
    return SourceRef(source_table=source_table, source_id=source_id)
