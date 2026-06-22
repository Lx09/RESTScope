from schemathesis_mcp.models import RunOutcome, RunState, RunStatus


def test_run_status_separates_job_state_from_test_outcome() -> None:
    status = RunStatus(run_id="run-1", state=RunState.COMPLETED, outcome=RunOutcome.FAILED)

    assert status.model_dump(mode="json") == {
        "run_id": "run-1",
        "state": "completed",
        "outcome": "failed",
        "created_at": status.created_at.isoformat().replace("+00:00", "Z"),
        "started_at": None,
        "finished_at": None,
        "current_phase": None,
        "stop_reason": None,
        "progress": {
            "events": 0,
            "scenarios": 0,
            "failures": 0,
            "errors": 0,
        },
        "error": None,
    }
