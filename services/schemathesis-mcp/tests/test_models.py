import pytest
from pydantic import ValidationError

from schemathesis_mcp.models import InlineSchema, RunOutcome, RunRequest, RunState, RunStatus


def test_run_request_uses_discriminated_schema_input() -> None:
    request = RunRequest(
        schema={"kind": "inline", "format": "yaml", "content": "openapi: 3.0.0"},
        reports=["junit"],
    )

    assert isinstance(request.schema_input, InlineSchema)
    assert request.reports == ["junit"]


def test_old_string_schema_contract_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RunRequest(schema="openapi.yaml")


def test_run_status_separates_job_state_from_test_outcome() -> None:
    status = RunStatus(run_id="run-1", state=RunState.COMPLETED, outcome=RunOutcome.FAILED)

    assert status.state is RunState.COMPLETED
    assert status.outcome is RunOutcome.FAILED
