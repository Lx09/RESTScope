"""End-to-end ordering and stop contracts for Operation Smoke Coordinator."""

from __future__ import annotations

from types import SimpleNamespace

from restscope.operation_smoke import (
    OperationSmokeCoordinator,
    OperationSmokeRequest,
)
from restscope.operation_smoke.failure_solver import (
    FailureSolveOutcome,
    PatchCandidate,
)
from restscope.operation_smoke.parameter_patch import GeneratorPatchDraft
from restscope.operation_smoke.plan import FailureTodo, SmokeRoundPlan
from restscope.capabilities import ToolContext
from restscope.openapi_parser import OpenAPIParser
from restscope.testing import BatchFailureReport, InputGeneratorPatch
from restscope.testing.models import ConstantGenerator

from tests._operation_smoke_plan_solve_fixtures import smoke_config, smoke_report


def _report(run_id: str, *, status_code: int, revision: int):
    """Build one complete one-case Batch report at an explicit revision."""
    base = smoke_report()
    response = base.cases[0].response.model_copy(
        update={"status_code": status_code}
    )
    case = base.cases[0].model_copy(update={"response": response})
    return base.model_copy(
        update={
            "run_id": run_id,
            "config_revision": revision,
            "cases": [case],
            "status_code_counts": {str(status_code): 1},
            "observed_2xx": 200 <= status_code < 300,
            "failure_report": (
                BatchFailureReport()
                if 200 <= status_code < 300
                else base.failure_report
            ),
        }
    )


class StubCatalog:
    """Expose the active config changed by scripted applied Solve outcomes."""

    def __init__(self) -> None:
        self.current = smoke_config()

    def get_operation(self, operation_key):
        """Return the active Generator config for startup."""
        assert operation_key == self.current.operation_key
        return self.current

    def require_operation(self, operation_key):
        """Return the config after a Solve session atomically applies a Patch."""
        assert operation_key == self.current.operation_key
        return self.current


class StubRunner:
    """Return complete Batches and record their position in the event stream."""

    def __init__(self, reports, events) -> None:
        self.reports = list(reports)
        self.events = events
        self.calls = []

    def run_smoke_batch(self, context, **arguments):
        """Return the next full report plus bounded private evidence."""
        del context
        report = self.reports.pop(0)
        self.events.append(f"batch:{report.run_id}")
        self.calls.append(arguments)
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
    """Return scripted complete Plans and record call ordering."""

    def __init__(self, plans, events) -> None:
        self.plans = list(plans)
        self.events = events
        self.requests = []

    def plan(self, request, *, max_outputs):
        """Return the next Plan and retain its complete Batch request."""
        self.events.append(f"plan:{request.round_number}")
        self.requests.append((request, max_outputs))
        return self.plans.pop(0)


class StubSolveFactory:
    """Create one scripted Solve session per fixed Plan item."""

    def __init__(self, outcomes, events, catalog) -> None:
        self.outcomes = list(outcomes)
        self.events = events
        self.catalog = catalog
        self.requests = []

    def create(self):
        """Return an Agent-like object whose session emits one outcome."""
        owner = self

        class _Agent:
            def start(self, request, **settings):
                owner.requests.append((request, settings))

                class _Session:
                    def advance(self):
                        owner.events.append(
                            f"solve:{request.todo.todo_id}"
                        )
                        outcome = owner.outcomes.pop(0)
                        if outcome.status == "applied_patch":
                            owner.catalog.current = owner.catalog.current.model_copy(
                                update={
                                    "revision": outcome.active_config_revision
                                }
                            )
                        return outcome

                return _Session()

        return _Agent()


class EmptyReferences:
    """Provide no observed-value Generators in Coordinator tests."""

    def values_for(self, strategy):
        """No active fixture strategy uses an observed pool."""
        del strategy
        return []

    def available_options(self, **arguments):
        """Return no optional reference choices."""
        del arguments
        return []

    def prepare_updates(self, *, updates, **arguments):
        """Leave direct Generator updates unchanged."""
        del arguments
        return updates


def _context() -> ToolContext:
    """Build the smallest initialized operation context used by Coordinator."""
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


def _todo(todo_id: str) -> FailureTodo:
    """Build one independent stable Failure item."""
    return FailureTodo(
        todo_id=todo_id,
        failure_id=f"db-{todo_id}",
        failure=f"Failure {todo_id}",
        cases=[{"case_id": "case_1", "response": {"status_code": 404}}],
    )


def _plan(*todo_ids: str, outputs_used: int = 1) -> SmokeRoundPlan:
    """Build one fixed ordered Plan."""
    return SmokeRoundPlan(
        status="planned",
        todos=[_todo(todo_id) for todo_id in todo_ids],
        reason="Investigate every unique Failure.",
        outputs_used=outputs_used,
    )


def _applied(revision: int, value: str) -> FailureSolveOutcome:
    """Build one selected Patch result already committed by Solve runtime."""
    candidate = PatchCandidate(
        candidate_ref="P1",
        patch=GeneratorPatchDraft(
            updates=[
                InputGeneratorPatch(
                    input_node_id="path/projectId",
                    strategy=ConstantGenerator(type="constant", value=value),
                )
            ]
        ),
        before_generators={"path.projectId": {"type": "random_string"}},
        after_generators={"path.projectId": {"type": "constant", "value": value}},
        samples=[{"values": {"path.projectId": value}}],
        patch_outputs=2,
    )
    return FailureSolveOutcome(
        status="applied_patch",
        outputs_used=4,
        investigation_id=f"investigation-{revision}",
        active_config_revision=revision,
        applied_patch=candidate,
    )


