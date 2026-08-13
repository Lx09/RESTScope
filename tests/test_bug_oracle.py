"""Deterministic and System Agent behavior of the Bug Oracle."""

from __future__ import annotations

from restscope.agent import SystemAgentResult


class _Runner:
    """Return configured decisions while recording one isolated call per candidate."""

    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.calls = []

    def run_system_agent(self, profile_name, task):
        self.calls.append((profile_name, task))
        confirmed = next(self.decisions)
        return SystemAgentResult(
            session_id=f"session-{len(self.calls)}",
            profile_name=profile_name,
            status="completed",
            output={"confirmed_bug": confirmed, "reason": "bounded reason"},
        )


class _Catalog:
    """Capture final Assessments without requiring database setup."""

    def __init__(self):
        self.records = []

    def record_oracle_assessment(self, **values):
        self.records.append(values)


class _FailingCatalog:
    """Reject final persistence while allowing the Oracle to keep its verdict."""

    def record_oracle_assessment(self, **_values):
        raise RuntimeError("database unavailable")


def test_multiple_confirmed_candidates_share_one_replay_decision() -> None:
    """Primary confirmation returns one Replay instruction for all categories."""

    from restscope.api_behavior_monitor.contract_validation import ContractMismatch, ContractValidationResult
    from restscope.api_behavior_monitor.oracle import BugOracle

    runner = _Runner([True, True])
    catalog = _Catalog()
    oracle = BugOracle(catalog=catalog, system_agent_runner=runner)
    mismatch = ContractValidationResult((ContractMismatch("schema", "/c", "/i", "int", "str"),))

    primary = oracle.evaluate_primary(
        primary_observation_id="primary",
        operation_id="POST /items",
        status_code=500,
        input_validity="valid",
        validity_provenance="positive_generator",
        baseline_validation=mismatch,
    )

    assert primary.replay_required is True
    assert [item.name for item in primary.confirmed] == [
        "valid_input_server_error",
        "response_schema_mismatch",
    ]
    assert len(runner.calls) == 2
    assert catalog.records == []

    finalization = oracle.evaluate_replay(
        primary=primary,
        replay_observation_id="replay",
        status_code=503,
        baseline_validation=mismatch,
        replay_persisted=True,
    )

    final = finalization.assessment
    assert final.is_bug is True
    assert [check.status for check in final.checks] == [
        "reproduced",
        "no_candidate",
        "reproduced",
    ]
    assert len(catalog.records) == 1


def test_http_request_status_rules_are_not_applicable_but_schema_still_runs() -> None:
    """Absent Generator validity disables only the two input-semantic rules."""

    from restscope.api_behavior_monitor.contract_validation import ContractValidationResult
    from restscope.api_behavior_monitor.oracle import BugOracle

    catalog = _Catalog()
    oracle = BugOracle(catalog=catalog, system_agent_runner=_Runner([]))
    decision = oracle.evaluate_primary(
        primary_observation_id="primary",
        operation_id="GET /items",
        status_code=500,
        input_validity=None,
        validity_provenance=None,
        baseline_validation=ContractValidationResult(),
    )

    assert decision.replay_required is False
    assert [check.status for check in decision.checks] == [
        "not_applicable",
        "not_applicable",
        "no_candidate",
    ]
    assert catalog.records[0]["assessment"].is_bug is False


def test_assessment_persistence_failure_keeps_the_in_memory_verdict() -> None:
    """Advisory storage loss does not erase a completed no-Bug Assessment."""

    from restscope.api_behavior_monitor.contract_validation import ContractValidationResult
    from restscope.api_behavior_monitor.oracle import BugOracle

    decision = BugOracle(
        catalog=_FailingCatalog(),
        system_agent_runner=_Runner([]),
    ).evaluate_primary(
        primary_observation_id="primary",
        operation_id="GET /items",
        status_code=200,
        input_validity=None,
        validity_provenance=None,
        baseline_validation=ContractValidationResult(),
    )

    assert decision.assessment is not None
    assert decision.assessment.is_bug is False
    assert decision.persistence_failed is True
