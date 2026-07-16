"""Database-only Planner Agent for versioned test-requirement plans."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from restscope.context import ContextBuildRequest, ContextBuilder
from restscope.context.context_snapshot_service import ContextSnapshotService, LocalContextArtifactStore
from restscope.db import UnitOfWork, create_engine_from_config
from restscope.db.exceptions import NotFoundError
from restscope.db.ids import new_artifact_id, new_id
from restscope.db.session import make_session_factory
from restscope.db.time import utc_now
from restscope.llm import (
    LLMClient,
    LLMMessage,
    LLMRequestFactory,
    ModelSelector,
    OutputValidator,
    build_llm_client,
)
from restscope.memory import MemoryService
from restscope.restscope_config import RESTScopeConfig

from .schemas import (
    PlannerRequest,
    PlannerResult,
    SingleOperationTarget,
    TestRequirement,
    TestRequirementPlan,
    TestRequirementPlanDraft,
    WorkflowTarget,
)


PLAN_ARTIFACT_TYPE = "test_requirement_plan"


class PlannerError(RuntimeError):
    """Stable Planner failure with a machine-readable code."""

    def __init__(self, code: str, message: str, *, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or []


class LocalPlanArtifactStore:
    """Immutable local JSON storage backing plan Artifact records."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def write(self, plan: TestRequirementPlan) -> tuple[str, str, int]:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = plan.model_dump_json(indent=2)
        path = self.root / f"{plan.plan_id}.json"
        if path.exists():
            raise PlannerError("plan_artifact_conflict", f"Plan artifact already exists: {plan.plan_id}")
        path.write_text(payload, encoding="utf-8")
        encoded = payload.encode("utf-8")
        return path.resolve().as_uri(), hashlib.sha256(encoded).hexdigest(), len(encoded)

    def read(self, artifact_uri: str) -> TestRequirementPlan:
        if not artifact_uri.startswith("file://"):
            raise PlannerError("prior_plan_unavailable", "Only local plan artifacts are supported in v1.")
        try:
            payload = Path(artifact_uri.removeprefix("file://")).read_text(encoding="utf-8")
            return TestRequirementPlan.model_validate_json(payload)
        except (OSError, ValueError) as exc:
            raise PlannerError("prior_plan_unavailable", str(exc)) from exc


