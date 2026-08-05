"""End-to-end ordering and stop contracts for Operation Smoke Coordinator."""

from __future__ import annotations

from dataclasses import replace

from restscope.capabilities import ToolContext
from restscope.openapi_parser import OpenAPIParser
from restscope.operation_smoke import OperationSmokeCoordinator, OperationSmokeRequest
from restscope.operation_smoke.failure_resolution import (
    FailureResolutionOutcome,
    FailureWorklist,
    ResolutionCommit,
    ResolutionItemCommit,
    WorklistDecision,
    WorklistItem,
)
from tests._operation_smoke_resolution_fixtures import smoke_config, smoke_report


def _report(run_id: str, *, status_code: int):
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
        cases=(
            base.cases[0].model_copy(
                update={
                    "response_body": (
                        None if failure is None else {"message": "project missing"}
                    ),
                    "failure": failure,
                }
            ),
        ),
    )


class StubCatalog:
    """Expose durable Generator state reloaded after Resolution commits."""

    def __init__(self) -> None:
        """Start from the shared operation fixture's Generator configuration."""
        self.current = smoke_config()

    def get_operation(self, operation_key):
        """Return current state only for the expected operation key."""
        assert operation_key == self.current.operation_key
        return self.current

    def require_operation(self, operation_key):
        """Reload the state that a scripted Resolution commit may have changed."""
        assert operation_key == self.current.operation_key
        return self.current


class StubRunner:
    """Return complete Batches and record workflow ordering."""

    def __init__(self, reports, events) -> None:
        """Store scripted reports in their expected execution order."""
        self.reports = list(reports)
        self.events = events

    def run_smoke_batch(self, context, **arguments):
        """Assign the Catalog's next TC reference before returning one Batch."""
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


class StubResolutionAgent:
    """Return one scripted continuous-session outcome per failed Batch."""

    def __init__(self, outcomes, events, catalog) -> None:
        """Retain outcomes, session settings, and the shared current-state stub."""
        self.outcomes = list(outcomes)
        self.events = events
        self.catalog = catalog
        self.starts = []

    def start(self, request, **settings):
        """Capture one failed-Batch session without inspecting semantic contents."""
        self.starts.append((request, settings))
        owner = self

        class _Session:
            """Advance exactly one scripted continuous Resolution session."""

            def advance(self):
                """Return or raise the next scripted result."""
                owner.events.append(f"resolution:{request.round_number}")
                result = owner.outcomes.pop(0)
                if isinstance(result, Exception):
                    raise result
                if (
                    result.commit is not None
                    and result.commit.applied_candidate_refs
                ):
                    from restscope.testing import preview_generator_patch
                    from restscope.testing import InputGeneratorPatch
                    from restscope.testing.models import ConstantGenerator

                    owner.catalog.current = preview_generator_patch(
                        owner.catalog.current,
                        [
                            InputGeneratorPatch(
                                input_node_id="path/projectId",
                                strategy=ConstantGenerator(
                                    type="constant",
                                    value="known-project",
                                ),
                            )
                        ],
                    )
                return result

        return _Session()


class EmptyReferences:
    """Provide no observed-value Generator pools for ordinary fixture strategies."""

    def values_for(self, strategy):
        """Return no values; the fixture uses no reference-backed Generator."""
        del strategy
        return []

    def prepare_updates(self, *, updates, **arguments):
        """Return ordinary Generator updates unchanged."""
        del arguments
        return updates


class EmptyConstraintReader:
    """Return durable-current Constraints at each complete Batch boundary."""

    def __init__(self) -> None:
        """Retain operation reads to show state is refreshed each round."""
        self.reads = []

    def current_constraints(self, operation_key):
        """Record the lookup and return an empty complete Constraint set."""
        self.reads.append(operation_key)
        return []


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


def _resolution(*, applied: bool, outputs_used: int = 4):
    """Build one finalized continuous-session outcome with one decided item."""
    decision = WorklistDecision(
        outcome="apply_patch" if applied else "no_patch",
        selected_candidate_ref="P1" if applied else None,
        reason=(
            "Apply the reviewed candidate."
            if applied
            else "No safe Patch is supported."
        ),
    )
    item = WorklistItem(
        item_id="missing-project",
        source_failure_refs=["E1"],
        test_case_refs=["TC1"],
        suspected_parameters=["path.projectId"],
        progress="Investigation complete.",
        root_cause="The generated identifier does not exist.",
        candidate_refs=["P1"] if applied else [],
        decision=decision,
    )
    committed = ResolutionItemCommit(
        item_id=item.item_id,
        failure_summary="HTTP 404: project missing",
        outcome=decision.outcome,
        failure_id="failure-1",
        attempt_id="attempt-1",
        candidate_ref="P1" if applied else None,
        generator_change_event_id="event-1" if applied else None,
        patch_outputs=2 if applied else None,
        changed_input_count=1 if applied else None,
        constraint_count=0 if applied else None,
    )
    return FailureResolutionOutcome(
        status="completed",
        outputs_used=outputs_used,
        source_count=1,
        worklist=FailureWorklist(revision=1, items=[item]),
        commit=ResolutionCommit(
            items=[committed],
            attempt_ids=[committed.attempt_id],
            generator_change_event_ids=(
                [committed.generator_change_event_id] if applied else []
            ),
            applied_candidate_refs=["P1"] if applied else [],
        ),
        reason="The worklist is ready.",
    )


