"""Deterministic provider for local tests and offline development."""

from __future__ import annotations

import json
import re

from restscope.db.ids import new_id
from restscope.llm.providers.base import BaseLLMProvider
from restscope.llm.schemas import LLMRequest, LLMResponse, ToolCall


class FakeProvider(BaseLLMProvider):
    """Return stable fake responses without network access."""

    name = "fake"

    def invoke(self, request: LLMRequest) -> LLMResponse:
        if request.tools and request.tool_choice in {"auto", "required"}:
            first_tool = request.tools[0]
            return LLMResponse(
                provider=self.name,
                model=request.model,
                tool_calls=[
                    ToolCall(
                        id=new_id("call"),
                        name=first_tool.name,
                        arguments={},
                        provider=self.name,
                    )
                ],
                finish_reason="tool_calls",
            )

        payload = self._payload_for_schema(request.json_schema_name, request=request)
        return LLMResponse(
            provider=self.name,
            model=request.model,
            content=json.dumps(payload),
            parsed_json=payload,
            finish_reason="stop",
            metadata={"fake": True},
        )

    def _payload_for_schema(self, schema_name: str | None, *, request: LLMRequest) -> dict:
        if schema_name == "TestRequirementPlanDraft":
            prompt = "\n".join(message.content for message in request.messages)
            operation_ids = re.findall(r"Operation ID:\s*([^\s]+)", prompt)
            operation_id = operation_ids[0] if operation_ids else "op_fake"
            return {
                "requirements": [
                    {
                        "kind": "single_operation",
                        "title": "Validate documented operation behavior",
                        "priority": "medium",
                        "objective": "Validate the operation against its documented contract.",
                        "target": {"operation_id": operation_id},
                        "test_focus": ["request validation", "documented responses"],
                        "expected_behaviors": ["Responses conform to the documented contract."],
                        "rationale": "This deterministic requirement supports offline Planner tests.",
                        "evidence_refs": [f"operation:{operation_id}"],
                    }
                ]
            }
        if schema_name == "TestCampaignSpec":
            return {
                "campaign_type": "risk_targeted_fuzzing",
                "target_operation_ids": ["op_fake"],
                "hypothesis": "High-risk operations may expose validation failures.",
                "rationale": "FakeProvider returns a deterministic planner payload.",
                "schemathesis_config": {"checks": ["not_a_server_error"]},
                "expected_learning": ["operation failure behavior"],
                "stop_conditions": ["budget_exhausted"],
            }
        if schema_name == "AnalysisResult":
            return {
                "campaign_id": "camp_fake",
                "summary": "Fake campaign analysis.",
                "campaign_quality": "usable",
                "observations": [],
                "recommended_next_actions": ["continue_testing"],
            }
        if schema_name == "DecisionGateOutput":
            return {
                "next_action": "continue_testing",
                "rationale": "Fake decision keeps the loop moving.",
                "priority_operation_ids": ["op_fake"],
                "required_follow_up": [],
                "budget_assessment": "budget_available",
                "blockers": [],
            }
        return {"message": "fake response", "model": "fake-model"}
