"""Behavioral contracts for the Operation Smoke Memory Interface."""

from __future__ import annotations


def _memory():
    """Build the production SQLAlchemy Adapter behind the public Memory Interface."""
    from sqlalchemy import create_engine

    from restscope.db import SqlAlchemySmokeMemoryUnitOfWork, make_session_factory
    from restscope.db.base import Base
    from restscope.operation_smoke.memory import SmokeMemory

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return SmokeMemory(lambda: SqlAlchemySmokeMemoryUnitOfWork(session_factory))


def test_plan_memory_reuses_failure_and_supports_many_failure_observation_links() -> None:
    """Scenario: Planner can classify one Observation under multiple stable Failures."""
    from restscope.operation_smoke.memory import (
        FailureClassificationWrite,
        FailureObservationWrite,
        PlanMemoryWrite,
    )

    memory = _memory()
    first = memory.record_plan(
        PlanMemoryWrite(
            operation_key="POST /projects",
            round_number=1,
            batch_run_id="batch-1",
            classifications=[
                FailureClassificationWrite(
                    summary="Project name is rejected.",
                    observations=[
                        FailureObservationWrite(
                            observation_key="case-1",
                            trigger="name is empty",
                            response_summary={"status_code": 400},
                            necessary_values={"body.name": ""},
                        )
                    ],
                ),
                FailureClassificationWrite(
                    summary="Project start date is rejected.",
                    observations=[
                        FailureObservationWrite(
                            observation_key="case-1",
                            trigger="start date is before the supported window",
                            response_summary={"status_code": 400},
                            necessary_values={"body.startDate": "1900-01-01"},
                        )
                    ],
                ),
            ],
        )
    )
    reused = memory.record_plan(
        PlanMemoryWrite(
            operation_key="POST /projects",
            round_number=2,
            batch_run_id="batch-2",
            classifications=[
                FailureClassificationWrite(
                    failure_id=first.failures[0].failure_id,
                    summary="Project name is rejected.",
                    observations=[
                        FailureObservationWrite(
                            observation_key="case-3",
                            trigger="name is empty",
                            response_summary={"status_code": 400},
                            necessary_values={"body.name": ""},
                        )
                    ],
                )
            ],
        )
    )

    catalog = memory.list_operation_failures("POST /projects")

    assert len(catalog) == 2
    assert catalog[0].failure_id == first.failures[0].failure_id
    assert reused.failures[0].failure_id == first.failures[0].failure_id
    assert catalog[0].observation_count == 2
    assert catalog[1].observation_count == 1


def test_investigation_memory_queries_by_failure_and_parameter() -> None:
    """Scenario: Planner and Solve read the same applied knowledge in two directions."""
    from restscope.operation_smoke.memory import (
        AppliedPatchWrite,
        FailureClassificationWrite,
        FailureObservationWrite,
        InvestigationParameterWrite,
        InvestigationWrite,
        PlanMemoryWrite,
    )

    memory = _memory()
    plan = memory.record_plan(
        PlanMemoryWrite(
            operation_key="POST /projects",
            round_number=1,
            batch_run_id="batch-1",
            classifications=[
                FailureClassificationWrite(
                    summary="Project size is outside the accepted range.",
                    observations=[
                        FailureObservationWrite(
                            observation_key="case-1",
                            trigger="size is greater than 100",
                            response_summary={"status_code": 422},
                            necessary_values={"body.size": 101},
                        )
                    ],
                )
            ],
        )
    )
    failure_id = plan.failures[0].failure_id
    memory.record_investigation(
        InvestigationWrite(
            operation_key="POST /projects",
            failure_id=failure_id,
            round_number=1,
            outcome="applied_patch",
            trigger_conditions="size values above 100 are rejected",
            root_cause="body.size uses an unrestricted integer generator",
            solution="generate integers between 3 and 100",
            evidence_source="batch",
            parameters=[
                InvestigationParameterWrite(
                    input_node_id="request/body/properties/size",
                    cause_summary="This input generated the rejected value.",
                )
            ],
            applied_patch=AppliedPatchWrite(
                generator_revision=2,
                patch={"updates": [{"input_node_id": "request/body/properties/size"}]},
                before_generators={"request/body/properties/size": {"type": "integer"}},
                after_generators={
                    "request/body/properties/size": {
                        "type": "integer_range",
                        "minimum": 3,
                        "maximum": 100,
                    }
                },
                samples=[{"body": {"size": 3}}, {"body": {"size": 100}}],
            ),
        )
    )

    failure_history = memory.lookup_failure_history(
        "POST /projects",
        [failure_id],
    )
    parameter_history = memory.lookup_parameter_history(
        "POST /projects",
        ["request/body/properties/size"],
    )

    assert failure_history[0].investigations[0].root_cause.startswith("body.size")
    assert failure_history[0].investigations[0].applied_patch is not None
    assert parameter_history[0].input_node_id == "request/body/properties/size"
    assert parameter_history[0].failures[0].failure_id == failure_id


