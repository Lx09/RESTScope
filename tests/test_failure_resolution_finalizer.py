"""Protect registry-based validation and atomic Failure Resolution commits."""

from __future__ import annotations

import pytest


def _database(*, unit_of_work_class=None):
    """Build a foreign-key-enabled database with the current Generator baseline."""
    from restscope.db import (
        Base,
        SqlAlchemySmokeMemoryUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from tests._operation_smoke_resolution_fixtures import smoke_config

    engine = create_engine_from_url("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    uow_type = unit_of_work_class or SqlAlchemySmokeMemoryUnitOfWork
    factory = lambda: uow_type(session_factory)
    current = smoke_config()
    with SqlAlchemySmokeMemoryUnitOfWork(session_factory) as uow:
        uow.generator_configs.initialize(
            [(current.operation_key, current.configs)]
        )
        uow.commit()
    return current, factory, session_factory


def _catalog():
    """Create one trusted failed Test Case and both operation Parameter handles."""
    from restscope.harness.operation_testing.test_case_catalog import (
        CatalogTestCaseDraft,
        HTTPFailure,
        TestCaseCatalog,
    )
    from restscope.operation_references import RequestInputReference

    catalog = TestCaseCatalog(
        input_references=[
            RequestInputReference.parameter("path", "projectId"),
            RequestInputReference.parameter("query", "region"),
        ]
    )
    catalog.record(
        CatalogTestCaseDraft(
            request={
                "path": {"projectId": "random-123"},
                "query": {"region": "us-east"},
                "header": {},
                "cookie": {},
            },
            response_body={"message": "project missing"},
            failure=HTTPFailure(
                status_code=404,
                messages=["HTTP 404: project missing"],
            ),
        )
    )
    return catalog


def _request():
    """Build the Batch identity used by direct finalizer tests."""
    from restscope.operation_smoke.failure_resolution import FailureResolutionRequest

    return FailureResolutionRequest(
        operation_key="GET /projects/{projectId}",
        round_number=1,
        batch_run_id="run-1",
        case_ids=["TC1"],
    )


def _source():
    """Build the immutable exact source registry entry for TC1."""
    from restscope.operation_smoke.failure_resolution import FailureSource

    return FailureSource(
        failure_ref="E1",
        message="HTTP 404: project missing",
        test_case_refs=["TC1"],
    )


def _candidate(registry, *, handle, node_id, value):
    """Issue one precise constant-Generator candidate for a semantic input."""
    from restscope.operation_smoke.memory import SolveAttemptParameterWrite
    from restscope.operation_smoke.parameter_patch import GeneratorPatchDraft
    from restscope.request_generation import InputGeneratorPatch
    from restscope.request_generation.models import ConstantGenerator

    return registry.issue(
        patch=GeneratorPatchDraft(
            updates=[
                InputGeneratorPatch(
                    input_node_id=node_id,
                    strategy=ConstantGenerator(type="constant", value=value),
                )
            ]
        ),
        root_cause="Candidate-authored cause must not override the final worklist.",
        change_reason=f"Use accepted {handle} values.",
        affected_parameters=[handle],
        parameter_attributions=[
            SolveAttemptParameterWrite(
                input_node_id=node_id,
                cause_summary="Candidate-authored attribution.",
            )
        ],
        before_generators={handle: {"type": "random"}},
        after_generators={handle: {"type": "constant"}},
        samples=[
            {
                "values": {handle: value},
                "present": {handle: True},
            }
        ],
        outputs_used=3,
    )


def _worklist(*, registry, items, sources=None):
    """Validate final test items through the same reference-only store."""
    from restscope.operation_smoke.failure_resolution import FailureWorklistStore

    store = FailureWorklistStore(
        sources=list(sources or [_source()]),
        valid_parameters={"path.projectId", "query.region"},
        candidate_refs=registry.refs,
    )
    return store.write(expected_revision=0, active_item_id=None, items=items)


def _item(
    *,
    item_id,
    outcome,
    candidate_ref=None,
    suspected_parameters=("path.projectId",),
    source_failure_refs=("E1",),
):
    """Build one decided reference-only item without precise runtime objects."""
    from restscope.operation_smoke.failure_resolution import (
        WorklistDecision,
        WorklistItem,
    )

    return WorklistItem(
        item_id=item_id,
        source_failure_refs=list(source_failure_refs),
        test_case_refs=["TC1"],
        suspected_parameters=list(suspected_parameters),
        progress="Investigation is complete.",
        root_cause="Final worklist root cause.",
        candidate_refs=[candidate_ref] if candidate_ref else [],
        decision=WorklistDecision(
            outcome=outcome,
            selected_candidate_ref=candidate_ref,
            reason="Final Agent decision reason.",
        ),
    )


def test_duplicate_stable_failure_in_one_batch_increments_occurrence_once() -> None:
    """Overlapping semantic items may add Attempts without double-counting evidence."""
    from restscope.operation_smoke.failure_resolution import (
        FailureResolutionFinalizer,
        PatchCandidateRegistry,
    )
    from restscope.operation_smoke.memory import SmokeMemory

    current, factory, _session_factory = _database()
    registry = PatchCandidateRegistry()
    worklist = _worklist(
        registry=registry,
        items=[
            _item(item_id="WI-001", outcome="no_patch"),
            _item(item_id="WI-002", outcome="no_patch"),
        ],
    )

    commit = FailureResolutionFinalizer(factory).finalize(
        request=_request(),
        sources=(_source(),),
        worklist=worklist,
        candidates=registry,
        catalog=_catalog(),
        current=current,
        active_constraints=[],
    )

    assert len(commit.items) == 2
    assert commit.items[0].failure_id == commit.items[1].failure_id
    history = SmokeMemory(factory).failure_history(
        operation_key=current.operation_key,
        failure_id=commit.items[0].failure_id,
    )
    assert history.occurrence_count == 1
    assert history.summary == "HTTP 404: project missing"
    assert len(history.attempts) == 2
    assert history.attempts[0].reason == "Final Agent decision reason."
    assert history.attempts[0].root_cause == "Final worklist root cause."
    assert history.attempts[0].reason == "Final Agent decision reason."
    assert history.attempts[0].parameters[0].input_node_id == "path/projectId"


def test_grouped_failure_summary_is_derived_from_canonical_exact_messages() -> None:
    """A semantic group gets bounded display text without Agent-authored summary."""
    from restscope.operation_smoke.failure_resolution import (
        FailureResolutionFinalizer,
        FailureSource,
        PatchCandidateRegistry,
    )
    from restscope.operation_smoke.memory import SmokeMemory

    current, factory, _session_factory = _database()
    registry = PatchCandidateRegistry()
    second = FailureSource(
        failure_ref="E2",
        message="HTTP 422: namespace is invalid",
        test_case_refs=["TC1"],
    )
    sources = (_source(), second)
    worklist = _worklist(
        registry=registry,
        sources=sources,
        items=[
            _item(
                item_id="WI-001",
                outcome="no_patch",
                # Agent ordering cannot change the canonical display summary.
                source_failure_refs=("E2", "E1"),
            )
        ],
    )

    commit = FailureResolutionFinalizer(factory).finalize(
        request=_request(),
        sources=sources,
        worklist=worklist,
        candidates=registry,
        catalog=_catalog(),
        current=current,
        active_constraints=[],
    )

    assert commit.items[0].failure_summary == (
        "HTTP 404: project missing (+1 related Failure messages)"
    )
    history = SmokeMemory(factory).failure_history(
        operation_key=current.operation_key,
        failure_id=commit.items[0].failure_id,
    )
    assert history.summary == commit.items[0].failure_summary


def test_apply_patch_dereferences_candidate_and_recomputes_combined_state() -> None:
    """Agent text controls diagnosis, while exact Patch and input IDs come from P1."""
    from restscope.operation_smoke.failure_resolution import (
        FailureResolutionFinalizer,
        PatchCandidateRegistry,
    )
    from restscope.operation_smoke.memory import SmokeMemory

    current, factory, _session_factory = _database()
    registry = PatchCandidateRegistry()
    candidate = _candidate(
        registry,
        handle="path.projectId",
        node_id="path/projectId",
        value="known-project",
    )
    worklist = _worklist(
        registry=registry,
        items=[
            _item(
                item_id="WI-001",
                outcome="apply_patch",
                candidate_ref=candidate.candidate_ref,
                suspected_parameters=(),
            )
        ],
    )
    sampled = []

    commit = FailureResolutionFinalizer(factory).finalize(
        request=_request(),
        sources=(_source(),),
        worklist=worklist,
        candidates=registry,
        catalog=_catalog(),
        current=current,
        active_constraints=[],
        validate_combined_patch=sampled.append,
    )

    assert len(sampled) == 1
    assert sampled[0].updates == candidate.patch.updates
    assert commit.applied_candidate_refs == ["P1"]
    with factory() as uow:
        stored = uow.generator_configs.get_inputs(current.operation_key)
    assert stored[0].strategy.model_dump(mode="json") == {
        "type": "constant",
        "value": "known-project",
    }
    history = SmokeMemory(factory).failure_history(
        operation_key=current.operation_key,
        failure_id=commit.items[0].failure_id,
    )
    assert history.attempts[0].root_cause == "Final worklist root cause."
    assert history.attempts[0].parameters[0].input_node_id == "path/projectId"
    assert history.attempts[0].generator_change is not None


def test_overlapping_selected_candidates_are_rejected_before_persistence() -> None:
    """Two selected P refs cannot both own the same Generator or Constraint scope."""
    from sqlalchemy import func, select

    from restscope.tools import ToolFailure
    from restscope.db.orm import SmokeFailureORM
    from restscope.operation_smoke.failure_resolution import (
        FailureResolutionFinalizer,
        PatchCandidateRegistry,
    )

    current, factory, session_factory = _database()
    registry = PatchCandidateRegistry()
    first = _candidate(
        registry,
        handle="path.projectId",
        node_id="path/projectId",
        value="known-project",
    )
    second = _candidate(
        registry,
        handle="path.projectId",
        node_id="path/projectId",
        value="another-project",
    )
    worklist = _worklist(
        registry=registry,
        items=[
            _item(item_id="WI-001", outcome="apply_patch", candidate_ref=first.candidate_ref),
            _item(item_id="WI-002", outcome="apply_patch", candidate_ref=second.candidate_ref),
        ],
    )

    with pytest.raises(ToolFailure, match="overlaps"):
        FailureResolutionFinalizer(factory).finalize(
            request=_request(),
            sources=(_source(),),
            worklist=worklist,
            candidates=registry,
            catalog=_catalog(),
            current=current,
            active_constraints=[],
            validate_combined_patch=lambda _patch: None,
        )

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SmokeFailureORM)) == 0


