"""Protect the bounded FAST contract for operation result-state selection.

The API Behavior Monitor owns this vocabulary. These tests keep raw response
content out of the prompt and make existing state reuse unambiguous before the
Harness starts the registered System Agent.
"""

from __future__ import annotations


def test_state_prompt_contains_only_operation_resource_and_existing_names() -> None:
    """Untrusted response values can never become state-selection instructions."""

    from restscope.api_behavior_monitor.resource_state import build_state_prompt

    prompt = build_state_prompt(
        method="POST",
        path="/users",
        resource_name="users",
        existing_states=("active", "pending_review"),
    )

    assert "POST" in prompt.user
    assert "/users" in prompt.user
    assert "users" in prompt.user
    assert "active" in prompt.user
    assert "pending_review" in prompt.user
    assert "response" not in prompt.user.casefold()
    assert prompt.existing_states == ("active", "pending_review")


def test_state_contract_reuses_aliases_and_rejects_duplicate_new_names() -> None:
    """An established name must be selected as an alias, not recreated."""

    from restscope.agent import SystemAgentTask
    from restscope.api_behavior_monitor.resource_state import (
        ResourceStateDecision,
        resource_state_output_schema,
        validate_resource_state_output,
    )

    task = SystemAgentTask(
        objective="Choose the operation result state.",
        allowed_result_aliases=("active", "pending_review"),
    )
    schema = resource_state_output_schema(task)

    assert schema["properties"]["existing_state"]["anyOf"][0]["enum"] == [
        "active",
        "pending_review",
    ]
    assert validate_resource_state_output(
        ResourceStateDecision(existing_state="active"),
        task,
    ) == ()
    assert validate_resource_state_output(
        ResourceStateDecision(new_state="active"),
        task,
    ) == ("New state duplicates an existing state; reuse its alias: active.",)


def test_state_contract_rejects_unstable_or_ambiguous_names() -> None:
    """The structured result permits exactly one short snake-case state name."""

    import pytest
    from pydantic import ValidationError

    from restscope.api_behavior_monitor.resource_state import ResourceStateDecision

    with pytest.raises(ValidationError):
        ResourceStateDecision()
    with pytest.raises(ValidationError):
        ResourceStateDecision(existing_state="active", new_state="created")
    with pytest.raises(ValidationError):
        ResourceStateDecision(new_state="Needs Review")
