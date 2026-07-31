"""End-to-end ordering and stop contracts for Operation Smoke Coordinator."""

from __future__ import annotations

from dataclasses import replace

from restscope.capabilities import ToolContext
from restscope.openapi_parser import OpenAPIParser
from restscope.operation_smoke import (
    OperationSmokeCoordinator,
    OperationSmokeRequest,
)
from restscope.operation_smoke.failure_dedup import (
    FailureDedupResult,
    FailureTodo,
)
from restscope.operation_smoke.failure_solver import (
    FailureSolveOutcome,
    PatchCandidate,
)
from restscope.operation_smoke.parameter_patch import GeneratorPatchDraft
from restscope.testing import InputGeneratorPatch
from restscope.testing.models import ConstantGenerator
from tests._operation_smoke_dedup_solve_fixtures import smoke_config, smoke_report


def _report(run_id: str, *, status_code: int, revision: int):
    """Build one complete single-case Batch result."""
    from restscope.operation_smoke.test_case_catalog import HTTPFailure

    base = smoke_report()
    failure = (
        None
        if 200 <= status_code < 300
        else HTTPFailure(
            status_code=status_code,
            messages=[f"HTTP {status_code}: project missing"],
        )
    )
    return replace(
        base,
        run_id=run_id,
        config_revision=revision,
        cases=(
            base.cases[0].model_copy(
                update={
                    "response_body": (
                        None
                        if failure is None
                        else {"message": "project missing"}
                    ),
                    "failure": failure,
                }
            ),
        ),
    )


class StubCatalog:
    """Expose active Generator state changed by scripted applied Patches."""

    def __init__(self) -> None:
        self.current = smoke_config()

    def get_operation(self, operation_key):
        assert operation_key == self.current.operation_key
        return self.current

    def require_operation(self, operation_key):
        assert operation_key == self.current.operation_key
        return self.current


class StubRunner:
    """Return complete Batches and record workflow ordering."""

    def __init__(self, reports, events) -> None:
        self.reports = list(reports)
        self.events = events

    def run_smoke_batch(self, context, **arguments):
        del context
        batch = self.reports.pop(0)
        case_id_factory = arguments["case_id_factory"]
        batch = replace(
            batch,
            cases=(
                batch.cases[0].model_copy(
                    update={"case_id": case_id_factory()}
                ),
            ),
        )
        self.events.append(f"batch:{batch.run_id}")
        return batch


class StubDeduplicator:
    """Return fixed current-round Failure Todos."""

    def __init__(self, results, events) -> None:
        self.results = list(results)
        self.events = events
        self.requests = []

    def deduplicate(self, request, *, catalog, max_outputs):
        del catalog
        self.events.append(f"dedup:{request.round_number}")
        self.requests.append((request, max_outputs))
        return self.results.pop(0)


class StubSolveFactory:
    """Create one scripted Solve session per Failure Todo."""

    def __init__(self, outcomes, events, catalog) -> None:
        self.outcomes = list(outcomes)
        self.events = events
        self.catalog = catalog
        self.requests = []

    def create(self):
        owner = self

        class _Agent:
            def start(self, request, **settings):
                owner.requests.append((request, settings))

                class _Session:
                    def advance(self):
                        owner.events.append(f"solve:{request.todo.todo_id}")
                        outcome = owner.outcomes.pop(0)
                        if outcome.status == "applied_patch":
                            owner.catalog.current = (
                                owner.catalog.current.model_copy(
                                    update={
                                        "revision": outcome.active_config_revision
                                    }
                                )
                            )
                        return outcome

                return _Session()

        return _Agent()


class EmptyReferences:
    """Provide no observed-value Generator pools."""

    def values_for(self, strategy):
        del strategy
        return []

    def available_options(self, **arguments):
        del arguments
        return []

    def prepare_updates(self, *, updates, **arguments):
        del arguments
        return updates


def _context() -> ToolContext:
    """Build the minimal initialized operation context."""
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Smoke", "version": "1"},
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
        baseline_schema_source={"kind": "inline", "format": "json", "content": {}},
        base_url="https://api.example.test",
    )


def _todo(todo_id: str) -> FailureTodo:
    """Build one stable Failure carrying exactly one test case."""
    return FailureTodo(
        todo_id=todo_id,
        failure_id=f"db-{todo_id}",
        failure=f"Failure {todo_id}",
        test_case_id="TC1",
        suspected_parameters=["path.projectId"],
    )


def _dedup(*todo_ids: str, outputs_used: int = 1) -> FailureDedupResult:
    """Build one successful semantic Dedup result."""
    return FailureDedupResult(
        status="deduplicated",
        todos=[_todo(todo_id) for todo_id in todo_ids],
        reason="Every distinct Failure appears once.",
        outputs_used=outputs_used,
        exact_fingerprint_count=max(2, len(todo_ids)),
    )


