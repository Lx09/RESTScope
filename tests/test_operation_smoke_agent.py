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

    def diagnose(self, *, report, config):
        self.calls.append((report, config))
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


def _diagnosis(*, node_id: str, strategy: dict[str, Any]):
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

    assert result.status == "failed"
    assert len(runner.calls) == 2
    assert [
        item.lifecycle for item in catalog.list_revisions(operation_key)
    ] == ["accepted", "rejected", "rollback"]
    restored = catalog.inspect_operation(operation_key)
    assert restored.configs[0].strategy == baseline.configs[0].strategy


def test_empty_reference_pool_waits_without_running_the_candidate_batch(
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

    assert result.status == "waiting"
    assert len(runner.calls) == 1
    assert [
        item.model_dump(mode="json")
        for item in result.waiting_references
    ] == [
        {
            "input_node_id": node_id,
            "type": "resource_identifier",
            "name": "item",
        }
    ]
    assert [
        item.lifecycle for item in catalog.list_revisions(operation_key)
    ] == ["accepted", "candidate"]


def test_waiting_candidate_resumes_when_reference_value_arrives(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import (
        OperationSmokeAgent,
        OperationSmokeRequest,
    )

    catalog, operation_key = _catalog(tmp_path)
    node_id = catalog.inspect_operation(operation_key).configs[0].input_node_id
    runner = _BatchRunner(catalog, [(0, 10), (10, 0)])
    empty_values = _ReferenceValues()
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
        reference_values=empty_values,
    )
    waiting = agent.run(
        object(),
        OperationSmokeRequest(operation_key=operation_key),
    )
    assert waiting.status == "waiting"

    empty_values.values[("resource_identifier", "item")] = ["item-123"]
    resumed = agent.run(
        object(),
        OperationSmokeRequest(operation_key=operation_key),
    )

    assert resumed.status == "passed"
    assert len(runner.calls) == 2
    assert [
        item.lifecycle for item in catalog.list_revisions(operation_key)
    ] == ["accepted", "accepted"]


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
            self.values = []

        def register_response_value(self, **kwargs):
            self.registrations.append(kwargs)
            return SimpleNamespace(value_name="response_system_assigned")

        def response_values_for(self, value_name):
            assert value_name == "response_system_assigned"
            return list(self.values)

        def lookup(self, request):
            raise AssertionError(f"unexpected resource lookup: {request}")

    catalog, operation_key = _catalog(tmp_path)
    config = catalog.inspect_operation(operation_key)
    node_id = config.configs[0].input_node_id
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
                        "value_name": "model-invented-name",
                    },
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

    assert result.status == "waiting"
    assert behavior_agent.registrations == [
        {
            "ir": ir,
            "consumer_operation_key": operation_key,
            "consumer_input_node_id": node_id,
            "parameter_name": "itemId",
            "expected_type": "string",
        }
    ]
    candidate = catalog.inspect_operation(operation_key)
    assert candidate.configs[0].strategy.model_dump(mode="json") == {
        "type": "response_value",
        "value_name": "response_system_assigned",
    }

    behavior_agent.values.append("item-123")
    resumed = smoke.run(
        SimpleNamespace(ir=ir),
        OperationSmokeRequest(operation_key=operation_key),
    )

    assert resumed.status == "passed"
    assert len(runner.calls) == 2


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