def test_memory_rejects_failure_references_from_another_operation() -> None:
    """Scenario: request-local aliases cannot cross an operation memory seam."""
    import pytest

    from restscope.operation_smoke.memory import (
        FailureClassificationWrite,
        PlanMemoryWrite,
        SmokeMemoryReferenceError,
    )

    memory = _memory()
    recorded = memory.record_plan(
        PlanMemoryWrite(
            operation_key="GET /projects",
            round_number=1,
            batch_run_id="batch-1",
            classifications=[
                FailureClassificationWrite(
                    summary="Project lookup is rejected.",
                    observations=[],
                    disposition="non_debuggable",
                    disposition_reason="The target requires authorization.",
                )
            ],
        )
    )

    with pytest.raises(SmokeMemoryReferenceError):
        memory.lookup_failure_history(
            "GET /employees",
            [recorded.failures[0].failure_id],
        )


def _atomic_patch_fixture():
    """Build Generator and Memory repositories over one in-memory transaction source."""
    from sqlalchemy import create_engine

    from restscope.db import (
        SqlAlchemySmokeMemoryUnitOfWork,
        make_session_factory,
    )
    from restscope.db.base import Base
    from restscope.operation_smoke.memory import SmokeMemory, SmokePatchApplication

    from tests._operation_smoke_plan_solve_fixtures import smoke_config

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    factory = lambda: SqlAlchemySmokeMemoryUnitOfWork(session_factory)
    with factory() as uow:
        uow.generator_configs.initialize([smoke_config()])
        uow.commit()
    return SmokeMemory(factory), SmokePatchApplication(factory), factory


def _record_patch_failure(memory):
    """Create the stable Failure required by an applied Investigation."""
    from restscope.operation_smoke.memory import (
        FailureClassificationWrite,
        FailureObservationWrite,
        PlanMemoryWrite,
    )

    return memory.record_plan(
        PlanMemoryWrite(
            operation_key="GET /projects/{projectId}",
            round_number=1,
            batch_run_id="batch-1",
            classifications=[
                FailureClassificationWrite(
                    summary="Project identifier is rejected.",
                    observations=[
                        FailureObservationWrite(
                            observation_key="case-1",
                            trigger="identifier returns 404",
                            response_summary={"status_code": 404},
                            necessary_values={"path.projectId": "missing"},
                        )
                    ],
                )
            ],
        )
    ).failures[0].failure_id


