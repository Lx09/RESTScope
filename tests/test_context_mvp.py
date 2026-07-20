from __future__ import annotations


def test_context_budget_keeps_required_sections_and_high_priority_optional_content() -> None:
    from restscope.context import ContextBudgetManager, ContextSection

    required = ContextSection(
        kind="role_instruction",
        title="Role",
        content="required content",
        required=True,
        priority=100,
        estimated_tokens=2,
    )
    high = ContextSection(
        kind="operation_targets",
        title="High",
        content="important optional content",
        priority=90,
        estimated_tokens=3,
    )
    low = ContextSection(
        kind="recent_events",
        title="Low",
        content="less important optional content",
        priority=10,
        estimated_tokens=4,
    )

    fitted = ContextBudgetManager().fit([low, required, high], token_budget=5)

    assert [section.title for section in fitted] == ["Role", "High"]


def test_prompt_renderer_builds_context_without_persistence_dependency() -> None:
    from restscope.context import (
        ContextBuildRequest,
        ContextSection,
        OutputContract,
        PromptRenderer,
    )

    request = ContextBuildRequest(
        task_id="task_1",
        schema_id="schema_1",
        role="planner",
        model_name="fake-model",
    )
    section = ContextSection(
        kind="role_instruction",
        title="Role",
        content="Plan from supplied evidence.",
        required=True,
        estimated_tokens=4,
    )
    contract = OutputContract(
        name="Plan",
        description="A plan",
        json_schema={"type": "object"},
    )

    context = PromptRenderer().render(
        request=request,
        prompt_version="planner_v1",
        sections=[section],
        output_contract=contract,
        source_refs={"schemas": ["schema_1"]},
        cycle_index=0,
        token_budget=100,
        context_id="context_1",
    )

    assert context.messages[1].content.endswith("Plan from supplied evidence.")
    assert context.source_refs == {"schemas": ["schema_1"]}
