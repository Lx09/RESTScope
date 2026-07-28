"""End-to-end contracts for the thin LLM-led Operation Smoke coordinator."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from restscope.agent.failure_solver import (
    FailureSolveOutcome,
    PatchRequirement,
)
from restscope.agent.operation_smoke import (
    OperationSmokeAgent,
    OperationSmokeRequest,
)
from restscope.agent.operation_smoke.history import OperationSmokeHistory
from restscope.agent.parameter_patch import (
    GeneratorPatchDraft,
    ParameterPatchFailure,
    ValidatedParameterPatch,
)
from restscope.agent.smoke_effect import SmokeEffectOutcome
from restscope.agent.smoke_plan import FailureTodo, SmokeRoundPlan
from restscope.capabilities import ToolContext
from restscope.openapi_parser import OpenAPIParser
from restscope.testing import (
    BatchFailureReport,
    InputGeneratorPatch,
    preview_generator_patch,
)
from tests._operation_smoke_plan_solve_fixtures import smoke_config, smoke_report


def _report(
    run_id: str,
    *,
    status_code: int,
    revision: int,
):
    """Build one aligned complete-batch report."""
    base = smoke_report()
    response = base.cases[0].response.model_copy(
        update={"status_code": status_code}
    )
    case = base.cases[0].model_copy(update={"response": response})
    failure_report = (
        base.failure_report
        if status_code >= 300
        else BatchFailureReport()
    )
    return base.model_copy(
        update={
            "run_id": run_id,
            "config_revision": revision,
            "cases": [case],
            "status_code_counts": {str(status_code): 1},
            "observed_2xx": 200 <= status_code < 300,
            "failure_report": failure_report,
        }
    )


class StubCatalog:
    """Model candidate stage/finalize/rollback without a database."""

    def __init__(self) -> None:
        self.current = smoke_config()
        self.parent = self.current
        self.lifecycle = "accepted"
        self.accepted = 0
        self.rolled_back = 0

    def get_operation(self, operation_key):
        """Return the current frozen config."""
        assert operation_key == self.current.operation_key
        return self.current

    def recover_interrupted_candidate(self, operation_key):
        """No prior interrupted candidate exists in this test."""
        assert operation_key == self.current.operation_key
        return self.current

    def stage_candidate(
        self,
        *,
        operation_key,
        expected_revision,
        updates,
        hypothesis,
    ):
        """Apply the whole generator Patch as one candidate revision."""
        assert operation_key == self.current.operation_key
        assert expected_revision == self.current.revision
        assert hypothesis["kind"] == "operation_smoke_todo_patch"
        self.parent = self.current
        self.current = preview_generator_patch(self.current, updates).model_copy(
            update={"revision": expected_revision + 1}
        )
        self.lifecycle = "candidate"
        return self.current

    def accept_candidate(
        self,
        *,
        operation_key,
        candidate_revision,
        evaluation,
    ):
        """Accept the complete staged Patch."""
        assert operation_key == self.current.operation_key
        assert candidate_revision == self.current.revision
        assert evaluation["validation_status"] == "accepted"
        self.accepted += 1
        self.lifecycle = "accepted"
        return self.current

    def reject_candidate_and_rollback(
        self,
        *,
        operation_key,
        candidate_revision,
        evaluation,
    ):
        """Reject the whole staged Patch and create a rollback revision."""
        assert operation_key == self.current.operation_key
        assert candidate_revision == self.current.revision
        assert evaluation["validation_status"] in {
            "rejected",
            "technical_error",
        }
        self.rolled_back += 1
        self.current = self.parent.model_copy(
            update={"revision": candidate_revision + 1}
        )
        self.lifecycle = "accepted"
        return self.current

class StubRunner:
    """Return prepared complete batches and retain seed/Constraint arguments."""

    def __init__(self, reports) -> None:
        self.reports = list(reports)
        self.calls = []

    def run_smoke_batch(self, context, **arguments):
        """Return the next full report with App-only body evidence."""
        del context
        self.calls.append(arguments)
        report = self.reports.pop(0)
        return SimpleNamespace(
            report=report,
            case_evidence=(
                SimpleNamespace(
                    case_id="case_1",
                    response_body=b'{"error":"project missing"}',
                    response_body_truncated=False,
                    response_encoding="utf-8",
                ),
            ),
        )


class StubPlanAgent:
    """Return prepared round plans and retain complete batch requests."""

    def __init__(self, plans) -> None:
        self.plans = list(plans)
        self.requests = []

    def plan(self, request, *, max_outputs):
        """Return the next Plan decision."""
        self.requests.append((request, max_outputs))
        return self.plans.pop(0)


class StubSolveSession:
    """Return prepared outcomes while proving feedback stays in one session."""

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.feedback = []

    def advance(self, *, feedback=None):
        """Continue the same todo conversation."""
        self.feedback.append(feedback)
        return self.outcomes.pop(0)


class StubSolveFactory:
    """Create one isolated session per todo."""

    def __init__(self, outcome_lists) -> None:
        self.outcome_lists = list(outcome_lists)
        self.requests = []
        self.sessions = []

    def create(self):
        """Return a fresh Agent-like object."""
        factory = self

        class _Agent:
            def start(self, request, **settings):
                factory.requests.append((request, settings))
                session = StubSolveSession(factory.outcome_lists.pop(0))
                factory.sessions.append(session)
                return session

        return _Agent()


class StubPatchFactory:
    """Create fresh Patch Agents and return prepared outcomes."""

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    def create(self):
        """Return a new Agent-like object for one PatchRequirement."""
        factory = self

        class _Agent:
            def run(self, **arguments):
                factory.calls.append(arguments)
                return factory.outcomes.pop(0)

        return _Agent()


class StubEffectAgent:
    """Return prepared atomic candidate decisions."""

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.requests = []

    def validate(self, request, *, max_outputs):
        """Return the next semantic Effect result."""
        self.requests.append((request, max_outputs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class EmptyReferences:
    """Provide no optional reference generators."""

    def values_for(self, strategy):
        """No current config in these tests uses a reference strategy."""
        del strategy
        return []

    def available_options(self, **arguments):
        """Expose the complete empty-reference behavior required by Smoke."""
        del arguments
        return []

    def prepare_updates(self, *, updates, **arguments):
        """Leave non-reference Patch updates unchanged."""
        del arguments
        return updates


def _context() -> ToolContext:
    """Build the smallest real initialized target context used by Smoke."""
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Smoke test", "version": "1"},
            "paths": {
                "/projects/{projectId}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "projectId",
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
    return ToolContext(
        ir=ir,
        baseline_schema_source={
            "kind": "inline",
            "format": "json",
            "content": {},
        },
        base_url="https://api.example.test",
    )


def _todo(todo_id: str = "T1") -> FailureTodo:
    """Build one expanded todo without Plan codes."""
    return FailureTodo(
        todo_id=todo_id,
        failure="Project lookup returns not found.",
        cases=[
            {
                "case_id": "case_1",
                "request": {"path": "/projects/random-123"},
                "response": {"status_code": 404},
            }
        ],
    )


def _requirement() -> PatchRequirement:
    """Build one parameter root cause owned by Failure Solve."""
    return PatchRequirement(
        root_cause="The generated project identifier does not exist.",
        affected_inputs=["path.projectId"],
        desired_behavior="Generate an observed existing identifier.",
        acceptance_criteria="The aligned case returns 2xx.",
    )


def _validated_patch(*, outputs: int = 2) -> ValidatedParameterPatch:
    """Build one locally compiled and reviewed Generator Patch."""
    return ValidatedParameterPatch(
        todo_id="T1",
        patch=GeneratorPatchDraft(
            updates=[
                InputGeneratorPatch(
                    input_node_id="path/projectId",
                    strategy={"type": "constant", "value": "known-project"},
                )
            ]
        ),
        samples=[{"path": {"projectId": "known-project"}}],
        outputs_used=outputs,
    )


def _validated_constraint_patch() -> ValidatedParameterPatch:
    """Build one locally validated runtime-only presence Constraint."""
    from restscope.agent.parameter_patch import CompiledConstraintPatch
    from restscope.testing import ConstraintSet

    return ValidatedParameterPatch(
        todo_id="T1",
        patch=GeneratorPatchDraft(
            constraints=[
                CompiledConstraintPatch(
                    constraint_id="constraint-region-present",
                    kind="presence",
                    constraint=ConstraintSet(
                        constraints=[
                            {
                                "type": "present",
                                "input_node_id": "query/region",
                            }
                        ]
                    ),
                )
            ]
        ),
        samples=[{"query": {"region": "us-east"}}],
        outputs_used=2,
    )


def _agent(
    *,
    reports,
    plans,
    solve_outcomes=(),
    patch_outcomes=(),
    effects=(),
    history=None,
):
    """Assemble the coordinator entirely from offline stubs."""
    catalog = StubCatalog()
    runner = StubRunner(reports)
    plan = StubPlanAgent(plans)
    solve = StubSolveFactory(solve_outcomes)
    patch = StubPatchFactory(patch_outcomes)
    effect = StubEffectAgent(effects)
    agent = OperationSmokeAgent(
        config_catalog=catalog,
        batch_runner=runner,
        plan_agent=plan,
        failure_solver_factory=solve,
        patch_agent_factory=patch,
        effect_agent=effect,
        reference_values=EmptyReferences(),
        history=history,
    )
    return agent, catalog, runner, plan, solve, patch, effect


def test_complete_baseline_can_pass_without_any_plan_output() -> None:
    """A passing latest batch is the sole success gate."""
    agent, _, runner, plan, *_ = _agent(
        reports=[_report("baseline", status_code=200, revision=3)],
        plans=[],
    )

    result = agent.run(
        _context(),
        OperationSmokeRequest(operation_key="GET /projects/{projectId}", seed=7),
    )

    assert result.status == "passed"
    assert result.rounds == []
    assert plan.requests == []
    assert runner.calls[0]["seed"] == 7


def test_patch_is_atomically_accepted_then_next_round_uses_same_seed() -> None:
    """Resolved Effect accepts the whole Patch and reruns a fresh full batch."""
    requirement = _requirement()
    agent, catalog, runner, _, solve, patch, effect = _agent(
        reports=[
            _report("before", status_code=404, revision=3),
            _report("candidate", status_code=200, revision=4),
            _report("next-round", status_code=200, revision=4),
        ],
        plans=[
            SmokeRoundPlan(
                status="planned",
                todos=[_todo()],
                reason="One distinct failure.",
                outputs_used=1,
            )
        ],
        solve_outcomes=[
            [
                FailureSolveOutcome(
                    status="patch_ready",
                    outputs_used=1,
                    patch_requirement=requirement,
                )
            ]
        ],
        patch_outcomes=[_validated_patch()],
        effects=[
            SmokeEffectOutcome(
                outcome="resolved_without_regression",
                reason="The aligned failure is gone.",
                outputs_used=1,
            )
        ],
    )

    result = agent.run(
        _context(),
        OperationSmokeRequest(operation_key="GET /projects/{projectId}", seed=9),
    )

    assert result.status == "passed"
    assert [call["seed"] for call in runner.calls] == [9, 9, 9]
    assert catalog.accepted == 1
    assert catalog.rolled_back == 0
    assert result.rounds[0].todos[0].status == "resolved"
    assert result.rounds[0].todos[0].patch_attempts[0].accepted is True
    assert solve.requests[0][1]["max_outputs"] == 50
    assert patch.calls[0]["case_count"] == 10
    assert effect.requests[0][0].before_batch["run"]["run_id"] == "before"
    assert effect.requests[0][0].candidate_batch["run"]["run_id"] == "candidate"


def test_unresolved_candidate_rolls_back_and_returns_to_same_solve_session() -> None:
    """Rejected Effect feedback resumes the todo rather than creating a new Solve."""
    requirement = _requirement()
    agent, catalog, _, _, solve, _, _ = _agent(
        reports=[
            _report("before", status_code=404, revision=3),
            _report("candidate", status_code=404, revision=4),
            _report("after-round", status_code=404, revision=5),
        ],
        plans=[
            SmokeRoundPlan(
                status="planned",
                todos=[_todo()],
                reason="Investigate.",
                outputs_used=1,
            ),
            SmokeRoundPlan(
                status="no_new_failure_work",
                reason="The recorded attempt has no new direction.",
                outputs_used=1,
            ),
        ],
        solve_outcomes=[
            [
                FailureSolveOutcome(
                    status="patch_ready",
                    outputs_used=1,
                    patch_requirement=requirement,
                ),
                FailureSolveOutcome(
                    status="no_new_attempt",
                    outputs_used=2,
                    reason="No distinct next attempt remains.",
                ),
            ]
        ],
        patch_outcomes=[_validated_patch()],
        effects=[
            SmokeEffectOutcome(
                outcome="unresolved",
                reason="The same 404 remains.",
                outputs_used=1,
            )
        ],
    )

    result = agent.run(
        _context(),
        OperationSmokeRequest(operation_key="GET /projects/{projectId}", seed=4),
    )

    assert result.status == "retry"
    assert result.failure_kind == "no_new_failure_work"
    assert catalog.accepted == 0
    assert catalog.rolled_back == 1
    assert len(solve.sessions) == 1
    feedback = solve.sessions[0].feedback[1]
    assert feedback["effect"]["outcome"] == "unresolved"
    assert feedback["candidate_batch"]["run"]["run_id"] == "candidate"


def test_remaining_todo_receives_latest_accepted_candidate_batch() -> None:
    """A round snapshot is fixed, but later todos see earlier accepted effects."""
    requirement = _requirement()
    second = _todo("T2").model_copy(
        update={"failure": "A second planned failure."}
    )
    agent, _, _, _, solve, _, _ = _agent(
        reports=[
            _report("before", status_code=404, revision=3),
            _report("candidate", status_code=200, revision=4),
            _report("next-round", status_code=200, revision=4),
        ],
        plans=[
            SmokeRoundPlan(
                status="planned",
                todos=[_todo(), second],
                reason="Two fixed todos.",
                outputs_used=1,
            )
        ],
        solve_outcomes=[
            [
                FailureSolveOutcome(
                    status="patch_ready",
                    outputs_used=1,
                    patch_requirement=requirement,
                )
            ],
            [
                FailureSolveOutcome(
                    status="already_absent",
                    outputs_used=1,
                    reason="The latest batch no longer contains it.",
                )
            ],
        ],
        patch_outcomes=[_validated_patch()],
        effects=[
            SmokeEffectOutcome(
                outcome="resolved_without_regression",
                reason="Resolved.",
                outputs_used=1,
            )
        ],
    )

    result = agent.run(
        _context(),
        OperationSmokeRequest(operation_key="GET /projects/{projectId}", seed=2),
    )

    assert result.status == "passed"
    assert solve.requests[1][0].current_batch["run"]["run_id"] == "candidate"
    assert result.rounds[0].todos[1].status == "already_absent"


def test_plan_budget_exhaustion_is_the_only_other_retry_stop_reason() -> None:
    """Invalid Plan outputs consume the global Plan budget and stop cleanly."""
    agent, *_ = _agent(
        reports=[_report("before", status_code=404, revision=3)],
        plans=[
            SmokeRoundPlan(
                status="plan_budget_exhausted",
                reason="All outputs were invalid.",
                outputs_used=50,
            )
        ],
    )

    result = agent.run(
        _context(),
        OperationSmokeRequest(operation_key="GET /projects/{projectId}"),
    )

    assert result.status == "retry"
    assert result.failure_kind == "plan_budget_exhausted"
    assert result.rounds[0].plan_outputs == 50


def test_planned_output_that_consumes_last_plan_turn_stops_before_another_call() -> None:
    """A valid 50th Plan output may finish its todos but cannot start Plan 51."""
    agent, _, _, plan, *_ = _agent(
        reports=[
            _report("before", status_code=404, revision=3),
            _report("after-round", status_code=404, revision=3),
        ],
        plans=[
            SmokeRoundPlan(
                status="planned",
                todos=[_todo()],
                reason="Use the last available Plan output.",
                outputs_used=50,
            )
        ],
        solve_outcomes=[
            [
                FailureSolveOutcome(
                    status="no_new_attempt",
                    outputs_used=1,
                    reason="No distinct parameter attempt remains.",
                )
            ]
        ],
    )

    result = agent.run(
        _context(),
        OperationSmokeRequest(operation_key="GET /projects/{projectId}"),
    )

    assert result.status == "retry"
    assert result.failure_kind == "plan_budget_exhausted"
    assert len(plan.requests) == 1


def test_accepted_constraints_survive_a_later_smoke_retry_in_same_app() -> None:
    """App-lifetime Constraints are reused but never added to public result data."""
    from restscope.agent.parameter_patch import CompiledConstraintPatch
    from restscope.testing import ConstraintSet

    history = OperationSmokeHistory()
    history.state_for("GET /projects/{projectId}").accepted_constraints["c1"] = (
        CompiledConstraintPatch(
            constraint_id="c1",
            kind="presence",
            constraint=ConstraintSet(
                constraints=[
                    {
                        "type": "present",
                        "input_node_id": "query/region",
                    }
                ]
            ),
        )
    )
    agent, _, runner, *_ = _agent(
        reports=[_report("passing", status_code=200, revision=3)],
        plans=[],
        history=history,
    )

    result = agent.run(
        _context(),
        OperationSmokeRequest(operation_key="GET /projects/{projectId}"),
    )

    assert result.status == "passed"
    assert runner.calls[0]["constraints"] is not None
    assert "accepted_constraints" not in result.model_dump(mode="json")


def test_flow_accepted_constraint_is_applied_on_next_supervisor_retry() -> None:
    """A real accepted Constraint remains active across separate ``run`` calls."""
    agent, _, runner, _, _, _, _ = _agent(
        reports=[
            _report("before", status_code=404, revision=3),
            _report("candidate", status_code=404, revision=3),
            _report("after-round", status_code=404, revision=3),
            _report("supervisor-retry", status_code=200, revision=3),
        ],
        plans=[
            SmokeRoundPlan(
                status="planned",
                todos=[_todo()],
                reason="Investigate a relationship.",
                outputs_used=1,
            ),
            SmokeRoundPlan(
                status="no_new_failure_work",
                reason="Only a different failure remains.",
                outputs_used=1,
            ),
        ],
        solve_outcomes=[
            [
                FailureSolveOutcome(
                    status="patch_ready",
                    outputs_used=1,
                    patch_requirement=_requirement(),
                )
            ]
        ],
        patch_outcomes=[_validated_constraint_patch()],
        effects=[
            SmokeEffectOutcome(
                outcome="resolved_without_regression",
                reason="The target relationship failure is gone.",
                outputs_used=1,
            )
        ],
    )
    request = OperationSmokeRequest(
        operation_key="GET /projects/{projectId}",
        seed=15,
    )

    first = agent.run(_context(), request)
    second = agent.run(_context(), request)

    assert first.status == "retry"
    assert second.status == "passed"
    assert runner.calls[0].get("constraints") is None
    assert all(
        call.get("constraints") is not None
        for call in runner.calls[1:]
    )


def test_technical_error_rolls_back_a_staged_candidate() -> None:
    """An Effect infrastructure failure cannot leave a candidate revision active."""
    agent, catalog, *_ = _agent(
        reports=[
            _report("before", status_code=404, revision=3),
            _report("candidate", status_code=404, revision=4),
        ],
        plans=[
            SmokeRoundPlan(
                status="planned",
                todos=[_todo()],
                reason="Investigate.",
                outputs_used=1,
            )
        ],
        solve_outcomes=[
            [
                FailureSolveOutcome(
                    status="patch_ready",
                    outputs_used=1,
                    patch_requirement=_requirement(),
                )
            ]
        ],
        patch_outcomes=[_validated_patch()],
        effects=[RuntimeError("effect runtime unavailable")],
    )

    result = agent.run(
        _context(),
        OperationSmokeRequest(operation_key="GET /projects/{projectId}"),
    )

    assert result.status == "errored"
    assert result.failure_kind == "operation_error"
    assert catalog.rolled_back == 1
    assert catalog.lifecycle == "accepted"


def test_database_error_rolls_back_candidate_then_propagates_to_supervisor() -> None:
    """Database-class failures remain global technical errors after cleanup."""
    from sqlalchemy.exc import SQLAlchemyError

    agent, catalog, *_ = _agent(
        reports=[
            _report("before", status_code=404, revision=3),
            _report("candidate", status_code=404, revision=4),
        ],
        plans=[
            SmokeRoundPlan(
                status="planned",
                todos=[_todo()],
                reason="Investigate.",
                outputs_used=1,
            )
        ],
        solve_outcomes=[
            [
                FailureSolveOutcome(
                    status="patch_ready",
                    outputs_used=1,
                    patch_requirement=_requirement(),
                )
            ]
        ],
        patch_outcomes=[_validated_patch()],
        effects=[SQLAlchemyError("database unavailable")],
    )

    with pytest.raises(SQLAlchemyError):
        agent.run(
            _context(),
            OperationSmokeRequest(operation_key="GET /projects/{projectId}"),
        )

    assert catalog.rolled_back == 1
    assert catalog.lifecycle == "accepted"


def test_app_state_clear_releases_raw_history_and_constraints() -> None:
    """App shutdown removes all non-persistent Smoke memory."""
    history = OperationSmokeHistory()
    history.record(
        "GET /projects/{projectId}",
        {"response": {"body": "sensitive-target-data"}},
    )
    agent, *_ = _agent(
        reports=[],
        plans=[],
        history=history,
    )

    agent.clear_app_state()

    assert history.snapshot("GET /projects/{projectId}") == []
    assert history.state_for(
        "GET /projects/{projectId}"
    ).accepted_constraints == {}