def _patch_application_arguments(failure_id):
    """Build one complete direct Patch and its Investigation explanation."""
    from restscope.operation_smoke.memory import (
        InvestigationParameterWrite,
        PatchInvestigation,
    )
    from restscope.operation_smoke.parameter_patch import GeneratorPatchDraft
    from restscope.testing import InputGeneratorPatch
    from restscope.testing.models import ConstantGenerator

    return {
        "expected_revision": 3,
        "patch": GeneratorPatchDraft(
            updates=[
                InputGeneratorPatch(
                    input_node_id="path/projectId",
                    strategy=ConstantGenerator(
                        type="constant",
                        value="known-project",
                    ),
                )
            ]
        ),
        "samples": [{"values": {"path.projectId": "known-project"}}],
        "before_generators": {
            "path.projectId": {"type": "random_string"}
        },
        "after_generators": {
            "path.projectId": {
                "type": "constant",
                "value": "known-project",
            }
        },
        "investigation": PatchInvestigation(
            operation_key="GET /projects/{projectId}",
            failure_id=failure_id,
            round_number=1,
            trigger_conditions="unknown identifiers return 404",
            root_cause="path.projectId uses arbitrary strings",
            solution="use a known project identifier",
            evidence_source="batch",
            parameters=[
                InvestigationParameterWrite(
                    input_node_id="path/projectId",
                    cause_summary="This path value selects the missing project.",
                )
            ],
        ),
    }


def test_patch_application_commits_generator_and_memory_in_one_transaction() -> None:
    """Scenario: an applied Patch and its explanation become visible together."""
    memory, application, factory = _atomic_patch_fixture()
    failure_id = _record_patch_failure(memory)

    applied = application.apply(
        **_patch_application_arguments(failure_id)
    )

    with factory() as uow:
        active = uow.generator_configs.get("GET /projects/{projectId}")
    history = memory.lookup_failure_history(
        "GET /projects/{projectId}",
        [failure_id],
    )[0]
    assert active is not None
    assert active.revision == applied.config.revision == 4
    assert history.investigations[0].investigation_id == applied.investigation_id
    assert history.investigations[0].applied_patch.generator_revision == 4


def test_constraint_only_patch_still_creates_an_accepted_revision() -> None:
    """Scenario: a cross-input rule can be applied without changing a Generator."""
    from restscope.operation_smoke.parameter_patch import (
        CompiledConstraintPatch,
        GeneratorPatchDraft,
    )
    from restscope.testing import ConstraintSet, PresentPredicate

    memory, application, factory = _atomic_patch_fixture()
    failure_id = _record_patch_failure(memory)
    arguments = _patch_application_arguments(failure_id)
    arguments["patch"] = GeneratorPatchDraft(
        constraints=[
            CompiledConstraintPatch(
                constraint_id="constraint_project_id_present",
                kind="present",
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

    applied = application.apply(**arguments)

    with factory() as uow:
        active = uow.generator_configs.get("GET /projects/{projectId}")
    history = memory.lookup_failure_history(
        "GET /projects/{projectId}",
        [failure_id],
    )[0]
    assert active is not None
    assert active.revision == applied.config.revision == 4
    assert active.configs[0].strategy.type == "random_string"
    stored_patch = history.investigations[0].applied_patch
    assert stored_patch is not None
    assert stored_patch.patch["updates"] == []
    assert stored_patch.patch["constraints"][0]["constraint_id"] == (
        "constraint_project_id_present"
    )


def test_patch_application_rolls_back_generator_when_memory_write_fails(
    monkeypatch,
) -> None:
    """Scenario: a Memory Adapter error cannot leave an unexplained revision."""
    import pytest

    from restscope.db.repositories import SqlAlchemySmokeMemoryRepository

    memory, application, factory = _atomic_patch_fixture()
    failure_id = _record_patch_failure(memory)

    def fail_record(_self, _write):
        raise RuntimeError("simulated memory write failure")

    monkeypatch.setattr(
        SqlAlchemySmokeMemoryRepository,
        "record_investigation",
        fail_record,
    )
    with pytest.raises(RuntimeError, match="simulated memory write failure"):
        application.apply(**_patch_application_arguments(failure_id))

    with factory() as uow:
        active = uow.generator_configs.get("GET /projects/{projectId}")
    assert active is not None
    assert active.revision == 3
