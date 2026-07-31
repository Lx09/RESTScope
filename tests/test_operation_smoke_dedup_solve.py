"""Cross-role request and HTTP-scope contracts for the simplified lifecycle."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from restscope.operation_smoke.failure_solver import CurrentOperationHTTPProbe
from restscope.operation_smoke import OperationSmokeRequest
from restscope.llm import ToolCall
from tests._operation_smoke_dedup_solve_fixtures import smoke_config


def test_operation_smoke_request_uses_large_output_budgets() -> None:
    """The public request exposes the new role budgets and no legacy controls."""
    request = OperationSmokeRequest(
        operation_key="GET /projects/{projectId}"
    )

    assert request.max_dedup_outputs == 50
    assert request.max_solve_outputs_per_todo == 50
    assert request.max_patch_outputs == 20
    assert request.continuation_interval == 10
    assert not hasattr(request, "seed")
    assert not hasattr(request, "max_effect_outputs")
    assert not hasattr(request, "max_feedback_rounds")
    assert not hasattr(request, "max_diagnosis_outputs_per_failure")
    assert not hasattr(request, "max_patch_attempts")


@pytest.mark.parametrize(
    "legacy_field",
    [
        "max_feedback_rounds",
        "max_diagnosis_outputs_per_failure",
        "max_patch_attempts",
        "seed",
        "max_effect_outputs",
    ],
)
def test_operation_smoke_request_rejects_legacy_budget_fields(
    legacy_field: str,
) -> None:
    """No compatibility layer silently revives the old state-machine budget."""
    with pytest.raises(ValidationError):
        OperationSmokeRequest.model_validate(
            {
                "operation_key": "GET /projects/{projectId}",
                legacy_field: 3,
            }
        )


def test_failure_solve_http_probe_rejects_cross_operation_method_and_path() -> None:
    """Solve may vary parameters but cannot leave the current method/template."""
    probe = CurrentOperationHTTPProbe(executor=object())

    wrong_method = probe.validate(
        config=smoke_config(),
        tool_call=ToolCall(
            id="wrong-method",
            name="restscope.http.request",
            arguments={"method": "POST", "path": "/projects/known"},
        ),
    )
    wrong_path = probe.validate(
        config=smoke_config(),
        tool_call=ToolCall(
            id="wrong-path",
            name="restscope.http.request",
            arguments={"method": "GET", "path": "/users/known"},
        ),
    )
    allowed = probe.validate(
        config=smoke_config(),
        tool_call=ToolCall(
            id="allowed",
            name="restscope.http.request",
            arguments={"method": "GET", "path": "/projects/known"},
        ),
    )

    assert "method must be GET" in wrong_method
    assert "must match" in wrong_path
    assert allowed is None


def test_failure_solve_http_probe_atomically_preflights_strict_arguments() -> None:
    """Unknown HTTP fields are rejected before Failure Solve executes a call."""
    probe = CurrentOperationHTTPProbe(executor=object())

    error = probe.validate(
        config=smoke_config(),
        tool_call=ToolCall(
            id="invalid-arguments",
            name="restscope.http.request",
            arguments={
                "method": "GET",
                "path": "/projects/known",
                "unexpected": True,
            },
        ),
    )

    assert error is not None
    assert "unexpected" in error


def test_failure_solve_http_probe_rejects_mutating_operations() -> None:
    """A forged DELETE probe is denied even when no Agent tool was exposed."""
    probe = CurrentOperationHTTPProbe(executor=object())
    config = smoke_config()
    config = config.model_copy(
        update={
            "operation_key": "DELETE /projects/{projectId}",
            "snapshot": config.snapshot.model_copy(
                update={
                    "operation_key": "DELETE /projects/{projectId}",
                    "method": "DELETE",
                }
            ),
        }
    )

    error = probe.validate(
        config=config,
        tool_call=ToolCall(
            id="forged-delete",
            name="restscope.http.request",
            arguments={"method": "DELETE", "path": "/projects/known"},
        ),
    )

    assert error == "HTTP probes are unavailable for mutating operations"