def _limit_outcome():
    """Build the one approved model-output hard-stop result."""
    return FailureResolutionOutcome(
        status="failure_resolution_limit_exceeded",
        outputs_used=1_000,
        source_count=1,
        worklist=FailureWorklist(revision=0),
        reason="Operation Smoke reached its hard limit of 1000 model outputs.",
    )


def _coordinator(*, reports, outcomes, events):
    """Wire one Coordinator through the new continuous Resolution seam."""
    catalog = StubCatalog()
    agent = StubResolutionAgent(outcomes, events, catalog)
    return (
        OperationSmokeCoordinator(
            config_catalog=catalog,
            batch_runner=StubRunner(reports, events),
            failure_resolution_agent=agent,
            constraint_reader=EmptyConstraintReader(),
            reference_values=EmptyReferences(),
            random_seed=731,
        ),
        catalog,
        agent,
    )


def test_success_rate_stops_before_failure_resolution() -> None:
    """A successful complete Batch needs no Agent output."""
    events = []
    coordinator, _catalog, agent = _coordinator(
        reports=[_report("batch-1", status_code=200)],
        outcomes=[],
        events=events,
    )

    result = coordinator.run(
        _context(),
        OperationSmokeRequest(operation_key=smoke_config().operation_key),
    )

    assert result.stop_reason == "success_rate_reached"
    assert events == ["batch:batch-1"]
    assert agent.starts == []


def test_no_applied_patch_stops_after_one_continuous_resolution() -> None:
    """A failed Batch is semantically grouped and concluded inside one session."""
    events = []
    coordinator, _catalog, agent = _coordinator(
        reports=[_report("batch-1", status_code=404)],
        outcomes=[_resolution(applied=False)],
        events=events,
    )

    result = coordinator.run(
        _context(),
        OperationSmokeRequest(operation_key=smoke_config().operation_key),
    )

    assert result.stop_reason == "no_patch_applied"
    assert events == ["batch:batch-1", "resolution:1"]
    assert len(agent.starts) == 1
    assert agent.starts[0][0].case_ids == ["TC1"]
    assert result.rounds[0].resolution_outputs == 4
    assert result.rounds[0].items[0].outcome == "no_patch"


def test_all_selected_candidates_commit_before_the_next_complete_batch() -> None:
    """One atomic Resolution finalization precedes the next Batch boundary."""
    events = []
    coordinator, _catalog, agent = _coordinator(
        reports=[
            _report("batch-1", status_code=404),
            _report("batch-2", status_code=200),
        ],
        outcomes=[_resolution(applied=True)],
        events=events,
    )

    result = coordinator.run(
        _context(),
        OperationSmokeRequest(operation_key=smoke_config().operation_key),
    )

    assert result.stop_reason == "success_rate_reached"
    assert events == ["batch:batch-1", "resolution:1", "batch:batch-2"]
    assert result.rounds[0].items[0].applied_patch.candidate_ref == "P1"
    assert agent.starts[0][1]["output_limit"].max_outputs == 1_000


def test_failure_resolution_hard_limit_is_the_only_agent_budget_error() -> None:
    """An unfinished worklist produces no round commit or legacy budget kind."""
    coordinator, _catalog, _agent = _coordinator(
        reports=[_report("batch-1", status_code=404)],
        outcomes=[_limit_outcome()],
        events=[],
    )

    result = coordinator.run(
        _context(),
        OperationSmokeRequest(operation_key=smoke_config().operation_key),
    )

    assert result.status == "errored"
    assert result.failure_kind == "failure_resolution_limit_exceeded"
    assert result.rounds == []


def test_provider_unavailable_preserves_completed_batch_and_round_evidence() -> None:
    """A later provider outage retains the prior committed Resolution summary."""
    from restscope.llm import ProviderUnavailableError

    coordinator, _catalog, agent = _coordinator(
        reports=[
            _report("batch-1", status_code=404),
            _report("batch-2", status_code=404),
        ],
        outcomes=[
            _resolution(applied=True),
            ProviderUnavailableError(status_code=503, retry_limit=3),
        ],
        events=[],
    )

    result = coordinator.run(
        _context(),
        OperationSmokeRequest(operation_key=smoke_config().operation_key),
    )

    assert result.status == "errored"
    assert result.failure_kind == "provider_unavailable"
    assert result.batch_run_ids == ["batch-1", "batch-2"]
    assert [round_.batch_run_id for round_ in result.rounds] == ["batch-1"]
    assert agent.starts[0][1]["output_limit"] is agent.starts[1][1]["output_limit"]


def test_public_contract_rejects_removed_agent_budgets_and_old_summary_names() -> None:
    """The public seam exposes Resolution names without compatibility aliases."""
    import pytest
    from pydantic import ValidationError

    import restscope.operation_smoke as operation_smoke
    from restscope.operation_smoke import ResolutionItemSummary

    with pytest.raises(ValidationError, match="max_dedup_outputs"):
        OperationSmokeRequest.model_validate(
            {
                "operation_key": smoke_config().operation_key,
                "max_dedup_outputs": 1,
            }
        )
    with pytest.raises(ValidationError, match="todo_id"):
        ResolutionItemSummary.model_validate(
            {
                "item_id": "item-1",
                "failure_summary": "Missing project",
                "outcome": "no_patch",
                "attempt_id": "attempt-1",
                "reason": "No safe Patch.",
                "todo_id": "removed-name",
            }
        )
    assert not hasattr(operation_smoke, "PatchAttemptSummary")
    assert not hasattr(operation_smoke, "TodoRunSummary")
