"""ContextBuilder orchestration."""

from __future__ import annotations

from restscope.db.ids import new_id
from restscope.memory import MemoryPackage, MemoryService

from .context_budget import ContextBudgetManager
from .context_policy import ContextPolicyRegistry
from .context_renderer import PromptRenderer
from .context_sections import build_sections
from .context_snapshot_service import ContextSnapshotService
from .schemas import ContextBuildRequest, ContextPackage, OutputContract


class ContextBuilder:
    def __init__(
        self,
        *,
        memory_service: MemoryService,
        snapshot_service: ContextSnapshotService,
        policy_registry: ContextPolicyRegistry | None = None,
        renderer: PromptRenderer | None = None,
        budget_manager: ContextBudgetManager | None = None,
    ) -> None:
        self.memory_service = memory_service
        self.snapshot_service = snapshot_service
        self.policy_registry = policy_registry or ContextPolicyRegistry()
        self.renderer = renderer or PromptRenderer()
        self.budget_manager = budget_manager or ContextBudgetManager()

    def build(self, request: ContextBuildRequest) -> ContextPackage:
        policy = self.policy_registry.get(request.role, request.prompt_version)
        token_budget = request.token_budget or policy.default_token_budget
        memory_package = self._retrieve_memory(request, token_budget)
        if request.role == "planner":
            memory_package = memory_package.model_copy(
                update={"planner_evidence": request.planner_evidence}
            )
        output_contract = build_output_contract(policy.output_contract_name)
        sections = build_sections(
            policy=policy,
            memory_package=memory_package,
            output_contract=output_contract,
        )
        sections = self.budget_manager.fit(sections, token_budget)
        source_refs = dict(memory_package.source_refs)
        for table, identifiers in request.planner_evidence.get("source_refs", {}).items():
            source_refs.setdefault(table, [])
            source_refs[table].extend(
                identifier for identifier in identifiers if identifier not in source_refs[table]
            )
        context = self.renderer.render(
            request=request,
            prompt_version=policy.prompt_version,
            sections=sections,
            output_contract=output_contract,
            source_refs=source_refs,
            cycle_index=_cycle_index(memory_package),
            token_budget=token_budget,
            context_id=new_id("context"),
        )
        snapshot = self.snapshot_service.persist(context)
        context.artifact_uri = snapshot.artifact_uri
        context.metadata["context_snapshot_id"] = snapshot.id
        return context

    def _retrieve_memory(self, request: ContextBuildRequest, token_budget: int) -> MemoryPackage:
        if request.role == "planner":
            return self.memory_service.retrieve_for_planner(
                task_id=request.task_id,
                schema_id=request.schema_id,
                token_budget=token_budget,
            )
        if request.role == "result_analyst":
            return self.memory_service.retrieve_for_result_analyst(
                task_id=request.task_id,
                schema_id=request.schema_id,
                campaign_id=request.campaign_id or "",
                operation_ids=request.operation_ids,
                token_budget=token_budget,
            )
        if request.role == "decision_maker":
            return self.memory_service.retrieve_for_decision_maker(
                task_id=request.task_id,
                schema_id=request.schema_id,
                token_budget=token_budget,
            )
        raise ValueError(f"Unsupported context role for MVP: {request.role}")


def build_output_contract(name: str) -> OutputContract:
    schemas = {
        "TestRequirementPlanDraft": {
            "type": "object",
            "required": ["requirements"],
            "properties": {
                "requirements": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": [
                            "kind",
                            "title",
                            "priority",
                            "objective",
                            "target",
                            "test_focus",
                            "expected_behaviors",
                            "rationale",
                            "evidence_refs",
                        ],
                    },
                }
            },
        },
        "TestCampaignSpec": {
            "type": "object",
            "required": ["campaign_type", "target_operation_ids", "hypothesis", "rationale"],
            "properties": {
                "campaign_type": {"type": "string"},
                "target_operation_ids": {"type": "array", "items": {"type": "string"}},
                "hypothesis": {"type": "string"},
                "rationale": {"type": "string"},
                "schemathesis_config": {"type": "object"},
                "expected_learning": {"type": "array", "items": {"type": "string"}},
                "stop_conditions": {"type": "array", "items": {"type": "string"}},
            },
        },
        "AnalysisResult": {
            "type": "object",
            "required": ["campaign_id", "summary", "campaign_quality", "observations"],
            "properties": {
                "campaign_id": {"type": "string"},
                "summary": {"type": "string"},
                "campaign_quality": {"type": "string"},
                "observations": {"type": "array"},
                "recommended_next_actions": {"type": "array", "items": {"type": "string"}},
            },
        },
        "DecisionGateOutput": {
            "type": "object",
            "required": ["next_action", "rationale", "budget_assessment"],
            "properties": {
                "next_action": {"type": "string"},
                "rationale": {"type": "string"},
                "priority_operation_ids": {"type": "array", "items": {"type": "string"}},
                "required_follow_up": {"type": "array", "items": {"type": "string"}},
                "budget_assessment": {"type": "string"},
                "blockers": {"type": "array", "items": {"type": "string"}},
            },
        },
    }
    return OutputContract(
        name=name,
        description=f"Return JSON matching {name}.",
        json_schema=schemas[name],
        validation_hint="Output only JSON. Do not include prose outside the JSON object.",
    )


def _cycle_index(memory_package: MemoryPackage) -> int:
    if not memory_package.working_memory:
        return 0
    return int(memory_package.working_memory[0].structured.get("cycle_index", 0))
