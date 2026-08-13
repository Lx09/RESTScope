"""Deterministic status rules and Replay finalization for the Bug Oracle."""

from __future__ import annotations

import pytest


class _Catalog:
    """Capture final Assessments without requiring database setup."""

    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def record_oracle_assessment(self, **values: object) -> None:
        """Keep the public persistence arguments for assertions."""

        self.records.append(values)


class _FailingCatalog:
    """Reject final persistence while allowing the Oracle to keep its verdict."""

    def record_oracle_assessment(self, **_values: object) -> None:
        """Represent an unavailable advisory database."""

        raise RuntimeError("database unavailable")


@pytest.mark.parametrize(
    ("status_code", "input_validity", "expected_reasons"),
    [
        (200, "invalid", ("invalid_input_unexpected_status",)),
        (299, "invalid", ("invalid_input_unexpected_status",)),
        (400, "invalid", ()),
        (499, "invalid", ()),
        (500, "invalid", ("server_error", "invalid_input_unexpected_status")),
        (599, "invalid", ("server_error", "invalid_input_unexpected_status")),
        (200, "valid", ()),
        (400, "valid", ()),
        (500, "valid", ("server_error",)),
        (500, None, ("server_error",)),
        (199, "invalid", ()),
        (300, "invalid", ()),
    ],
)
def test_primary_status_matrix_selects_only_approved_reasons(
    status_code: int,
    input_validity: str | None,
    expected_reasons: tuple[str, ...],
) -> None:
    """Only invalid 2xx and all 5xx responses require Replay confirmation."""

    from restscope.api_behavior_monitor.oracle import BugOracle

    catalog = _Catalog()
    decision = BugOracle(catalog=catalog).evaluate_primary(
        primary_observation_id="primary",
        status_code=status_code,
        input_validity=input_validity,
    )

    assert decision.primary_reasons == expected_reasons
    assert decision.replay_required is bool(expected_reasons)
    if expected_reasons:
        assert decision.assessment is None
        assert catalog.records == []
    else:
        assert decision.assessment is not None
        assert decision.assessment.schema_version == 2
        assert decision.assessment.checks[0].status == "no_candidate"
        assert len(catalog.records) == 1


def test_replay_requires_the_exact_same_reason_set() -> None:
    """An invalid 5xx Primary is not reproduced by an invalid 2xx Replay."""

    from restscope.api_behavior_monitor.oracle import BugOracle

    catalog = _Catalog()
    oracle = BugOracle(catalog=catalog)
    primary = oracle.evaluate_primary(
        primary_observation_id="primary",
        status_code=500,
        input_validity="invalid",
    )

    finalization = oracle.evaluate_replay(
        primary=primary,
        replay_observation_id="replay",
        status_code=200,
        input_validity="invalid",
        replay_persisted=True,
    )

    check = finalization.assessment.checks[0]
    assert check.status == "not_reproduced"
    assert check.primary_reasons == (
        "server_error",
        "invalid_input_unexpected_status",
    )
    assert check.replay_reasons == ("invalid_input_unexpected_status",)
    assert finalization.assessment.is_bug is False
    assert len(catalog.records) == 1


def test_replay_with_identical_reasons_reproduces_one_check() -> None:
    """The one status Check becomes a Bug only after exact Replay reproduction."""

    from restscope.api_behavior_monitor.oracle import BugOracle

    catalog = _Catalog()
    oracle = BugOracle(catalog=catalog)
    primary = oracle.evaluate_primary(
        primary_observation_id="primary",
        status_code=503,
        input_validity=None,
    )

    finalization = oracle.evaluate_replay(
        primary=primary,
        replay_observation_id="replay",
        status_code=500,
        input_validity=None,
        replay_persisted=True,
    )

    check = finalization.assessment.checks[0]
    assert check.name == "unexpected_response_status"
    assert check.status == "reproduced"
    assert check.primary_reasons == ("server_error",)
    assert check.replay_reasons == ("server_error",)
    assert finalization.assessment.is_bug is True


def test_replay_failure_keeps_primary_reasons_and_error() -> None:
    """A transport failure cannot prove a candidate even when it is persisted."""

    from restscope.api_behavior_monitor.oracle import BugOracle

    oracle = BugOracle(catalog=_Catalog())
    primary = oracle.evaluate_primary(
        primary_observation_id="primary",
        status_code=500,
        input_validity="valid",
    )

    finalization = oracle.replay_failed(
        primary=primary,
        replay_observation_id="replay",
        error="request_timeout",
        replay_persisted=True,
    )

    check = finalization.assessment.checks[0]
    assert check.status == "replay_failed"
    assert check.primary_reasons == ("server_error",)
    assert check.replay_reasons == ()
    assert check.error == "request_timeout"


def test_unpersisted_replay_keeps_the_in_memory_bug_and_records_error() -> None:
    """Replay persistence loss does not erase exact in-memory reproduction."""

    from restscope.api_behavior_monitor.oracle import BugOracle

    oracle = BugOracle(catalog=_Catalog())
    primary = oracle.evaluate_primary(
        primary_observation_id=None,
        status_code=500,
        input_validity=None,
    )

    finalization = oracle.evaluate_replay(
        primary=primary,
        replay_observation_id=None,
        status_code=503,
        input_validity=None,
        replay_persisted=False,
    )

    assert finalization.assessment.is_bug is True
    assert finalization.assessment.errors == ("replay_observation_not_persisted",)


def test_final_persistence_failure_keeps_a_reproduced_verdict() -> None:
    """Assessment storage is advisory after Replay has proved the status Bug."""

    from restscope.api_behavior_monitor.oracle import BugOracle

    oracle = BugOracle(catalog=_FailingCatalog())
    primary = oracle.evaluate_primary(
        primary_observation_id="primary",
        status_code=500,
        input_validity=None,
    )

    finalization = oracle.evaluate_replay(
        primary=primary,
        replay_observation_id="replay",
        status_code=500,
        input_validity=None,
        replay_persisted=True,
    )

    assert finalization.assessment.is_bug is True
    assert finalization.persistence_failed is True


def test_assessment_persistence_failure_keeps_the_in_memory_verdict() -> None:
    """Advisory storage loss does not erase a completed no-Bug Assessment."""

    from restscope.api_behavior_monitor.oracle import BugOracle

    decision = BugOracle(catalog=_FailingCatalog()).evaluate_primary(
        primary_observation_id="primary",
        status_code=200,
        input_validity=None,
    )

    assert decision.assessment is not None
    assert decision.assessment.is_bug is False
    assert decision.persistence_failed is True
