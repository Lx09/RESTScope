"""Role policies for context construction."""

from __future__ import annotations

from pydantic import BaseModel

from .schemas import ContextRole, ContextSectionKind


class SectionPolicy(BaseModel):
    kind: ContextSectionKind
    required: bool
    max_tokens: int
    priority: int


class ContextPolicy(BaseModel):
    role: ContextRole
    prompt_version: str
    default_token_budget: int
    section_policies: list[SectionPolicy]
    output_contract_name: str


class ContextPolicyRegistry:
    def __init__(self, policies: list[ContextPolicy] | None = None) -> None:
        self._policies = {policy.role: policy for policy in (policies or default_policies())}

    def get(self, role: ContextRole, prompt_version: str | None = None) -> ContextPolicy:
        policy = self._policies[role]
        if prompt_version is None or prompt_version == policy.prompt_version:
            return policy
        return policy.model_copy(update={"prompt_version": prompt_version})


def default_policies() -> list[ContextPolicy]:
    return [
        ContextPolicy(
            role="planner",
            prompt_version="planner_v1",
            default_token_budget=6000,
            output_contract_name="TestCampaignSpec",
            section_policies=[
                _section("role_instruction", True, 400, 100),
                _section("task_state", True, 700, 95),
                _section("test_goal", True, 500, 90),
                _section("budget", True, 400, 90),
                _section("operation_targets", True, 1600, 85),
                _section("operation_risk_profile", True, 1200, 80),
                _section("historical_observations", True, 1200, 75),
                _section("campaign_history", True, 800, 60),
                _section("tool_affordances", True, 500, 70),
                _section("execution_assumptions", True, 500, 100),
                _section("output_contract", True, 700, 100),
            ],
        ),
        ContextPolicy(
            role="result_analyst",
            prompt_version="result_analyst_v1",
            default_token_budget=8000,
            output_contract_name="AnalysisResult",
            section_policies=[
                _section("role_instruction", True, 400, 100),
                _section("task_state", True, 600, 90),
                _section("current_campaign_result", True, 1200, 95),
                _section("operation_targets", True, 1400, 85),
                _section("operation_risk_profile", True, 1000, 75),
                _section("historical_observations", True, 1600, 80),
                _section("campaign_history", False, 800, 50),
                _section("recent_events", False, 600, 45),
                _section("output_contract", True, 800, 100),
            ],
        ),
        ContextPolicy(
            role="decision_maker",
            prompt_version="decision_maker_v1",
            default_token_budget=4000,
            output_contract_name="DecisionGateOutput",
            section_policies=[
                _section("role_instruction", True, 350, 100),
                _section("task_state", True, 600, 95),
                _section("budget", True, 400, 90),
                _section("operation_risk_profile", True, 900, 75),
                _section("historical_observations", True, 900, 80),
                _section("campaign_history", True, 700, 60),
                _section("recent_events", True, 600, 55),
                _section("output_contract", True, 650, 100),
            ],
        ),
    ]


def _section(
    kind: ContextSectionKind,
    required: bool,
    max_tokens: int,
    priority: int,
) -> SectionPolicy:
    return SectionPolicy(kind=kind, required=required, max_tokens=max_tokens, priority=priority)
