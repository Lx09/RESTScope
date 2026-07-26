from __future__ import annotations

from pathlib import Path
from typing import Any


def test_request_rejects_removed_successful_operation_keys() -> None:
    from pydantic import ValidationError

    from restscope.agent.operation_smoke import OperationSmokeRequest

    try:
        OperationSmokeRequest(
            operation_key="GET /items/{itemId}",
            successful_operation_keys=["POST /items"],
        )
    except ValidationError as exc:
        assert exc.errors()[0]["type"] == "extra_forbidden"
        assert exc.errors()[0]["loc"] == ("successful_operation_keys",)
    else:
        raise AssertionError("removed successful_operation_keys field was accepted")


def _catalog(tmp_path: Path):
    from restscope.db import (
        Base,
        SqlAlchemyGeneratorConfigUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import GeneratorConfigCatalog

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Smoke", "version": "1"},
            "paths": {
                "/items/{itemId}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "itemId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "region",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'smoke.sqlite'}")
    Base.metadata.create_all(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(
            make_session_factory(engine)
        )
    )
    assert catalog.initialize_once(ir) is True
    return catalog, "GET /items/{itemId}"


def _report(
    *,
    operation_key: str,
    revision: int,
    run_number: int,
    passed: int,
    failed: int,
):
    from restscope.testing import OperationExecutionReport

    counts: dict[str, int] = {}
    if passed:
        counts["200"] = passed
    if failed:
        counts["400"] = failed
    return OperationExecutionReport(
        run_id=f"run_{run_number}",
        operation_key=operation_key,
        seed=run_number,
        config_revision=revision,
        status="completed",
        cases=[],
        status_code_counts=counts,
        error_count=0,
        observed_2xx=passed > 0,
    )


class _BatchRunner:
    def __init__(self, catalog, outcomes: list[tuple[int, int]]) -> None:
        self.catalog = catalog
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def run_operation(
        self,
        context,
        /,
        *,
        operation_key: str,
        case_count: int,
        seed: int | None = None,
    ):
        del context
        config = self.catalog.inspect_operation(operation_key)
        revision = config.revision
        passed, failed = self.outcomes.pop(0)
        self.calls.append(
            {
                "operation_key": operation_key,
                "case_count": case_count,
                "seed": seed,
                "revision": revision,
                "configs": [
                    item.model_dump(mode="json") for item in config.configs
                ],
            }
        )
        return _report(
            operation_key=operation_key,
            revision=revision,
            run_number=len(self.calls),
            passed=passed,
            failed=failed,
        )


class _Diagnoser:
    def __init__(self, results, validations=None) -> None:
        self.results = list(results)
        self.validations = list(validations or [])
        self.calls: list[dict[str, Any]] = []
        self.validation_calls: list[dict[str, Any]] = []

    def diagnose(
        self,
        *,
        report,
        config,
        reference_option_provider,
        **kwargs,
    ):
        self.calls.append(
            {
                "report": report,
                "config": config,
                "reference_option_provider": reference_option_provider,
                **kwargs,
            }
        )
        return self.results.pop(0)

    def validate_patch(self, **kwargs):
        self.validation_calls.append(kwargs)
        result = self.validations.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _ReferenceValues:
    def __init__(self, values: dict[tuple[str, str], list[Any]] | None = None):
        self.values = values or {}

    def values_for(self, strategy):
        name = (
            strategy.resource
            if strategy.type == "resource_identifier"
            else strategy.value_name
        )
        return list(self.values.get((strategy.type, name), []))

    def available_options(self, *, ir, config, input_node_ids):
        del ir, config, input_node_ids
        return []

    def prepare_updates(
        self,
        *,
        ir,
        config,
        updates,
        selected_reference_options,
    ):
        del ir, config, selected_reference_options
        for update in updates:
            strategy = update.strategy
            if strategy is not None and strategy.type in {
                "resource_identifier",
                "response_value",
            }:
                if not self.values_for(strategy):
                    raise RuntimeError("selected reference pool is empty")
        return updates


def _diagnosis(
    *,
    node_id: str,
    strategy: dict[str, Any],
    item_id: str = "I1",
    semantic_input: str = "path.itemId",
    selected_reference_options=None,
):
    from restscope.agent.operation_smoke import PlanSolveDiagnosisResult
    from restscope.agent.operation_smoke.schemas import (
        GeneratorPatchDraft,
        PlanItemSummary,
    )

    return PlanSolveDiagnosisResult(
        status="patch_ready",
        termination_reason="model_finalize",
        patch=GeneratorPatchDraft(
            updates=[
                {
                    "input_node_id": node_id,
                    "strategy": strategy,
                }
            ],
            attributions=[
                {
                    "input_node_id": node_id,
                    "item_ids": [item_id],
                }
            ],
        ),
        selected_reference_options=selected_reference_options or [],
        ready_items=[
            PlanItemSummary(
                item_id=item_id,
                failure_refs=["F1"],
                cause="The generated input is rejected.",
                confidence=0.9,
                affected_inputs=[semantic_input],
                solution="Generate a value accepted by the target.",
                evidence_refs=["F1", "C1"],
            )
        ],
        covered_item_ids=[item_id],
    )


def _validation(*, node_id: str, status: str):
    from restscope.agent.operation_smoke import (
        PatchItemValidationSummary,
        PatchValidationSummary,
    )

    resolved = status == "resolved"
    item = PatchItemValidationSummary(
        item_id="I1",
        status=status,
        current_failure_refs=["F1"] if status == "persisting" else [],
        reason=f"Validation classified the item as {status}.",
        confidence=0.9,
    )
    return PatchValidationSummary(
        items=[item],
        accepted_item_ids=["I1"] if resolved else [],
        accepted_input_node_ids=[node_id] if resolved else [],
        rejected_input_node_ids=[] if resolved else [node_id],
    )


def test_candidate_is_validated_only_by_the_next_batch_and_then_accepted(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import (
        OperationSmokeAgent,
        OperationSmokeRequest,
    )

    catalog, operation_key = _catalog(tmp_path)
    node_id = catalog.inspect_operation(operation_key).configs[0].input_node_id
    runner = _BatchRunner(catalog, [(0, 10), (9, 1)])
    diagnoser = _Diagnoser(
        [
            _diagnosis(
                node_id=node_id,
                strategy={"type": "constant", "value": "known"},
            )
        ],
        validations=[_validation(node_id=node_id, status="persisting")],
    )
    agent = OperationSmokeAgent(
        config_catalog=catalog,
        batch_runner=runner,
        diagnoser=diagnoser,
        reference_values=_ReferenceValues(),
    )

    result = agent.run(
        object(),
        OperationSmokeRequest(
            operation_key=operation_key,
            case_count=10,
            success_rate_threshold=0.8,
            max_feedback_rounds=1,
        ),
    )

    assert result.status == "passed"
    assert result.success_rate == 0.9
    assert [call["revision"] for call in runner.calls] == [1, 2]
    assert len(diagnoser.calls) == 1
    assert len(diagnoser.validation_calls) == 1
    assert result.diagnoses[0].patch_validation.items[0].status == "persisting"
    assert [
        item.lifecycle for item in catalog.list_revisions(operation_key)
    ] == ["accepted", "accepted"]
    assert catalog.list_revisions(operation_key)[1].evaluation[
        "run_id"
    ] == "run_2"


def test_agent_passes_private_smoke_evidence_and_lowered_budgets(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import (
        OperationSmokeAgent,
        OperationSmokeRequest,
        PlanSolveDiagnosisResult,
    )
    from restscope.testing.execution import (
        SmokeCaseExecutionEvidence,
        SmokeExecutionOutcome,
    )

    catalog, operation_key = _catalog(tmp_path)

    class SmokeRunner(_BatchRunner):
        def run_operation_for_smoke(self, context, /, **arguments):
            report = self.run_operation(context, **arguments)
            return SmokeExecutionOutcome(
                report=report,
                case_evidence=(
                    SmokeCaseExecutionEvidence(
                        case_id="private_case",
                        response_body=b'{"detail":"private failure"}',
                    ),
                ),
            )

    runner = SmokeRunner(catalog, [(0, 1)])
    diagnoser = _Diagnoser(
        [
            PlanSolveDiagnosisResult(
                status="no_parameter_issue",
                termination_reason="model_finalize",
                non_parameter_failures=["F1"],
                planning_outputs=1,
            )
        ]
    )
    agent = OperationSmokeAgent(
        config_catalog=catalog,
        batch_runner=runner,
        diagnoser=diagnoser,
        reference_values=_ReferenceValues(),
    )

    result = agent.run(
        object(),
        OperationSmokeRequest(
            operation_key=operation_key,
            max_planning_outputs=3,
            max_http_tool_rounds=2,
        ),
    )

    assert result.failure_kind == "no_parameter_issue"
    call = diagnoser.calls[0]
    evidence = call["private_case_evidence"]["private_case"]
    assert evidence.response_body == b'{"detail":"private failure"}'
    assert call["max_planning_outputs"] == 3
    assert call["max_http_tool_rounds"] == 2


def test_failed_candidate_is_rejected_and_compensated_without_case_probe(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import (
        OperationSmokeAgent,
        OperationSmokeRequest,
    )

    catalog, operation_key = _catalog(tmp_path)
    baseline = catalog.inspect_operation(operation_key)
    node_id = baseline.configs[0].input_node_id
    runner = _BatchRunner(catalog, [(0, 10), (2, 8)])
    agent = OperationSmokeAgent(
        config_catalog=catalog,
        batch_runner=runner,
        diagnoser=_Diagnoser(
            [
                _diagnosis(
                    node_id=node_id,
                    strategy={"type": "constant", "value": "still-bad"},
                )
            ],
            validations=[_validation(node_id=node_id, status="unknown")],
        ),
        reference_values=_ReferenceValues(),
    )

    result = agent.run(
        object(),
        OperationSmokeRequest(
            operation_key=operation_key,
            max_feedback_rounds=1,
        ),
    )

    assert result.status == "retry"
    assert result.failure_kind == "threshold_exhausted"
    assert len(runner.calls) == 2
    assert [
        item.lifecycle for item in catalog.list_revisions(operation_key)
    ] == ["accepted", "rejected", "accepted"]
    restored = catalog.inspect_operation(operation_key)
    assert restored.configs[0].strategy == baseline.configs[0].strategy


def test_validated_patch_accumulates_into_the_next_candidate(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import (
        OperationSmokeAgent,
        OperationSmokeRequest,
    )

    catalog, operation_key = _catalog(tmp_path)
    baseline = catalog.inspect_operation(operation_key)
    path_node_id = baseline.configs[0].input_node_id
    query_node_id = baseline.configs[1].input_node_id
    first = _diagnosis(
        node_id=path_node_id,
        strategy={"type": "constant", "value": "known-item"},
    )
    second = _diagnosis(
        node_id=query_node_id,
        strategy={"type": "constant", "value": "known-region"},
        semantic_input="query.region",
    )
    diagnoser = _Diagnoser(
        [first, second],
        validations=[
            _validation(node_id=path_node_id, status="resolved"),
            _validation(node_id=query_node_id, status="unknown"),
        ],
    )
    runner = _BatchRunner(catalog, [(0, 10), (0, 10), (0, 10)])
    agent = OperationSmokeAgent(
        config_catalog=catalog,
        batch_runner=runner,
        diagnoser=diagnoser,
        reference_values=_ReferenceValues(),
    )

    result = agent.run(
        object(),
        OperationSmokeRequest(
            operation_key=operation_key,
            max_feedback_rounds=2,
        ),
    )

    assert result.status == "retry"
    assert result.failure_kind == "threshold_exhausted"
    assert [call["revision"] for call in runner.calls] == [1, 2, 3]
    second_candidate_configs = {
        item["input_node_id"]: item for item in runner.calls[2]["configs"]
    }
    assert second_candidate_configs[path_node_id]["strategy"] == {
        "type": "constant",
        "value": "known-item",
    }
    assert second_candidate_configs[query_node_id]["strategy"] == {
        "type": "constant",
        "value": "known-region",
    }
    active = catalog.inspect_operation(operation_key)
    assert active.revision == 4
    active_by_id = {item.input_node_id: item for item in active.configs}
    assert active_by_id[path_node_id].strategy.value == "known-item"
    assert active_by_id[query_node_id].strategy == baseline.configs[1].strategy
    assert [
        item.lifecycle for item in catalog.list_revisions(operation_key)
    ] == ["accepted", "accepted", "rejected", "accepted"]
    assert len(diagnoser.calls) == 2
    assert len(diagnoser.validation_calls) == 2
    previous_experiment = diagnoser.calls[1]["previous_experiment"]
    assert previous_experiment["accepted_change_count"] == 1
    assert previous_experiment["removed_change_count"] == 0
    assert "complete experimental patch" in previous_experiment["evidence_note"]


def test_patch_validation_provider_error_discards_only_current_candidate(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import (
        OperationSmokeAgent,
        OperationSmokeRequest,
    )

    catalog, operation_key = _catalog(tmp_path)
    baseline = catalog.inspect_operation(operation_key)
    node_id = baseline.configs[0].input_node_id
    diagnoser = _Diagnoser(
        [
            _diagnosis(
                node_id=node_id,
                strategy={"type": "constant", "value": "experimental"},
            )
        ],
        validations=[RuntimeError("validation provider unavailable")],
    )
    runner = _BatchRunner(catalog, [(0, 10), (0, 10)])
    agent = OperationSmokeAgent(
        config_catalog=catalog,
        batch_runner=runner,
        diagnoser=diagnoser,
        reference_values=_ReferenceValues(),
    )

    result = agent.run(
        object(),
        OperationSmokeRequest(
            operation_key=operation_key,
            max_feedback_rounds=1,
        ),
    )

    assert result.status == "errored"
    assert result.error["message"] == "validation provider unavailable"
    active = catalog.inspect_operation(operation_key)
    assert active.configs == baseline.configs
    assert [
        item.lifecycle for item in catalog.list_revisions(operation_key)
    ] == ["accepted", "rejected", "accepted"]


def test_joint_patch_keeps_only_changes_owned_by_resolved_items(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import (
        GeneratorPatchAttribution,
        GeneratorPatchDraft,
        OperationSmokeAgent,
        OperationSmokeRequest,
        PatchItemValidationSummary,
        PatchValidationSummary,
        PlanSolveDiagnosisResult,
    )
    from restscope.agent.operation_smoke.schemas import PlanItemSummary

    catalog, operation_key = _catalog(tmp_path)
    baseline = catalog.inspect_operation(operation_key)
    path_node_id = baseline.configs[0].input_node_id
    query_node_id = baseline.configs[1].input_node_id
    diagnosis = PlanSolveDiagnosisResult(
        status="patch_ready",
        termination_reason="model_finalize",
        patch=GeneratorPatchDraft(
            updates=[
                {
                    "input_node_id": path_node_id,
                    "strategy": {"type": "constant", "value": "known-item"},
                },
                {
                    "input_node_id": query_node_id,
                    "strategy": {
                        "type": "constant",
                        "value": "experimental-region",
                    },
                },
            ],
            attributions=[
                GeneratorPatchAttribution(
                    input_node_id=path_node_id,
                    item_ids=["I1"],
                ),
                GeneratorPatchAttribution(
                    input_node_id=query_node_id,
                    item_ids=["I2"],
                ),
            ],
        ),
        ready_items=[
            PlanItemSummary(
                item_id="I1",
                failure_refs=["F1"],
                cause="The item ID does not exist.",
                confidence=0.9,
                affected_inputs=["path.itemId"],
                solution="Use an existing item ID.",
                evidence_refs=["F1", "C1"],
            ),
            PlanItemSummary(
                item_id="I2",
                failure_refs=["F2"],
                cause="The region may be invalid.",
                confidence=0.7,
                affected_inputs=["query.region"],
                solution="Use a supported region.",
                evidence_refs=["F2", "C1"],
            ),
        ],
        covered_item_ids=["I1", "I2"],
    )
    validation = PatchValidationSummary(
        items=[
            PatchItemValidationSummary(
                item_id="I1",
                status="resolved",
                current_failure_refs=[],
                reason="The item lookup failure disappeared.",
                confidence=0.9,
            ),
            PatchItemValidationSummary(
                item_id="I2",
                status="persisting",
                current_failure_refs=["F1"],
                reason="The region failure remains.",
                confidence=0.8,
            ),
        ],
        accepted_item_ids=["I1"],
        accepted_input_node_ids=[path_node_id],
        rejected_input_node_ids=[query_node_id],
    )
    runner = _BatchRunner(catalog, [(0, 10), (0, 10)])
    agent = OperationSmokeAgent(
        config_catalog=catalog,
        batch_runner=runner,
        diagnoser=_Diagnoser([diagnosis], validations=[validation]),
        reference_values=_ReferenceValues(),
    )

    result = agent.run(
        object(),
        OperationSmokeRequest(
            operation_key=operation_key,
            max_feedback_rounds=1,
        ),
    )

    assert result.status == "retry"
    active = catalog.inspect_operation(operation_key)
    assert active.revision == 3
    active_by_id = {item.input_node_id: item for item in active.configs}
    assert active_by_id[path_node_id].strategy.value == "known-item"
    assert active_by_id[query_node_id].strategy == baseline.configs[1].strategy
    assert result.diagnoses[0].patch_validation == validation


def test_empty_reference_pool_is_an_operation_error_and_is_not_staged(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import (
        OperationSmokeAgent,
        OperationSmokeRequest,
    )

    catalog, operation_key = _catalog(tmp_path)
    node_id = catalog.inspect_operation(operation_key).configs[0].input_node_id
    runner = _BatchRunner(catalog, [(0, 10)])
    agent = OperationSmokeAgent(
        config_catalog=catalog,
        batch_runner=runner,
        diagnoser=_Diagnoser(
            [
                _diagnosis(
                    node_id=node_id,
                    strategy={
                        "type": "resource_identifier",
                        "resource": "item",
                    },
                )
            ]
        ),
        reference_values=_ReferenceValues(),
    )

    result = agent.run(
        object(),
        OperationSmokeRequest(operation_key=operation_key),
    )

    assert result.status == "errored"
    assert result.failure_kind == "operation_error"
    assert len(runner.calls) == 1
    assert [
        item.lifecycle for item in catalog.list_revisions(operation_key)
    ] == ["accepted"]


def test_disabled_request_structure_returns_unsupported_without_a_batch(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import (
        OperationSmokeAgent,
        OperationSmokeRequest,
    )
    from restscope.db import (
        Base,
        SqlAlchemyGeneratorConfigUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import GeneratorConfigCatalog

    engine = create_engine_from_url(
        f"sqlite:///{tmp_path / 'unsupported-smoke.sqlite'}"
    )
    Base.metadata.create_all(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(
            make_session_factory(engine)
        )
    )
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Unsupported", "version": "1"},
            "paths": {
                "/items": {
                    "post": {
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {
                                                "type": "string",
                                                "not": {"const": "forbidden"},
                                            }
                                        },
                                    }
                                }
                            },
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    assert catalog.initialize_once(ir) is True
    runner = _BatchRunner(catalog, [])
    smoke = OperationSmokeAgent(
        config_catalog=catalog,
        batch_runner=runner,
        diagnoser=_Diagnoser([]),
        reference_values=_ReferenceValues(),
    )

    result = smoke.run(
        object(),
        OperationSmokeRequest(operation_key="POST /items"),
    )

    assert result.status == "unsupported"
    assert result.failure_kind == "unsupported_operation"
    assert runner.calls == []


def test_response_value_patch_is_registered_and_uses_system_value_name(
    tmp_path: Path,
) -> None:
    from types import SimpleNamespace

    from restscope.agent.operation_smoke import (
        BehaviorMonitorReferenceValues,
        OperationSmokeAgent,
        OperationSmokeRequest,
    )

    class BehaviorAgent:
        def __init__(self) -> None:
            self.registrations = []
            self.values = ["item-123"]

        def register_response_value(self, **kwargs):
            raise AssertionError(f"unexpected unselected registration: {kwargs}")

        def register_response_value_sources(self, **kwargs):
            self.registrations.append(kwargs)
            return SimpleNamespace(value_name="response_system_assigned")

        def response_values_for(self, value_name):
            assert value_name == "response_system_assigned"
            return list(self.values)

        def lookup(self, request):
            raise AssertionError(f"unexpected resource lookup: {request}")

        def available_reference_options(self, **kwargs):
            del kwargs
            return []

    catalog, operation_key = _catalog(tmp_path)
    config = catalog.inspect_operation(operation_key)
    node_id = config.configs[0].input_node_id
    from restscope.agent.operation_smoke import AvailableReferenceOption

    selected_option = AvailableReferenceOption(
        option_id="ref_selected",
        input_node_id=node_id,
        kind="response_value",
        value_name="response_system_assigned",
        compatible_scalar_type="string",
        value_count=1,
        producer_operation_keys=["GET /items"],
        producer_status_code="200",
        producer_media_type="application/json",
        source_field="item_id",
        source_selector="$.items[].item_id",
    )
    behavior_agent = BehaviorAgent()
    runner = _BatchRunner(catalog, [(0, 10), (10, 0)])
    smoke = OperationSmokeAgent(
        config_catalog=catalog,
        batch_runner=runner,
        diagnoser=_Diagnoser(
            [
                _diagnosis(
                    node_id=node_id,
                    strategy={
                        "type": "response_value",
                        "value_name": "response_system_assigned",
                    },
                    selected_reference_options=[selected_option],
                )
            ],
            validations=[_validation(node_id=node_id, status="resolved")],
        ),
        reference_values=BehaviorMonitorReferenceValues(behavior_agent),
    )
    ir = object()

    result = smoke.run(
        SimpleNamespace(ir=ir),
        OperationSmokeRequest(operation_key=operation_key),
    )

    assert result.status == "passed"
    assert len(behavior_agent.registrations) == 1
    registration_call = behavior_agent.registrations[0]
    assert {
        key: value
        for key, value in registration_call.items()
        if key != "sources"
    } == {
        "consumer_operation_key": operation_key,
        "consumer_input_node_id": node_id,
        "parameter_name": "itemId",
        "expected_type": None,
    }
    source = registration_call["sources"][0]
    assert (
        source.producer_operation_key,
        source.status_code,
        source.media_type,
        source.selector,
        source.field_name,
    ) == (
        "GET /items",
        "200",
        "application/json",
        "$.items[].item_id",
        "item_id",
    )
    accepted = catalog.inspect_operation(operation_key)
    assert accepted.configs[0].strategy.model_dump(mode="json") == {
        "type": "response_value",
        "value_name": "response_system_assigned",
    }
    assert len(runner.calls) == 2


def test_available_reference_options_exclude_empty_pools_and_actual_values(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from restscope.agent.api_behavior_monitor import (
        ResourceIdentifierSummary,
        ResourceLookupResult,
        ResourceNameSummary,
        ResponseValueSource,
        ResponseValueSourceOption,
    )
    from restscope.agent.operation_smoke import BehaviorMonitorReferenceValues

    class BehaviorAgent:
        def __init__(self) -> None:
            self.source_requests = []
            self.catalog = SimpleNamespace(
                list_resources=lambda **kwargs: [
                    ResourceNameSummary(
                        resource_id="resource_1",
                        canonical_name="item",
                        aliases=["items"],
                    ),
                    ResourceNameSummary(
                        resource_id="resource_2",
                        canonical_name="empty",
                        aliases=[],
                    ),
                ]
            )

        def lookup(self, request):
            if request.resource == "empty":
                return ResourceLookupResult(status="found")
            return ResourceLookupResult(
                status="found",
                canonical_resource="item",
                identifiers=[
                    ResourceIdentifierSummary(
                        value="secret-item-id",
                        value_type="string",
                        last_seen_at=datetime.now(UTC),
                    )
                ],
            )

        def available_response_value_sources(self, **kwargs):
            self.source_requests.append(kwargs)
            return [
                ResponseValueSourceOption(
                    value_name="response_known",
                    value_count=3,
                    compatible_scalar_type="string",
                    source=(
                    ResponseValueSource(
                        producer_operation_key="GET /items",
                        status_code="200",
                        media_type="application/json",
                        selector="$.data[].item_id",
                        field_name="item_id",
                    )
                    ),
                )
            ]

    catalog, operation_key = _catalog(tmp_path)
    config = catalog.inspect_operation(operation_key)
    behavior_agent = BehaviorAgent()
    options = BehaviorMonitorReferenceValues(behavior_agent).available_options(
        ir=object(),
        config=config,
    )

    assert behavior_agent.source_requests[0]["parameter_name"] == "itemId"
    assert behavior_agent.source_requests[0]["expected_type"] is None
    assert {item.kind for item in options} == {
        "resource_identifier",
        "response_value",
    }
    assert all(item.value_count > 0 for item in options)
    assert all("empty" != item.canonical_resource for item in options)
    serialized = str([item.model_dump(mode="json") for item in options])
    assert "secret-item-id" not in serialized


def test_reference_options_exclude_request_body_and_object_nodes() -> None:
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from restscope.agent.api_behavior_monitor import (
        ResourceIdentifierSummary,
        ResourceLookupResult,
        ResourceNameSummary,
        ResponseValueSource,
        ResponseValueSourceOption,
    )
    from restscope.agent.operation_smoke import BehaviorMonitorReferenceValues
    from restscope.testing import (
        InputGeneratorConfig,
        InputNodeSnapshot,
        OperationGeneratorConfig,
        OperationTestSnapshot,
        SchemaSnapshot,
    )

    class BehaviorAgent:
        def __init__(self) -> None:
            self.source_input_ids = []
            self.catalog = SimpleNamespace(
                list_resources=lambda **kwargs: [
                    ResourceNameSummary(
                        resource_id="resource_1",
                        canonical_name="assignment",
                        aliases=[],
                    )
                ]
            )

        def lookup(self, request):
            return ResourceLookupResult(
                status="found",
                canonical_resource=request.resource,
                identifiers=[
                    ResourceIdentifierSummary(
                        value=7,
                        value_type="integer",
                        last_seen_at=datetime.now(UTC),
                    )
                ],
            )

        def available_response_value_sources(self, **kwargs):
            self.source_input_ids.append(kwargs["consumer_input_node_id"])
            return [
                ResponseValueSourceOption(
                    value_name="response_name",
                    value_count=1,
                    compatible_scalar_type="string",
                    source=ResponseValueSource(
                        producer_operation_key="GET /assignments",
                        status_code="200",
                        media_type="application/json",
                        selector="$.name",
                        field_name="name",
                    ),
                )
            ]

    config = OperationGeneratorConfig(
        operation_key="POST /assignments",
        revision=1,
        snapshot=OperationTestSnapshot(
            operation_key="POST /assignments",
            method="POST",
            path="/assignments",
            parameters=[],
            request_body_node_id="body",
            media_type_node_ids={"application/json": "body/application~1json"},
            available_media_types=["application/json"],
            input_nodes=[
                InputNodeSnapshot(
                    input_node_id="body",
                    node_kind="request_body",
                    canonical_path="body",
                    required=True,
                    schema_contract=None,
                ),
                InputNodeSnapshot(
                    input_node_id="body/application~1json",
                    node_kind="media_type",
                    canonical_path="body/application~1json",
                    parent_node_id="body",
                    required=True,
                    schema_contract=SchemaSnapshot(type="object"),
                ),
                InputNodeSnapshot(
                    input_node_id="body/application~1json/properties/name",
                    node_kind="property",
                    canonical_path="body/application~1json/properties/name",
                    parent_node_id="body/application~1json",
                    required=False,
                    schema_contract=SchemaSnapshot(type="string"),
                ),
            ],
        ),
        configs=[
            InputGeneratorConfig(
                input_node_id="body",
                inclusion_probability=1,
                strategy={"type": "request_body"},
            ),
            InputGeneratorConfig(
                input_node_id="body/application~1json",
                inclusion_probability=1,
                strategy={"type": "object"},
            ),
            InputGeneratorConfig(
                input_node_id="body/application~1json/properties/name",
                inclusion_probability=0.5,
                strategy={"type": "random_string"},
            ),
        ],
        active_media_type="application/json",
    )
    behavior_agent = BehaviorAgent()

    options = BehaviorMonitorReferenceValues(behavior_agent).available_options(
        ir=object(),
        config=config,
    )

    assert behavior_agent.source_input_ids == [
        "body/application~1json/properties/name"
    ]
    assert {
        option.input_node_id for option in options
    } == {"body/application~1json/properties/name"}


def test_reference_generator_selects_a_value_deterministically() -> None:
    from restscope.testing import (
        ResourceIdentifierGenerator,
        generate_strategy_value,
    )

    strategy = ResourceIdentifierGenerator(
        type="resource_identifier",
        resource="item",
    )
    values = _ReferenceValues(
        {("resource_identifier", "item"): ["item-1", "item-2"]}
    )

    first = generate_strategy_value(
        strategy,
        seed=42,
        reference_values=values,
    )
    second = generate_strategy_value(
        strategy,
        seed=42,
        reference_values=values,
    )

    assert first == second
    assert first in {"item-1", "item-2"}