class PlannerAgent:
    """Generate complete requirement-plan revisions from persisted evidence."""

    def __init__(
        self,
        *,
        schema_id: str,
        session_factory: sessionmaker[Session],
        llm_client: LLMClient,
        model_selector: ModelSelector,
        context_artifact_root: Path,
        plan_artifact_store: LocalPlanArtifactStore,
    ) -> None:
        self.schema_id = schema_id
        self.session_factory = session_factory
        self.llm_client = llm_client
        self.model_selector = model_selector
        self.context_artifact_root = context_artifact_root
        self.plan_artifact_store = plan_artifact_store
        self.request_factory = LLMRequestFactory()
        self.output_validator = OutputValidator()

    def plan(self, request: PlannerRequest) -> PlannerResult:
        evidence = self._load_evidence(request.task_id)
        context = self._build_context(request.task_id, evidence)
        evidence["allowed_evidence_refs"] = _allowed_evidence_refs(
            context.source_refs,
            prior_plan=evidence["prior_plan"],
        )
        model_config = self.model_selector.select("planner")
        llm_request = self.request_factory.from_context(
            context_package=context,
            model_config=model_config,
            output_model=TestRequirementPlanDraft,
            tools=[],
            tool_choice="none",
        )

        response = self.llm_client.invoke(llm_request)
        draft, issues = self._validate_response(response, context, evidence)
        if draft is None:
            repair_request = llm_request.model_copy(
                update={
                    "messages": [
                        *llm_request.messages,
                        LLMMessage(
                            role="assistant",
                            content=json.dumps(
                                response.parsed_json if response.parsed_json is not None else response.content,
                                ensure_ascii=False,
                            ),
                        ),
                        LLMMessage(
                            role="user",
                            content=(
                                "Repair the JSON so it matches the required contract and database evidence. "
                                "Return only the complete corrected JSON. Validation errors:\n- "
                                + "\n- ".join(issues)
                            ),
                        ),
                    ],
                    "tools": [],
                    "tool_choice": "none",
                }
            )
            repaired = self.llm_client.invoke(repair_request)
            draft, issues = self._validate_response(repaired, context, evidence)

        if draft is None:
            raise PlannerError(
                "planner_output_invalid",
                "Planner output remained invalid after one repair attempt.",
                details=issues,
            )

        plan = self._materialize_plan(request.task_id, draft, evidence["prior_plan"])
        artifact_id = self._persist_plan(plan)
        return PlannerResult(
            plan=plan,
            artifact_id=artifact_id,
            context_snapshot_id=context.metadata["context_snapshot_id"],
        )

    def _load_evidence(self, task_id: str) -> dict[str, Any]:
        with UnitOfWork(self.session_factory) as uow:
            try:
                task = uow.tasks.require(task_id)
            except NotFoundError as exc:
                raise PlannerError("task_not_found", str(exc)) from exc
            if task.schema_id != self.schema_id:
                raise PlannerError(
                    "schema_mismatch",
                    f"Task {task_id} belongs to schema {task.schema_id}, not {self.schema_id}.",
                )

            operations = uow.operations.list_by_schema(self.schema_id)
            edges = uow.operation_edges.list_by_schema(self.schema_id)
            observations = uow.observations.list_by_schema_status(self.schema_id, [], limit=500)
            prior_artifact = uow.artifacts.get_latest_by_task_and_type(task_id, PLAN_ARTIFACT_TYPE)

        prior_plan = self.plan_artifact_store.read(prior_artifact.artifact_uri) if prior_artifact else None
        operation_ids = {operation.id for operation in operations}
        return {
            "operations": operations,
            "operation_ids": operation_ids,
            "operation_edges": edges,
            "observations": observations,
            "prior_artifact": prior_artifact,
            "prior_plan": prior_plan,
            "allowed_evidence_refs": set(),
        }

    def _build_context(self, task_id: str, evidence: dict[str, Any]):
        with UnitOfWork(self.session_factory) as uow:
            snapshot_service = ContextSnapshotService(
                artifact_store=LocalContextArtifactStore(self.context_artifact_root),
                artifact_repo=uow.artifacts,
                context_snapshot_repo=uow.context_snapshots,
                event_log_repo=uow.events,
            )
            builder = ContextBuilder(
                memory_service=MemoryService.from_unit_of_work(uow),
                snapshot_service=snapshot_service,
            )
            model = self.model_selector.select("planner")
            planner_evidence = {
                "operation_catalog": [
                    {
                        "id": operation.id,
                        "operation_id": operation.operation_id,
                        "method": operation.method,
                        "path": operation.path,
                        "summary": operation.summary,
                        "card_json": operation.card_json,
                    }
                    for operation in evidence["operations"]
                ],
                "operation_edges": [
                    {
                        "id": edge.id,
                        "source_operation_id": edge.source_operation_id,
                        "target_operation_id": edge.target_operation_id,
                        "edge_type": edge.edge_type,
                        "value": edge.value,
                        "confidence": edge.confidence,
                        "status": edge.status,
                        "reason": edge.reason,
                    }
                    for edge in evidence["operation_edges"]
                ],
                "prior_plan": (
                    evidence["prior_plan"].model_dump(mode="json")
                    if evidence["prior_plan"] is not None
                    else None
                ),
                "source_refs": {
                    "operations": [operation.id for operation in evidence["operations"]],
                    "operation_edges": [edge.id for edge in evidence["operation_edges"]],
                    "artifacts": (
                        [evidence["prior_artifact"].id] if evidence["prior_artifact"] else []
                    ),
                },
            }
            context = builder.build(
                ContextBuildRequest(
                    task_id=task_id,
                    schema_id=self.schema_id,
                    role="planner",
                    model_name=model.model,
                    planner_evidence=planner_evidence,
                )
            )
            uow.commit()
            return context

    def _validate_response(self, response, context, evidence):
        result = self.output_validator.validate(
            response=response,
            output_model=TestRequirementPlanDraft,
            context_package=context,
        )
        if not result.valid:
            return None, [
                f"{issue.location or '$'}: {issue.message}" for issue in result.errors
            ]
        draft: TestRequirementPlanDraft = result.validated_object
        issues = _semantic_issues(
            draft,
            operation_ids=evidence["operation_ids"],
            allowed_evidence_refs=evidence["allowed_evidence_refs"],
        )
        return (draft, []) if not issues else (None, issues)

    def _materialize_plan(
        self,
        task_id: str,
        draft: TestRequirementPlanDraft,
        prior_plan: TestRequirementPlan | None,
    ) -> TestRequirementPlan:
        return TestRequirementPlan(
            plan_id=new_id("plan"),
            task_id=task_id,
            schema_id=self.schema_id,
            revision=(prior_plan.revision + 1) if prior_plan else 1,
            previous_plan_id=prior_plan.plan_id if prior_plan else None,
            generated_at=utc_now(),
            requirements=[
                TestRequirement(
                    requirement_id=new_id("req"),
                    **requirement.model_dump(),
                )
                for requirement in draft.requirements
            ],
        )

    def _persist_plan(self, plan: TestRequirementPlan) -> str:
        artifact_uri, content_hash, size_bytes = self.plan_artifact_store.write(plan)
        artifact_id = new_artifact_id()
        with UnitOfWork(self.session_factory) as uow:
            uow.artifacts.add(
                id=artifact_id,
                task_id=plan.task_id,
                artifact_type=PLAN_ARTIFACT_TYPE,
                artifact_uri=artifact_uri,
                content_hash=content_hash,
                size_bytes=size_bytes,
                metadata_json={
                    "plan_id": plan.plan_id,
                    "schema_id": plan.schema_id,
                    "revision": plan.revision,
                    "previous_plan_id": plan.previous_plan_id,
                },
            )
            uow.events.append(
                task_id=plan.task_id,
                event_type="test_requirement_plan_generated",
                actor="planner",
                payload_json={
                    "plan_id": plan.plan_id,
                    "artifact_id": artifact_id,
                    "schema_id": plan.schema_id,
                    "revision": plan.revision,
                    "previous_plan_id": plan.previous_plan_id,
                    "requirement_count": len(plan.requirements),
                },
            )
            uow.commit()
        return artifact_id


