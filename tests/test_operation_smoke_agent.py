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
        revision = self.catalog.inspect_operation(operation_key).revision
        passed, failed = self.outcomes.pop(0)
        self.calls.append(
            {
                "operation_key": operation_key,
                "case_count": case_count,
                "seed": seed,
                "revision": revision,
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
    def __init__(self, results) -> None:
        self.results = list(results)
        self.calls: list[tuple[Any, Any]] = []

    def diagnose(self, *, report, config, reference_option_provider):
        self.calls.append((report, config, reference_option_provider))
        return self.results.pop(0)


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
    selected_reference_options=None,
):
    from restscope.agent.operation_smoke import (
        ParameterDiagnosis,
        ParameterSuspect,
        TwoRoundDiagnosisResult,
    )

    return TwoRoundDiagnosisResult(
        diagnosis=ParameterDiagnosis(
            no_parameter_issue=False,
            suspects=[
                ParameterSuspect(
                    input_node_id=node_id,
                    confidence=0.9,
                    reason="failure points to this input",
                    evidence_refs=["failure_1"],
                )
            ],
        ),
        updates=[
            {
                "input_node_id": node_id,
                "strategy": strategy,
            }
        ],
        selected_reference_options=selected_reference_options or [],
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
        [_diagnosis(node_id=node_id, strategy={"type": "constant", "value": "known"})]
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
    assert [
        item.lifecycle for item in catalog.list_revisions(operation_key)
    ] == ["accepted", "accepted"]
    assert catalog.list_revisions(operation_key)[1].evaluation[
        "run_id"
    ] == "run_2"


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
            ]
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
    ] == ["accepted", "rejected", "rollback"]
    restored = catalog.inspect_operation(operation_key)
    assert restored.configs[0].strategy == baseline.configs[0].strategy


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
            ]
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
        "expected_type": "string",
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
            del kwargs
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
    options = BehaviorMonitorReferenceValues(BehaviorAgent()).available_options(
        ir=object(),
        config=config,
    )

    assert {item.kind for item in options} == {
        "resource_identifier",
        "response_value",
    }
    assert all(item.value_count > 0 for item in options)
    assert all("empty" != item.canonical_resource for item in options)
    serialized = str([item.model_dump(mode="json") for item in options])
    assert "secret-item-id" not in serialized


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
