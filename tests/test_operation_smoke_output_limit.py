"""Contracts for the one Operation-wide model-output safety limit."""

from __future__ import annotations

import pytest


def test_resolution_patch_and_review_share_one_counter() -> None:
    """Role labels explain usage but cannot create independent allowances."""
    from restscope.operation_smoke.output_limit import ModelOutputLimit

    limit = ModelOutputLimit(max_outputs=3)

    assert limit.consume("operation_smoke_failure_resolution").remaining == 2
    assert limit.consume("parameter_patch_agent").remaining == 1
    usage = limit.consume("parameter_patch_review_agent")

    assert usage.used == 3
    assert usage.remaining == 0
    assert usage.by_role == {
        "operation_smoke_failure_resolution": 1,
        "parameter_patch_agent": 1,
        "parameter_patch_review_agent": 1,
    }


def test_output_limit_refuses_the_next_call_without_overcounting() -> None:
    """The 1001st production call is never sent to a provider."""
    from restscope.operation_smoke.output_limit import (
        ModelOutputLimit,
        ModelOutputLimitExceeded,
    )

    limit = ModelOutputLimit(max_outputs=1)
    limit.consume("operation_smoke_failure_resolution")

    with pytest.raises(ModelOutputLimitExceeded, match="1 model outputs"):
        limit.consume("parameter_patch_agent")

    assert limit.used == 1
