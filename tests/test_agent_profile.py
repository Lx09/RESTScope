"""Protect explicit Agent access instead of role-driven hidden injection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_agent_profile_rejects_duplicate_access_names() -> None:
    """Scenario: repeated permissions are a construction error, not ambiguity."""
    from restscope.agent import AgentProfile

    with pytest.raises(ValidationError):
        AgentProfile(
            name="failure_resolution",
            model_config_name="thinking",
            tool_names=["openapi.list_inputs", "openapi.list_inputs"],
        )


def test_agent_profile_keeps_each_access_kind_explicit() -> None:
    """Scenario: callers can inspect exactly what one independent Agent sees."""
    from restscope.agent import AgentProfile

    profile = AgentProfile(
        name="failure_resolution",
        model_config_name="thinking",
        tool_names=["openapi.list_inputs"],
        skill_names=["failure_diagnosis"],
        context_sources=["failure_sources"],
    )

    assert profile.tool_names == ("openapi.list_inputs",)
    assert profile.skill_names == ("failure_diagnosis",)
    assert profile.context_sources == ("failure_sources",)


def test_agent_profile_accepts_only_a_bounded_optional_description() -> None:
    """A Profile description is metadata, while blank or oversized text fails."""
    from restscope.agent import AgentProfile

    profile = AgentProfile(
        name="research",
        description="Investigate one bounded question for the parent Agent.",
        model_config_name="thinking",
    )

    assert profile.description.startswith("Investigate")
    with pytest.raises(ValidationError):
        AgentProfile(name="blank", description="", model_config_name="thinking")
    with pytest.raises(ValidationError):
        AgentProfile(
            name="large",
            description="X" * 2_001,
            model_config_name="thinking",
        )


def test_agent_profile_accepts_only_bounded_nonblank_instructions() -> None:
    """Profile guidance is complete trusted text, never blank or oversized."""
    from restscope.agent import AgentProfile

    profile = AgentProfile(
        name="main",
        model_config_name="thinking",
        instructions="Own semantic testing decisions.",
    )

    assert profile.instructions == "Own semantic testing decisions."
    with pytest.raises(ValidationError):
        AgentProfile(
            name="blank",
            model_config_name="thinking",
            instructions="   \n",
        )
    with pytest.raises(ValidationError):
        AgentProfile(
            name="large",
            model_config_name="thinking",
            instructions="X" * 12_001,
        )


def test_harness_exposes_atomic_start_instead_of_shallow_profile_resolution() -> None:
    """Callers cannot resolve grants and then assemble an Agent themselves."""
    from restscope import harness

    runtime = harness.build_harness()

    assert not hasattr(harness, "ResolvedAgentAccess")
    assert not hasattr(runtime, "validate_profile")
