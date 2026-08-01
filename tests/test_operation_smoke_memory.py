"""Protect stable Failure, Solve Attempt, and atomic Patch persistence."""

from __future__ import annotations

import pytest


def _memory_fixture(*, initialize_generators: bool = False):
    """Build production Smoke adapters over one foreign-key-enabled database."""

    from restscope.db import (
        Base,
        SqlAlchemySmokeMemoryUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.operation_smoke.memory import SmokeMemory, SmokePatchApplication

    engine = create_engine_from_url("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    factory = lambda: SqlAlchemySmokeMemoryUnitOfWork(session_factory)
    if initialize_generators:
        from tests._operation_smoke_dedup_solve_fixtures import smoke_config

        config = smoke_config()
        with factory() as uow:
            uow.generator_configs.initialize(
                [(config.operation_key, config.configs)]
            )
            uow.commit()
    return SmokeMemory(factory), SmokePatchApplication(factory), factory


def _failure_write(*, suspected_input_node_ids=None):
    """Build one normalized Failure occurrence with explicit attribution state."""

    from restscope.operation_smoke.memory import FailureBatchWrite, FailureWrite

    return FailureBatchWrite(
        operation_key="GET /projects/{projectId}",
        failures=[
            FailureWrite(
                summary="Project identifier is rejected.",
                messages=["HTTP 404: project missing"],
                suspected_input_node_ids=suspected_input_node_ids,
                last_status_code=404,
            )
        ],
    )


def test_stable_failure_reuses_identity_and_updates_occurrence_metadata() -> None:
    """Repeated normalized evidence increments one stable Failure, not a new row."""

    memory, _, _ = _memory_fixture(initialize_generators=True)

    first = memory.record_failures(_failure_write())
    second = memory.record_failures(_failure_write())
    history = memory.failure_history(
        operation_key="GET /projects/{projectId}",
        failure_id=first.failures[0].failure_id,
    )

    assert second.failures[0].failure_id == first.failures[0].failure_id
    assert history.occurrence_count == 2
    assert history.attempts == []


def test_failure_key_preserves_null_empty_and_precise_suspected_input_states() -> None:
    """Bypassed, operation-level, and input-attributed Failures remain distinct."""

    memory, _, _ = _memory_fixture(initialize_generators=True)
    bypassed = memory.record_failures(_failure_write(suspected_input_node_ids=None))
    operation_level = memory.record_failures(
        _failure_write(suspected_input_node_ids=[])
    )
    attributed = memory.record_failures(
        _failure_write(suspected_input_node_ids=["path/projectId"])
    )

    assert len(
        {
            bypassed.failures[0].failure_id,
            operation_level.failures[0].failure_id,
            attributed.failures[0].failure_id,
        }
    ) == 3


def test_failure_rejects_suspected_input_outside_current_operation() -> None:
    """Stable Failure identity cannot contain an unvalidated foreign input ID."""

    memory, _, _ = _memory_fixture(initialize_generators=True)

    with pytest.raises(ValueError, match="unknown operation input"):
        memory.record_failures(
            _failure_write(
                suspected_input_node_ids=["another-operation/input"]
            )
        )


def test_no_patch_attempt_is_append_only_and_queryable_by_input() -> None:
    """A validated attribution appears in both Failure and Parameter history."""

    from restscope.operation_smoke.memory import (
        SolveAttemptParameterWrite,
        SolveAttemptWrite,
    )

    memory, _, _ = _memory_fixture(initialize_generators=True)
    failure_id = memory.record_failures(
        _failure_write(suspected_input_node_ids=["path/projectId"])
    ).failures[0].failure_id
    attempt_id = memory.record_solve_attempt(
        SolveAttemptWrite(
            operation_key="GET /projects/{projectId}",
            failure_id=failure_id,
            round_number=1,
            outcome="no_patch",
            trigger_conditions="unknown identifiers return 404",
            root_cause="the target record does not exist",
            solution="no safe Generator change is justified",
            evidence_source="batch",
            parameters=[
                SolveAttemptParameterWrite(
                    input_node_id="path/projectId",
                    cause_summary="The path value selects the missing record.",
                )
            ],
        )
    )

    failure = memory.failure_history(
        operation_key="GET /projects/{projectId}",
        failure_id=failure_id,
    )
    parameter = memory.parameter_history(
        operation_key="GET /projects/{projectId}",
        input_node_id="path/projectId",
    )
    assert failure.attempts[0].solve_attempt_id == attempt_id
    assert failure.attempts[0].generator_change is None
    assert parameter.failures[0].failure_id == failure_id


def _patch_attempt(failure_id: str):
    """Build the durable explanation paired with one accepted Patch."""

    from restscope.operation_smoke.memory import (
        PatchSolveAttempt,
        SolveAttemptParameterWrite,
    )

    return PatchSolveAttempt(
        operation_key="GET /projects/{projectId}",
        failure_id=failure_id,
        round_number=1,
        trigger_conditions="unknown identifiers return 404",
        root_cause="path.projectId uses arbitrary strings",
        solution="use a known project identifier",
        evidence_source="batch",
        parameters=[
            SolveAttemptParameterWrite(
                input_node_id="path/projectId",
                cause_summary="This path value selects the missing project.",
            )
        ],
    )


def _generator_patch():
    """Replace the current path Generator with a deterministic known value."""

    from restscope.operation_smoke.parameter_patch import GeneratorPatchDraft
    from restscope.testing import InputGeneratorPatch
    from restscope.testing.models import ConstantGenerator

    return GeneratorPatchDraft(
        updates=[
            InputGeneratorPatch(
                input_node_id="path/projectId",
                strategy=ConstantGenerator(
                    type="constant",
                    value="known-project",
                ),
            )
        ]
    )


def test_patch_commits_current_generator_attempt_and_change_event_atomically() -> None:
    """An accepted Patch exposes current state and its exact before/after event."""

    from tests._operation_smoke_dedup_solve_fixtures import smoke_config

    memory, application, factory = _memory_fixture(initialize_generators=True)
    failure_id = memory.record_failures(_failure_write()).failures[0].failure_id

    applied = application.apply(
        current=smoke_config(),
        expected_constraints=[],
        patch=_generator_patch(),
        attempt=_patch_attempt(failure_id),
    )

    with factory() as uow:
        inputs = uow.generator_configs.get_inputs("GET /projects/{projectId}")
    history = memory.failure_history(
        operation_key="GET /projects/{projectId}",
        failure_id=failure_id,
    )
    change = history.attempts[0].generator_change
    assert inputs[0].strategy.type == "constant"
    assert applied.config.configs[0].strategy.type == "constant"
    assert history.attempts[0].solve_attempt_id == applied.solve_attempt_id
    assert change is not None
    assert change.event_id == applied.generator_change_event_id
    assert change.generator_changes[0]["before"]["strategy"]["type"] == "random_string"
    assert "samples" not in change.model_dump(mode="json")


def test_constraint_only_patch_persists_stable_expression_identity() -> None:
    """Constraint-only acceptance changes no Generator and stores derived owners."""

    from restscope.operation_smoke.parameter_patch import (
        CompiledConstraintPatch,
        GeneratorPatchDraft,
    )
    from restscope.testing import ConstraintSet, PresentPredicate
    from tests._operation_smoke_dedup_solve_fixtures import smoke_config

    memory, application, _ = _memory_fixture(initialize_generators=True)
    failure_id = memory.record_failures(_failure_write()).failures[0].failure_id
    patch = GeneratorPatchDraft(
        constraints=[
            CompiledConstraintPatch(
                constraint_id="agent-order-dependent-id-is-ignored",
                kind="Complex",
                constraint=ConstraintSet(
                    constraints=[
                        PresentPredicate(
                            type="present",
                            input_node_id="path/projectId",
                        )
                    ]
                ),
            )
        ]
    )

    applied = application.apply(
        current=smoke_config(),
        expected_constraints=[],
        patch=patch,
        attempt=_patch_attempt(failure_id),
    )

    assert applied.config == smoke_config()
    assert applied.constraints[0].id.startswith("constraint_")
    assert applied.constraints[0].id != "agent-order-dependent-id-is-ignored"
    assert applied.constraints[0].owner_input_node_ids == ["path/projectId"]


def test_patch_rejects_constraint_state_changed_after_candidate_sampling() -> None:
    """A candidate cannot silently replace Constraints it was never shown."""

    from restscope.testing import (
        ConstraintSet,
        OperationConstraintRecord,
        PresentPredicate,
    )
    from restscope.testing.ports import GeneratorConfigConcurrentWrite
    from tests._operation_smoke_dedup_solve_fixtures import smoke_config

    memory, application, factory = _memory_fixture(initialize_generators=True)
    failure_id = memory.record_failures(_failure_write()).failures[0].failure_id
    concurrent_constraint = OperationConstraintRecord(
        id="constraint_concurrent",
        operation_key="GET /projects/{projectId}",
        owner_input_node_ids=["path/projectId"],
        kind="Complex",
        constraint=ConstraintSet(
            constraints=[
                PresentPredicate(
                    type="present",
                    input_node_id="path/projectId",
                )
            ]
        ),
    )
    with factory() as uow:
        uow.generator_configs.replace_constraints(
            operation_key="GET /projects/{projectId}",
            expected=[],
            updated=[concurrent_constraint],
        )
        uow.commit()

    with pytest.raises(GeneratorConfigConcurrentWrite):
        application.apply(
            current=smoke_config(),
            expected_constraints=[],
            patch=_generator_patch(),
            attempt=_patch_attempt(failure_id),
        )

    history = memory.failure_history(
        operation_key="GET /projects/{projectId}",
        failure_id=failure_id,
    )
    assert history.attempts == []


def test_transitive_constraint_overlap_replaces_old_connected_scope() -> None:
    """New {c,d} replaces connected old {a,b}/{b,c} but owns only {c,d}."""

    from restscope.operation_smoke.memory import replace_constraint_scope
    from restscope.testing import (
        AndConstraint,
        ConstraintSet,
        OperationConstraintRecord,
        PresentPredicate,
    )

    def record(identity: str, owners: list[str]):
        expression = AndConstraint(
            type="and",
            expressions=[
                PresentPredicate(type="present", input_node_id=item)
                for item in owners
            ],
        )
        return OperationConstraintRecord(
            id=identity,
            operation_key="GET /items",
            owner_input_node_ids=owners,
            kind="Complex",
            constraint=ConstraintSet(constraints=[expression]),
        )

    result = replace_constraint_scope(
        [record("old-ab", ["a", "b"]), record("old-bc", ["b", "c"])],
        [record("new-cd", ["c", "d"])],
        has_constraint_patch=True,
    )

    assert [item.id for item in result] == ["new-cd"]
    assert result[0].owner_input_node_ids == ["c", "d"]


def test_patch_rejects_no_actual_change_without_an_event() -> None:
    """A content-identical candidate cannot create a misleading accepted event."""

    from restscope.operation_smoke.parameter_patch import GeneratorPatchDraft
    from restscope.testing import InputGeneratorPatch
    from tests._operation_smoke_dedup_solve_fixtures import smoke_config

    memory, application, _ = _memory_fixture(initialize_generators=True)
    failure_id = memory.record_failures(_failure_write()).failures[0].failure_id

    current = smoke_config()
    unchanged = GeneratorPatchDraft(
        updates=[
            InputGeneratorPatch(
                input_node_id="path/projectId",
                strategy=current.configs[0].strategy,
            )
        ]
    )
    with pytest.raises(ValueError, match="does not change"):
        application.apply(
            current=current,
            expected_constraints=[],
            patch=unchanged,
            attempt=_patch_attempt(failure_id),
        )

    history = memory.failure_history(
        operation_key="GET /projects/{projectId}",
        failure_id=failure_id,
    )
    assert history.attempts == []


def test_patch_rejects_constraint_owner_outside_current_operation() -> None:
    """A forged input ID cannot enter current Constraints or change events."""

    from restscope.operation_smoke.parameter_patch import (
        CompiledConstraintPatch,
        GeneratorPatchDraft,
    )
    from restscope.testing import ConstraintSet, PresentPredicate
    from tests._operation_smoke_dedup_solve_fixtures import smoke_config

    memory, application, _ = _memory_fixture(initialize_generators=True)
    failure_id = memory.record_failures(_failure_write()).failures[0].failure_id
    patch = GeneratorPatchDraft(
        constraints=[
            CompiledConstraintPatch(
                constraint_id="forged",
                kind="Complex",
                constraint=ConstraintSet(
                    constraints=[
                        PresentPredicate(
                            type="present",
                            input_node_id="another-operation/input",
                        )
                    ]
                ),
            )
        ]
    )

    with pytest.raises(ValueError, match="unknown operation inputs"):
        application.apply(
            current=smoke_config(),
            expected_constraints=[],
            patch=patch,
            attempt=_patch_attempt(failure_id),
        )

    history = memory.failure_history(
        operation_key="GET /projects/{projectId}",
        failure_id=failure_id,
    )
    assert history.attempts == []


def test_memory_failure_rolls_back_generator_constraint_and_event(monkeypatch) -> None:
    """A failed Attempt insert leaves all current and audit tables unchanged."""

    from restscope.db.repositories import SqlAlchemySmokeMemoryRepository
    from tests._operation_smoke_dedup_solve_fixtures import smoke_config

    memory, application, factory = _memory_fixture(initialize_generators=True)
    failure_id = memory.record_failures(_failure_write()).failures[0].failure_id

    def fail_record(_self, _write):
        raise RuntimeError("simulated attempt write failure")

    monkeypatch.setattr(
        SqlAlchemySmokeMemoryRepository,
        "record_solve_attempt",
        fail_record,
    )
    with pytest.raises(RuntimeError, match="simulated attempt write failure"):
        application.apply(
            current=smoke_config(),
            expected_constraints=[],
            patch=_generator_patch(),
            attempt=_patch_attempt(failure_id),
        )

    with factory() as uow:
        inputs = uow.generator_configs.get_inputs("GET /projects/{projectId}")
        constraints = uow.generator_configs.get_constraints(
            "GET /projects/{projectId}"
        )
    assert inputs == smoke_config().configs
    assert constraints == []