def _outcome(status: str) -> FailureSolveOutcome:
    """Build one terminal Investigation that changes no Generator state."""
    return FailureSolveOutcome(
        status=status,
        outputs_used=2,
        investigation_id=f"investigation-{status}",
        active_config_revision=3,
        reason=f"Investigation ended with {status}.",
    )


def _coordinator(*, reports, plans, outcomes, events):
    """Wire one Coordinator with deterministic workflow doubles."""
    catalog = StubCatalog()
    return (
        OperationSmokeCoordinator(
            config_catalog=catalog,
            batch_runner=StubRunner(reports, events),
            plan_agent=StubPlanAgent(plans, events),
            failure_solver_factory=StubSolveFactory(
                outcomes,
                events,
                catalog,
            ),
            reference_values=EmptyReferences(),
            random_seed=731,
        ),
        catalog,
    )


def test_success_rate_stops_before_planner() -> None:
    """Scenario: an 80%+ complete Batch needs no LLM debug work."""
    events = []
    coordinator, _ = _coordinator(
        reports=[_report("batch-1", status_code=200, revision=3)],
        plans=[],
        outcomes=[],
        events=events,
    )

    result = coordinator.run(_context(), OperationSmokeRequest(operation_key=smoke_config().operation_key))

    assert result.status == "passed"
    assert result.stop_reason == "success_rate_reached"
    assert events == ["batch:batch-1"]


def test_planner_no_debug_is_passed_with_actual_unsuccessful_rate() -> None:
    """Scenario: semantic no-debug stop preserves the measured Batch rate."""
    events = []
    coordinator, _ = _coordinator(
        reports=[_report("batch-1", status_code=404, revision=3)],
        plans=[
            SmokeRoundPlan(
                status="no_debug",
                reason="The current Failure needs unavailable credentials.",
                outputs_used=1,
            )
        ],
        outcomes=[],
        events=events,
    )

    result = coordinator.run(_context(), OperationSmokeRequest(operation_key=smoke_config().operation_key))

    assert result.status == "passed"
    assert result.stop_reason == "planner_no_debug"
    assert result.success_rate == 0
    assert result.reason.startswith("The current")


def test_complete_plan_with_no_applied_patch_stops_without_second_batch() -> None:
    """Scenario: no_patch and conflict both finish before no_patch_applied."""
    events = []
    coordinator, _ = _coordinator(
        reports=[_report("batch-1", status_code=404, revision=3)],
        plans=[_plan("T1", "T2")],
        outcomes=[_outcome("no_patch"), _outcome("conflict")],
        events=events,
    )

    result = coordinator.run(_context(), OperationSmokeRequest(operation_key=smoke_config().operation_key))

    assert result.stop_reason == "no_patch_applied"
    assert [item.status for item in result.rounds[0].todos] == [
        "no_patch",
        "conflict",
    ]
    assert events == ["batch:batch-1", "plan:1", "solve:T1", "solve:T2"]


def test_all_plan_items_finish_before_next_complete_batch() -> None:
    """Scenario: multiple sequential Patches never trigger an intermediate Batch."""
    events = []
    coordinator, _ = _coordinator(
        reports=[
            _report("batch-1", status_code=404, revision=3),
            _report("batch-2", status_code=200, revision=5),
        ],
        plans=[_plan("T1", "T2")],
        outcomes=[_applied(4, "first"), _applied(5, "second")],
        events=events,
    )

    result = coordinator.run(_context(), OperationSmokeRequest(operation_key=smoke_config().operation_key))

    assert result.stop_reason == "success_rate_reached"
    assert result.active_config_revision == 5
    assert events == [
        "batch:batch-1",
        "plan:1",
        "solve:T1",
        "solve:T2",
        "batch:batch-2",
    ]


def test_planner_budget_exhaustion_is_technical_error() -> None:
    """Scenario: Planner cannot convert bounded exhaustion into a retry result."""
    events = []
    coordinator, _ = _coordinator(
        reports=[_report("batch-1", status_code=404, revision=3)],
        plans=[
            SmokeRoundPlan(
                status="plan_budget_exhausted",
                reason="No valid classification.",
                outputs_used=1,
            )
        ],
        outcomes=[],
        events=events,
    )

    result = coordinator.run(
        _context(),
        OperationSmokeRequest(
            operation_key=smoke_config().operation_key,
            max_plan_outputs=1,
        ),
    )

    assert result.status == "errored"
    assert result.failure_kind == "plan_budget_exhausted"


def test_solve_budget_exhaustion_is_technical_error() -> None:
    """Scenario: a bounded Investigation failure ends the workflow as errored."""
    events = []
    coordinator, _ = _coordinator(
        reports=[_report("batch-1", status_code=404, revision=3)],
        plans=[_plan("T1")],
        outcomes=[
            FailureSolveOutcome(
                status="solve_budget_exhausted",
                outputs_used=3,
                reason="The Failure Solve output budget was exhausted.",
            )
        ],
        events=events,
    )

    result = coordinator.run(_context(), OperationSmokeRequest(operation_key=smoke_config().operation_key))

    assert result.status == "errored"
    assert result.failure_kind == "solve_budget_exhausted"