def test_second_patch_event_failure_rolls_back_the_entire_finalization() -> None:
    """A late database failure leaves no Failure, Attempt, event, or state change."""
    from sqlalchemy import func, select

    from restscope.db import SqlAlchemySmokeMemoryUnitOfWork
    from restscope.db.orm import (
        GeneratorChangeEventORM,
        SmokeFailureORM,
        SmokeSolveAttemptORM,
    )
    from restscope.operation_smoke.failure_resolution import (
        FailureResolutionFinalizer,
        PatchCandidateRegistry,
    )

    class FailingSecondEventUnitOfWork(SqlAlchemySmokeMemoryUnitOfWork):
        """Raise only after the first event has been flushed in this transaction."""

        def __enter__(self):
            """Wrap the real event writer while preserving one SQLAlchemy session."""
            value = super().__enter__()
            original = self.generator_configs.record_change_event
            calls = 0

            def fail_second(**arguments):
                """Persist the first event and fail before the second can commit."""
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("second event failed")
                return original(**arguments)

            self.generator_configs.record_change_event = fail_second
            return value

    current, factory, session_factory = _database(
        unit_of_work_class=FailingSecondEventUnitOfWork
    )
    registry = PatchCandidateRegistry()
    path = _candidate(
        registry,
        handle="path.projectId",
        node_id="path/projectId",
        value="known-project",
    )
    region = _candidate(
        registry,
        handle="query.region",
        node_id="query/region",
        value="eu-west",
    )
    worklist = _worklist(
        registry=registry,
        items=[
            _item(item_id="WI-001", outcome="apply_patch", candidate_ref=path.candidate_ref),
            _item(
                item_id="WI-002",
                outcome="apply_patch",
                candidate_ref=region.candidate_ref,
                suspected_parameters=("query.region",),
            ),
        ],
    )

    with pytest.raises(RuntimeError, match="second event failed"):
        FailureResolutionFinalizer(factory).finalize(
            request=_request(),
            sources=(_source(),),
            worklist=worklist,
            candidates=registry,
            catalog=_catalog(),
            current=current,
            active_constraints=[],
            validate_combined_patch=lambda _patch: None,
        )

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(SmokeFailureORM)) == 0
        assert session.scalar(select(func.count()).select_from(SmokeSolveAttemptORM)) == 0
        assert session.scalar(select(func.count()).select_from(GeneratorChangeEventORM)) == 0
    with SqlAlchemySmokeMemoryUnitOfWork(session_factory) as uow:
        stored = uow.generator_configs.get_inputs(current.operation_key)
    assert stored == current.configs
