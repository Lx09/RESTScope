"""Integrated three-stage Operation Smoke feedback-loop contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


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


def test_groups_run_in_fresh_agents_then_one_candidate_batch_is_finalized(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import OperationSmokeRequest

    catalog, operation_key = _catalog(tmp_path)
    node_id = catalog.inspect_operation(operation_key).configs[0].input_node_id
    diagnosis = _diagnosis([_actionable("I1", "F1", "path.itemId")])
    task = _task("G1", "I1", "F1", "path.itemId")
    validated = _validated_update(
        "G1",
        "I1",
        "F1",
        node_id,
        "path.itemId",
        "known-item",
    )
    runner = _BatchRunner(catalog, [(0, 10), (5, 5)])
    diagnoser = _Diagnoser(
        [diagnosis],
        [
            _validation(
                resolved=["F1"],
                accepted_groups=["G1"],
                accepted_inputs=[node_id],
            )
        ],
    )
    factory = _PatchFactory([validated])
    smoke = _smoke(
        catalog=catalog,
        runner=runner,
        diagnoser=diagnoser,
        grouper=_Grouper([[task]]),
        factory=factory,
    )

    result = smoke.run(
        object(),
        OperationSmokeRequest(
            operation_key=operation_key,
            case_count=10,
            success_rate_threshold=0.8,
            max_feedback_rounds=1,
            seed=17,
        ),
    )

    assert result.status == "retry"
    assert [call["revision"] for call in runner.calls] == [1, 2]
    assert [call["seed"] for call in runner.calls] == [17, 17]
    assert [call["case_count"] for call in runner.calls] == [10, 10]
    assert len(factory.instances) == 1
    assert factory.instances[0].calls[0]["config"].revision == 1
    assert len(diagnoser.effect_calls) == 1
    current = catalog.inspect_operation(operation_key)
    assert current.revision == 2
    assert current.configs[0].strategy.value == "known-item"
    group_run = result.diagnoses[0].patch_group_runs[0]
    assert (group_run.group_id, group_run.status, group_run.attempts) == (
        "G1",
        "validated",
        2,
    )
    assert result.diagnoses[0].patch_validation.accepted_group_ids == ["G1"]


def test_candidate_is_rolled_back_before_database_error_propagates(
    tmp_path: Path,
) -> None:
    from sqlalchemy.exc import SQLAlchemyError

    from restscope.agent.operation_smoke import OperationSmokeRequest

    catalog, operation_key = _catalog(tmp_path)
    node_id = catalog.inspect_operation(operation_key).configs[0].input_node_id
    diagnosis = _diagnosis([_actionable("I1", "F1", "path.itemId")])
    task = _task("G1", "I1", "F1", "path.itemId")
    validated = _validated_update(
        "G1",
        "I1",
        "F1",
        node_id,
        "path.itemId",
        "known-item",
    )

    class CandidateDatabaseFailureRunner(_BatchRunner):
        def run_operation_for_smoke(self, *args, **kwargs):
            if self.calls:
                raise SQLAlchemyError("candidate database failure")
            return super().run_operation_for_smoke(*args, **kwargs)

    smoke = _smoke(
        catalog=catalog,
        runner=CandidateDatabaseFailureRunner(catalog, [(0, 10)]),
        diagnoser=_Diagnoser([diagnosis], []),
        grouper=_Grouper([[task]]),
        factory=_PatchFactory([validated]),
    )

    with pytest.raises(SQLAlchemyError, match="candidate database failure"):
        smoke.run(
            object(),
            OperationSmokeRequest(
                operation_key=operation_key,
                max_feedback_rounds=1,
                seed=19,
            ),
        )

    history = catalog.list_revisions(operation_key)
    assert [revision.lifecycle for revision in history] == [
        "accepted",
        "rejected",
        "rollback",
    ]
    assert catalog.inspect_operation(operation_key).revision == 3


def test_generator_groups_finalize_atomically_by_resolved_initial_failure(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import OperationSmokeRequest

    catalog, operation_key = _catalog(tmp_path)
    current = catalog.inspect_operation(operation_key)
    path_node = current.configs[0].input_node_id
    query_node = current.configs[1].input_node_id
    actionables = [
        _actionable("I1", "F1", "path.itemId"),
        _actionable("I2", "F2", "query.region"),
    ]
    groups = [
        _task("G1", "I1", "F1", "path.itemId"),
        _task("G2", "I2", "F2", "query.region"),
    ]
    outcomes = [
        _validated_update(
            "G1",
            "I1",
            "F1",
            path_node,
            "path.itemId",
            "known-item",
        ),
        _validated_update(
            "G2",
            "I2",
            "F2",
            query_node,
            "query.region",
            "eu",
        ),
    ]
    runner = _BatchRunner(catalog, [(0, 10), (5, 5)])
    smoke = _smoke(
        catalog=catalog,
        runner=runner,
        diagnoser=_Diagnoser(
            [_diagnosis(actionables)],
            [
                _validation(
                    resolved=["F1"],
                    persisting=["F2"],
                    accepted_groups=["G1"],
                    rejected_groups=["G2"],
                    accepted_inputs=[path_node],
                    rejected_inputs=[query_node],
                )
            ],
        ),
        grouper=_Grouper([groups]),
        factory=_PatchFactory(outcomes),
    )

    result = smoke.run(
        object(),
        OperationSmokeRequest(
            operation_key=operation_key,
            max_feedback_rounds=1,
            seed=23,
        ),
    )

    assert result.status == "retry"
    final = catalog.inspect_operation(operation_key)
    assert final.revision == 3
    assert final.configs[0].strategy.value == "known-item"
    assert getattr(final.configs[1].strategy, "value", None) != "eu"
    validation = result.diagnoses[0].patch_validation
    assert validation.accepted_group_ids == ["G1"]
    assert validation.rejected_group_ids == ["G2"]


def test_failed_group_does_not_accumulate_into_later_provisional_group(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import OperationSmokeRequest
    from restscope.agent.parameter_patch import PatchGroupFailure

    catalog, operation_key = _catalog(tmp_path)
    current = catalog.inspect_operation(operation_key)
    path_node = current.configs[0].input_node_id
    query_node = current.configs[1].input_node_id
    task1 = _task("G1", "I1", "F1", "path.itemId")
    task2 = _task("G2", "I2", "F2", "query.region")
    failure = PatchGroupFailure(
        group_id="G1",
        item_ids=["I1"],
        root_failure_refs=["F1"],
        reason="attempt_limit",
        attempts=20,
    )
    success = _validated_update(
        "G2",
        "I2",
        "F2",
        query_node,
        "query.region",
        "eu",
    )
    factory = _PatchFactory([failure, success])
    runner = _BatchRunner(catalog, [(0, 10), (5, 5)])
    smoke = _smoke(
        catalog=catalog,
        runner=runner,
        diagnoser=_Diagnoser(
            [
                _diagnosis(
                    [
                        _actionable("I1", "F1", "path.itemId"),
                        _actionable("I2", "F2", "query.region"),
                    ]
                )
            ],
            [
                _validation(
                    resolved=["F2"],
                    accepted_groups=["G2"],
                    accepted_inputs=[query_node],
                )
            ],
        ),
        grouper=_Grouper([[task1, task2]]),
        factory=factory,
    )

    result = smoke.run(
        object(),
        OperationSmokeRequest(
            operation_key=operation_key,
            max_feedback_rounds=1,
            seed=29,
        ),
    )

    assert result.status == "retry"
    assert len(factory.instances) == 2
    assert factory.instances[1].calls[0]["config"].revision == 1
    final = catalog.inspect_operation(operation_key)
    assert getattr(final.configs[0].strategy, "value", None) != "known-item"
    assert final.configs[1].strategy.value == "eu"
    assert [
        (item.group_id, item.status)
        for item in result.diagnoses[0].patch_group_runs
    ] == [("G1", "failed"), ("G2", "validated")]
    assert [
        item.item_id for item in result.diagnoses[0].actionable_failures
    ] == ["I2"]
    assert [
        (item.failure_ref, item.reason)
        for item in result.diagnoses[0].deferred_failures
    ] == [("F1", "patch_group_attempt_limit")]
    assert path_node not in result.diagnoses[
        0
    ].patch_validation.accepted_input_node_ids


def test_grouping_deferred_item_is_removed_from_actionable_routing(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import (
        OperationSmokeRequest,
        PatchGroupingResult,
    )

    catalog, operation_key = _catalog(tmp_path)
    current = catalog.inspect_operation(operation_key)
    path_node = current.configs[0].input_node_id
    task = _task("G1", "I1", "F1", "path.itemId")

    class GroupingWithDeferred:
        def group(self, **kwargs):
            del kwargs
            return PatchGroupingResult(
                status="grouped",
                tasks=[task],
                deferred_item_ids=["I2"],
            )

    smoke = _smoke(
        catalog=catalog,
        runner=_BatchRunner(catalog, [(0, 10), (5, 5)]),
        diagnoser=_Diagnoser(
            [
                _diagnosis(
                    [
                        _actionable("I1", "F1", "path.itemId"),
                        _actionable("I2", "F2", "query.region"),
                    ]
                )
            ],
            [
                _validation(
                    resolved=["F1"],
                    accepted_groups=["G1"],
                    accepted_inputs=[path_node],
                )
            ],
        ),
        grouper=GroupingWithDeferred(),
        factory=_PatchFactory(
            [
                _validated_update(
                    "G1",
                    "I1",
                    "F1",
                    path_node,
                    "path.itemId",
                    "known-item",
                )
            ]
        ),
    )

    result = smoke.run(
        object(),
        OperationSmokeRequest(
            operation_key=operation_key,
            max_feedback_rounds=1,
            seed=31,
        ),
    )

    final_diagnosis = result.diagnoses[0]
    assert [
        item.item_id for item in final_diagnosis.actionable_failures
    ] == ["I1"]
    assert [
        (item.failure_ref, item.reason)
        for item in final_diagnosis.deferred_failures
    ] == [("F2", "patch_grouping_deferred")]


def test_constraint_only_group_is_run_local_and_does_not_create_revision(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import OperationSmokeRequest
    from restscope.agent.parameter_patch import (
        CompiledConstraintPatch,
        GeneratorPatchDraft,
        ValidatedPatchGroup,
    )
    from restscope.testing import ConstraintSet

    catalog, operation_key = _catalog(tmp_path)
    node_id = catalog.inspect_operation(operation_key).configs[0].input_node_id
    constraint = CompiledConstraintPatch(
        constraint_id="constraint_presence",
        group_ids=["G1"],
        item_ids=["I1"],
        root_failure_refs=["F1"],
        kind="Complex",
        constraint=ConstraintSet(
            constraints=[
                {
                    "type": "present",
                    "input_node_id": node_id,
                }
            ]
        ),
    )
    outcome = ValidatedPatchGroup(
        group_id="G1",
        item_ids=["I1"],
        root_failure_refs=["F1"],
        patch=GeneratorPatchDraft(constraints=[constraint]),
        samples=[{"path.itemId": "generated"} for _ in range(10)],
        attempts=2,
    )
    runner = _BatchRunner(catalog, [(0, 10), (5, 5), (0, 10)])
    diagnoser = _Diagnoser(
        [_diagnosis([_actionable("I1", "F1", "path.itemId")])],
        [
            _validation(
                resolved=["F1"],
                accepted_groups=["G1"],
                accepted_constraints=["constraint_presence"],
            )
        ],
    )
    smoke = _smoke(
        catalog=catalog,
        runner=runner,
        diagnoser=diagnoser,
        grouper=_Grouper(
            [[_task("G1", "I1", "F1", "path.itemId")]]
        ),
        factory=_PatchFactory([outcome]),
    )
    request = OperationSmokeRequest(
        operation_key=operation_key,
        max_feedback_rounds=1,
        seed=31,
    )

    first = smoke.run(object(), request)
    second = smoke.run(
        object(),
        request.model_copy(update={"max_feedback_rounds": 0}),
    )

    assert first.status == "retry"
    assert second.status == "retry"
    assert catalog.inspect_operation(operation_key).revision == 1
    assert runner.calls[0]["constraints"] is None
    assert runner.calls[1]["constraints"] is not None
    assert runner.calls[2]["constraints"] is None


def test_success_threshold_accepts_every_successful_group(
    tmp_path: Path,
) -> None:
    from restscope.agent.operation_smoke import OperationSmokeRequest

    catalog, operation_key = _catalog(tmp_path)
    current = catalog.inspect_operation(operation_key)
    path_node = current.configs[0].input_node_id
    query_node = current.configs[1].input_node_id
    groups = [
        _task("G1", "I1", "F1", "path.itemId"),
        _task("G2", "I2", "F2", "query.region"),
    ]
    outcomes = [
        _validated_update(
            "G1",
            "I1",
            "F1",
            path_node,
            "path.itemId",
            "known-item",
        ),
        _validated_update(
            "G2",
            "I2",
            "F2",
            query_node,
            "query.region",
            "eu",
        ),
    ]
    smoke = _smoke(
        catalog=catalog,
        runner=_BatchRunner(catalog, [(0, 10), (10, 0)]),
        diagnoser=_Diagnoser(
            [
                _diagnosis(
                    [
                        _actionable("I1", "F1", "path.itemId"),
                        _actionable("I2", "F2", "query.region"),
                    ]
                )
            ],
            [
                _validation(
                    unknown=["F1", "F2"],
                    rejected_groups=["G1", "G2"],
                    rejected_inputs=[path_node, query_node],
                )
            ],
        ),
        grouper=_Grouper([groups]),
        factory=_PatchFactory(outcomes),
    )

    result = smoke.run(
        object(),
        OperationSmokeRequest(
            operation_key=operation_key,
            success_rate_threshold=0.8,
            max_feedback_rounds=1,
            seed=37,
        ),
    )

    assert result.status == "passed"
    assert result.diagnoses[0].patch_validation.accepted_group_ids == [
        "G1",
        "G2",
    ]
    final = catalog.inspect_operation(operation_key)
    assert final.configs[0].strategy.value == "known-item"
    assert final.configs[1].strategy.value == "eu"


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
                            },
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
    seed: int,
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
        seed=seed,
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

    def run_operation_for_smoke(
        self,
        context,
        /,
        *,
        operation_key: str,
        case_count: int,
        seed: int,
        constraints=None,
    ):
        del context
        config = self.catalog.inspect_operation(operation_key)
        passed, failed = self.outcomes.pop(0)
        self.calls.append(
            {
                "operation_key": operation_key,
                "case_count": case_count,
                "seed": seed,
                "revision": config.revision,
                "constraints": constraints,
            }
        )
        return SimpleNamespace(
            report=_report(
                operation_key=operation_key,
                revision=config.revision,
                run_number=len(self.calls),
                seed=seed,
                passed=passed,
                failed=failed,
            ),
            case_evidence=[],
        )


class _Diagnoser:
    def __init__(self, diagnoses, validations) -> None:
        self.diagnoses = list(diagnoses)
        self.validations = list(validations)
        self.calls: list[dict[str, Any]] = []
        self.effect_calls: list[dict[str, Any]] = []

    def diagnose(self, **kwargs):
        self.calls.append(kwargs)
        return self.diagnoses.pop(0)

    def validate_effect(self, **kwargs):
        self.effect_calls.append(kwargs)
        return self.validations.pop(0)


class _Grouper:
    def __init__(self, task_batches) -> None:
        self.task_batches = list(task_batches)

    def group(self, **kwargs):
        from restscope.agent.operation_smoke import PatchGroupingResult

        del kwargs
        return PatchGroupingResult(
            status="grouped",
            tasks=self.task_batches.pop(0),
        )


class _PatchAgent:
    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.outcome


class _PatchFactory:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.instances: list[_PatchAgent] = []

    def create(self):
        instance = _PatchAgent(self.outcomes.pop(0))
        self.instances.append(instance)
        return instance


class _ReferenceValues:
    def values_for(self, strategy):
        del strategy
        return []

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
        return updates


def _smoke(*, catalog, runner, diagnoser, grouper, factory):
    from restscope.agent.operation_smoke import OperationSmokeAgent

    return OperationSmokeAgent(
        config_catalog=catalog,
        batch_runner=runner,
        diagnoser=diagnoser,
        group_planner=grouper,
        patch_agent_factory=factory,
        reference_values=_ReferenceValues(),
    )


def _actionable(item_id: str, failure_ref: str, input_handle: str):
    from restscope.agent.operation_smoke import (
        ActionableFailure,
        ParameterSolution,
    )

    return ActionableFailure(
        item_id=item_id,
        failure_ref=failure_ref,
        root_failure_refs=[failure_ref],
        evidence_origin="initial",
        cause=f"{input_handle} has an invalid generated value.",
        solutions=[
            ParameterSolution(
                input=input_handle,
                desired_behavior=f"Generate an accepted {input_handle}.",
            )
        ],
        affected_inputs=[input_handle],
        evidence_refs=[failure_ref],
    )


def _diagnosis(actionables):
    from restscope.agent.operation_smoke import PlanSolveDiagnosisResult

    return PlanSolveDiagnosisResult(
        status="actionable",
        termination_reason="all_failures_processed",
        actionable_failures=actionables,
    )


def _task(
    group_id: str,
    item_id: str,
    failure_ref: str,
    input_handle: str,
):
    from restscope.agent.parameter_patch import PatchGroupTask

    return PatchGroupTask(
        group_id=group_id,
        item_ids=[item_id],
        root_failure_refs=[failure_ref],
        inputs=[input_handle],
        objective=f"Repair {failure_ref}.",
        requirements=[f"Generate an accepted {input_handle}."],
    )


def _validated_update(
    group_id: str,
    item_id: str,
    failure_ref: str,
    node_id: str,
    input_handle: str,
    value: str,
):
    from restscope.agent.parameter_patch import (
        GeneratorPatchAttribution,
        GeneratorPatchDraft,
        ValidatedPatchGroup,
    )

    return ValidatedPatchGroup(
        group_id=group_id,
        item_ids=[item_id],
        root_failure_refs=[failure_ref],
        patch=GeneratorPatchDraft(
            updates=[
                {
                    "input_node_id": node_id,
                    "strategy": {"type": "constant", "value": value},
                }
            ],
            attributions=[
                GeneratorPatchAttribution(
                    input_node_id=node_id,
                    group_ids=[group_id],
                    item_ids=[item_id],
                    root_failure_refs=[failure_ref],
                )
            ],
        ),
        samples=[{input_handle: value} for _ in range(10)],
        attempts=2,
    )


def _validation(
    *,
    resolved=(),
    persisting=(),
    unknown=(),
    accepted_groups=(),
    rejected_groups=(),
    accepted_inputs=(),
    rejected_inputs=(),
    accepted_constraints=(),
):
    from restscope.agent.operation_smoke import PatchValidationSummary

    items = [
        {
            "item_id": failure_ref,
            "status": status,
            "current_failure_refs": [],
            "reason": f"{failure_ref} is {status}.",
            "confidence": 0.9,
        }
        for status, refs in (
            ("resolved", resolved),
            ("persisting", persisting),
            ("unknown", unknown),
        )
        for failure_ref in refs
    ]
    return PatchValidationSummary(
        items=items,
        accepted_item_ids=list(resolved),
        accepted_group_ids=list(accepted_groups),
        rejected_group_ids=list(rejected_groups),
        accepted_input_node_ids=list(accepted_inputs),
        rejected_input_node_ids=list(rejected_inputs),
        accepted_constraint_ids=list(accepted_constraints),
    )