def _applied(revision: int, value: str) -> FailureSolveOutcome:
    """Build one selected Patch result already committed by Solve."""
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
    """Build one terminal Investigation that applies no Patch."""
    return FailureSolveOutcome(
        status=status,
        outputs_used=2,
        investigation_id=f"investigation-{status}",
        active_config_revision=3,
        reason=f"Investigation ended with {status}.",
    )


def _coordinator(*, reports, dedup_results, outcomes, events):
    """Wire one Coordinator through its approved seams."""
    catalog = StubCatalog()
    deduplicator = StubDeduplicator(dedup_results, events)
    solve_factory = StubSolveFactory(outcomes, events, catalog)
    return (
        OperationSmokeCoordinator(
            config_catalog=catalog,
            batch_runner=StubRunner(reports, events),
            failure_deduplicator=deduplicator,
            failure_solver_factory=solve_factory,
            reference_values=EmptyReferences(),
            random_seed=731,
        ),
        catalog,
        deduplicator,
        solve_factory,
    )


def test_success_rate_stops_before_dedup() -> None:
    """A successful complete Batch needs no Failure work."""
    events = []
    coordinator, _, _, _ = _coordinator(
        reports=[_report("batch-1", status_code=200, revision=3)],
        dedup_results=[],
        outcomes=[],
        events=events,
    )

    result = coordinator.run(
        _context(),
        OperationSmokeRequest(operation_key=smoke_config().operation_key),
    )

    assert result.stop_reason == "success_rate_reached"
    assert events == ["batch:batch-1"]


def test_no_applied_patch_stops_after_every_unique_failure() -> None:
    """All deduplicated Failures finish before the no-Patch stop."""
    events = []
    coordinator, _, _, solve_factory = _coordinator(
        reports=[_report("batch-1", status_code=404, revision=3)],
        dedup_results=[_dedup("T1", "T2")],
        outcomes=[_outcome("no_patch"), _outcome("conflict")],
        events=events,
    )

    result = coordinator.run(
        _context(),
        OperationSmokeRequest(operation_key=smoke_config().operation_key),
    )

    assert result.stop_reason == "no_patch_applied"
    assert events == [
        "batch:batch-1",
        "dedup:1",
        "solve:T1",
        "solve:T2",
    ]
    assert all(
        not hasattr(request, "current_batch")
        for request, _settings in solve_factory.requests
    )


def test_every_failure_finishes_before_next_complete_batch() -> None:
    """Sequential Patches never cause an intermediate Batch."""
    events = []
    coordinator, _, _, _ = _coordinator(
        reports=[
            _report("batch-1", status_code=404, revision=3),
            _report("batch-2", status_code=200, revision=5),
        ],
        dedup_results=[_dedup("T1", "T2")],
        outcomes=[_applied(4, "first"), _applied(5, "second")],
        events=events,
    )

    result = coordinator.run(
        _context(),
        OperationSmokeRequest(operation_key=smoke_config().operation_key),
    )

    assert result.active_config_revision == 5
    assert events == [
        "batch:batch-1",
        "dedup:1",
        "solve:T1",
        "solve:T2",
        "batch:batch-2",
    ]


def test_dedup_budget_exhaustion_is_a_technical_error() -> None:
    """An unusable semantic classification cannot become a passed result."""
    events = []
    coordinator, _, _, _ = _coordinator(
        reports=[_report("batch-1", status_code=404, revision=3)],
        dedup_results=[
            FailureDedupResult(
                status="dedup_budget_exhausted",
                reason="No valid classification.",
                outputs_used=1,
                exact_fingerprint_count=2,
            )
        ],
        outcomes=[],
        events=events,
    )

    result = coordinator.run(
        _context(),
        OperationSmokeRequest(
            operation_key=smoke_config().operation_key,
            max_dedup_outputs=1,
        ),
    )

    assert result.status == "errored"
    assert result.failure_kind == "dedup_budget_exhausted"


def test_solve_budget_exhaustion_is_a_technical_error() -> None:
    """One exhausted Investigation ends the workflow."""
    coordinator, _, _, _ = _coordinator(
        reports=[_report("batch-1", status_code=404, revision=3)],
        dedup_results=[_dedup("T1")],
        outcomes=[
            FailureSolveOutcome(
                status="solve_budget_exhausted",
                outputs_used=3,
                reason="The Solve budget was exhausted.",
            )
        ],
        events=[],
    )

    result = coordinator.run(
        _context(),
        OperationSmokeRequest(operation_key=smoke_config().operation_key),
    )

    assert result.status == "errored"
    assert result.failure_kind == "solve_budget_exhausted"