def build_planner_agent(
    config: RESTScopeConfig,
    schema_id: str,
    *,
    llm_client: LLMClient | None = None,
) -> PlannerAgent:
    """Build a Planner bound to one already initialized database catalog."""

    engine = create_engine_from_config(config.db)
    session_factory = make_session_factory(engine)
    with UnitOfWork(session_factory) as uow:
        schema = uow.schemas.get(schema_id)
        if schema is None or schema.catalog_status != "ready":
            raise PlannerError(
                "catalog_not_ready",
                f"Schema {schema_id} is not an initialized OpenAPI catalog.",
            )

    return PlannerAgent(
        schema_id=schema_id,
        session_factory=session_factory,
        llm_client=llm_client or build_llm_client(config.llm),
        model_selector=ModelSelector.from_config(config.llm),
        context_artifact_root=config.paths.data_dir / "artifacts" / "contexts",
        plan_artifact_store=LocalPlanArtifactStore(
            config.paths.data_dir / "artifacts" / "plans"
        ),
    )


def _semantic_issues(
    draft: TestRequirementPlanDraft,
    *,
    operation_ids: set[str],
    allowed_evidence_refs: set[str],
) -> list[str]:
    issues: list[str] = []
    for index, requirement in enumerate(draft.requirements):
        if isinstance(requirement.target, SingleOperationTarget):
            target_ids = [requirement.target.operation_id]
        elif isinstance(requirement.target, WorkflowTarget):
            target_ids = [step.operation_id for step in requirement.target.steps]
        else:
            target_ids = []
        for operation_id in target_ids:
            if operation_id not in operation_ids:
                issues.append(
                    f"requirements.{index}.target references unknown operation {operation_id}"
                )
        for evidence_ref in requirement.evidence_refs:
            if evidence_ref not in allowed_evidence_refs:
                issues.append(
                    f"requirements.{index}.evidence_refs contains unavailable reference {evidence_ref}"
                )
    return issues


def _allowed_evidence_refs(
    source_refs: dict[str, list[str]],
    *,
    prior_plan: TestRequirementPlan | None,
) -> set[str]:
    allowed = {
        f"operation:{operation_id}"
        for table in ("operations", "operation_intelligence")
        for operation_id in source_refs.get(table, [])
    }
    allowed.update(
        f"observation:{observation_id}"
        for observation_id in source_refs.get("test_observations", [])
    )
    if prior_plan is not None:
        allowed.add(f"plan:{prior_plan.plan_id}")
    return allowed
