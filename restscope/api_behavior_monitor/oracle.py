"""Classify unexpected HTTP statuses and finalize one Replay-backed verdict.

The Bug Oracle receives only the response status and Generator-provided input
validity after Observation and both Monitors have run. It deterministically
selects a small, ordered reason set, asks the shared Transport to Replay at most
once, and persists one final immutable Assessment for the Primary Observation.
It never sends HTTP, validates response schemas, or asks a model for judgment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .catalog import (
    APIBehaviorCatalog,
    OracleAssessment,
    OracleCheckNoCandidate,
    OracleCheckNotReproduced,
    OracleCheckReplayFailed,
    OracleCheckReproduced,
    OracleReason,
)

InputValidity = Literal["valid", "invalid"]


@dataclass(frozen=True, slots=True)
class OraclePrimaryDecision:
    """Carry one Primary reason set to Transport and the later Replay pass."""

    primary_observation_id: str | None
    primary_reasons: tuple[OracleReason, ...]
    assessment: OracleAssessment | None = None
    persistence_failed: bool = False

    @property
    def replay_required(self) -> bool:
        """Request one Replay exactly when deterministic reasons were found."""

        return bool(self.primary_reasons)


@dataclass(frozen=True, slots=True)
class OracleFinalization:
    """Keep the completed in-memory verdict even if advisory storage fails."""

    assessment: OracleAssessment
    persistence_failed: bool


class BugOracle:
    """Own the complete deterministic unexpected-response-status rule."""

    def __init__(self, *, catalog: APIBehaviorCatalog) -> None:
        """Retain the Catalog used only for final immutable Assessments."""

        self.catalog = catalog

    def evaluate_primary(
        self,
        *,
        primary_observation_id: str | None,
        status_code: int,
        input_validity: InputValidity | None,
    ) -> OraclePrimaryDecision:
        """Select Primary reasons and persist immediately when Replay is unnecessary.

        Any 5xx response is suspicious. An input explicitly produced as invalid
        is also suspicious when accepted with 2xx; invalid 5xx carries both
        reasons. Other status classes do not enter the Oracle in this version.
        """

        reasons = _unexpected_status_reasons(
            status_code=status_code,
            input_validity=input_validity,
        )
        assessment = None if reasons else _no_candidate_assessment()
        persistence_failed = False
        if assessment is not None:
            persistence_failed = not self._persist(
                primary_observation_id=primary_observation_id,
                replay_observation_id=None,
                assessment=assessment,
            )
        return OraclePrimaryDecision(
            primary_observation_id=primary_observation_id,
            primary_reasons=reasons,
            assessment=assessment,
            persistence_failed=persistence_failed,
        )

    def evaluate_replay(
        self,
        *,
        primary: OraclePrimaryDecision,
        replay_observation_id: str | None,
        status_code: int,
        input_validity: InputValidity | None,
        replay_persisted: bool,
    ) -> OracleFinalization:
        """Compare the Replay's complete reason set with the Primary's reasons."""

        replay_reasons = _unexpected_status_reasons(
            status_code=status_code,
            input_validity=input_validity,
        )
        reproduced = replay_reasons == primary.primary_reasons
        check_type = OracleCheckReproduced if reproduced else OracleCheckNotReproduced
        check = check_type(
            name="unexpected_response_status",
            status="reproduced" if reproduced else "not_reproduced",
            primary_reasons=primary.primary_reasons,
            replay_reasons=replay_reasons,
        )
        errors = () if replay_persisted else ("replay_observation_not_persisted",)
        assessment = OracleAssessment(checks=(check,), errors=errors)
        persisted = self._persist(
            primary_observation_id=primary.primary_observation_id,
            replay_observation_id=replay_observation_id,
            assessment=assessment,
        )
        return OracleFinalization(
            assessment=assessment,
            persistence_failed=not persisted,
        )

    def replay_failed(
        self,
        *,
        primary: OraclePrimaryDecision,
        replay_observation_id: str | None,
        error: str,
        replay_persisted: bool,
    ) -> OracleFinalization:
        """Finalize the status candidate when Replay has no HTTP response."""

        check = OracleCheckReplayFailed(
            name="unexpected_response_status",
            status="replay_failed",
            primary_reasons=primary.primary_reasons,
            replay_reasons=(),
            error=error[:500] or "Replay transport failed",
        )
        errors = () if replay_persisted else ("replay_observation_not_persisted",)
        assessment = OracleAssessment(checks=(check,), errors=errors)
        persisted = self._persist(
            primary_observation_id=primary.primary_observation_id,
            replay_observation_id=replay_observation_id,
            assessment=assessment,
        )
        return OracleFinalization(
            assessment=assessment,
            persistence_failed=not persisted,
        )

    def _persist(
        self,
        *,
        primary_observation_id: str | None,
        replay_observation_id: str | None,
        assessment: OracleAssessment,
    ) -> bool:
        """Persist only when Primary identity exists and report advisory failure."""

        if primary_observation_id is None:
            return True
        try:
            self.catalog.record_oracle_assessment(
                primary_observation_id=primary_observation_id,
                replay_observation_id=replay_observation_id,
                assessment=assessment,
            )
        except Exception:  # noqa: BLE001
            return False
        return True


def _unexpected_status_reasons(
    *,
    status_code: int,
    input_validity: InputValidity | None,
) -> tuple[OracleReason, ...]:
    """Return the canonical reason set for one response status and input meaning."""

    reasons: list[OracleReason] = []
    if 500 <= status_code <= 599:
        reasons.append("server_error")
    if input_validity == "invalid" and (
        200 <= status_code <= 299 or 500 <= status_code <= 599
    ):
        reasons.append("invalid_input_unexpected_status")
    return tuple(reasons)


def _no_candidate_assessment() -> OracleAssessment:
    """Build the sole final Assessment shape for a normal response status."""

    return OracleAssessment(
        checks=(
            OracleCheckNoCandidate(
                name="unexpected_response_status",
                status="no_candidate",
            ),
        )
    )
