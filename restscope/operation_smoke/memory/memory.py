"""Expose the deep Operation Smoke Memory Interface to workflow callers.

``SmokeMemory`` owns transaction lifetime and cross-call invariants while its
Adapter owns storage mechanics. Planner and Failure Solve therefore use small
domain operations—including ranked candidate retrieval—instead of coordinating
sessions, ORM rows, joins, or commits themselves.
"""

from __future__ import annotations

from collections.abc import Callable
import re
from typing import Any

from .ports import SmokeMemoryUnitOfWork
from .schemas import (
    FailureCatalogEntry,
    FailureCandidate,
    FailureHistory,
    FailureRetrievalObservation,
    InvestigationWrite,
    ParameterHistory,
    PlanMemoryWrite,
    RecordedPlan,
)


class SmokeMemoryReferenceError(ValueError):
    """A Failure or Parameter reference does not belong to the requested operation."""


class SmokeMemory:
    """Persist and retrieve structured Failure knowledge for one App database."""

    def __init__(
        self,
        unit_of_work_factory: Callable[[], SmokeMemoryUnitOfWork],
    ) -> None:
        """Bind the transaction Adapter without opening a database session."""
        self.unit_of_work_factory = unit_of_work_factory

    def record_plan(self, write: PlanMemoryWrite) -> RecordedPlan:
        """Atomically record one complete validated Planner classification."""
        with self.unit_of_work_factory() as uow:
            result = uow.smoke_memory.record_plan(write)
            uow.commit()
            return result

    def record_investigation(self, write: InvestigationWrite) -> str:
        """Append one terminal Solve conclusion and return its stable identity."""
        with self.unit_of_work_factory() as uow:
            investigation_id = uow.smoke_memory.record_investigation(write)
            uow.commit()
            return investigation_id

    def list_operation_failures(
        self,
        operation_key: str,
    ) -> list[FailureCatalogEntry]:
        """Return the compact Failure directory always included for Planner."""
        with self.unit_of_work_factory() as uow:
            return uow.smoke_memory.list_operation_failures(operation_key)

    def find_failure_candidates(
        self,
        operation_key: str,
        observations: list[FailureRetrievalObservation],
    ) -> list[FailureCandidate]:
        """Rank a small, operation-scoped candidate set for current failures.

        Storage continues to expose a simple catalog/history seam.  Retrieval
        policy lives here so Planner does not depend on SQL queries and a future
        vector implementation can replace this method without changing the
        Agent or its public Context interface.
        """
        if not observations:
            return []
        with self.unit_of_work_factory() as uow:
            catalog = uow.smoke_memory.list_operation_failures(operation_key)
            histories = uow.smoke_memory.lookup_failure_history(
                operation_key,
                [entry.failure_id for entry in catalog],
            )
        entry_by_id = {entry.failure_id: entry for entry in catalog}
        history_by_id = {history.failure_id: history for history in histories}

        # Rank independently per current case, then merge round-robin. This
        # prevents the first noisy observation from consuming all 24 slots.
        per_case: list[list[tuple[tuple[Any, ...], FailureCandidate]]] = []
        for observation in observations:
            matches: list[tuple[tuple[Any, ...], FailureCandidate]] = []
            for history in histories:
                score = _candidate_score(observation, history)
                if score is None:
                    continue
                reasons, ranking = score
                entry = entry_by_id[history.failure_id]
                matches.append(
                    (
                        ranking,
                        _candidate_projection(
                            history=history,
                            entry=entry,
                            case_code=observation.case_code,
                            reasons=reasons,
                        ),
                    )
                )
            matches.sort(key=lambda item: item[0])
            per_case.append(matches[:3])

        merged: dict[str, FailureCandidate] = {}
        for rank in range(3):
            for matches in per_case:
                if rank >= len(matches):
                    continue
                candidate = matches[rank][1]
                existing = merged.get(candidate.failure_id)
                if existing is None:
                    merged[candidate.failure_id] = candidate
                else:
                    merged[candidate.failure_id] = existing.model_copy(
                        update={
                            "matched_case_codes": sorted(
                                {
                                    *existing.matched_case_codes,
                                    *candidate.matched_case_codes,
                                }
                            ),
                            "match_reasons": list(
                                dict.fromkeys(
                                    [
                                        *existing.match_reasons,
                                        *candidate.match_reasons,
                                    ]
                                )
                            ),
                        }
                    )
                if len(merged) == 24:
                    return list(merged.values())
        return list(merged.values())

    def lookup_failure_history(
        self,
        operation_key: str,
        failure_ids: list[str],
    ) -> list[FailureHistory]:
        """Return ordered histories or fail if any reference crosses operations."""
        with self.unit_of_work_factory() as uow:
            return uow.smoke_memory.lookup_failure_history(
                operation_key,
                failure_ids,
            )

    def lookup_parameter_history(
        self,
        operation_key: str,
        input_node_ids: list[str],
    ) -> list[ParameterHistory]:
        """Return histories for exact operation inputs in caller order."""
        with self.unit_of_work_factory() as uow:
            return uow.smoke_memory.lookup_parameter_history(
                operation_key,
                input_node_ids,
            )


_GENERIC_TERMS = frozenset(
    {
        "error",
        "failed",
        "failure",
        "unexpected",
        "status",
        "response",
        "request",
        "invalid",
        "unknown",
        "application",
        "json",
    }
)


def _candidate_score(
    current: FailureRetrievalObservation,
    history: FailureHistory,
) -> tuple[list[str], tuple[Any, ...]] | None:
    """Return explicit match reasons and a stable ascending ranking tuple."""
    history_paths = {
        path
        for observation in history.observations
        for path in _leaf_paths(observation.necessary_values)
    }
    parameter_paths = {
        _semantic_node_path(parameter.input_node_id)
        for investigation in history.investigations
        for parameter in investigation.parameters
    }
    historical_statuses = {
        _integer_or_none(observation.response_summary.get("status_code"))
        for observation in history.observations
    }
    historical_kinds = {
        _normalize_token_text(observation.trigger)
        for observation in history.observations
    }
    historical_text = " ".join(
        [
            history.summary,
            *[observation.trigger for observation in history.observations],
            *[
                _searchable_evidence(observation.response_summary)
                for observation in history.observations
            ],
            *[
                text
                for investigation in history.investigations
                for text in (
                    investigation.root_cause,
                    investigation.solution,
                    investigation.conflict_reason or "",
                )
            ],
        ]
    )
    historical_terms = _keywords(historical_text)
    current_terms = set(current.keywords) or _keywords(
        " ".join(
            part
            for part in (
                current.failure_kind,
                current.transport_error,
                current.error_signature,
            )
            if part
        )
    )
    shared_paths = set(current.input_paths) & history_paths
    shared_parameters = set(current.input_paths) & parameter_paths
    shared_terms = current_terms & historical_terms
    status_match = (
        current.status_code is not None
        and current.status_code in historical_statuses
    )
    kind_match = any(
        _normalize_token_text(current.failure_kind) == historical_kind
        for historical_kind in historical_kinds
    )
    current_signature_terms = _keywords(current.error_signature or "")
    exact_signature = bool(
        current_signature_terms
        and current_signature_terms <= historical_terms
    )
    transport_match = bool(
        current.transport_error
        and _normalize_token_text(current.transport_error) in historical_text.casefold()
    )

    qualifies = (
        exact_signature
        or bool(shared_parameters)
        or (status_match and kind_match and bool(shared_paths))
        or bool(shared_terms)
        or transport_match
    )
    if not qualifies:
        return None

    reasons: list[str] = []
    if exact_signature:
        reasons.append("exact-error-signature")
    if shared_parameters:
        reasons.append(
            "same-causal-parameter:" + ",".join(sorted(shared_parameters))
        )
    if status_match and kind_match and shared_paths:
        reasons.append("same-status-kind-and-input")
    if shared_terms:
        reasons.append(
            "shared-terms:" + ",".join(sorted(shared_terms)[:5])
        )
    if transport_match:
        reasons.append("same-transport-error")
    last_round = max(
        [
            *[item.round_number for item in history.observations],
            *[item.round_number for item in history.investigations],
            0,
        ]
    )
    ranking = (
        0 if exact_signature else 1,
        0 if shared_parameters else 1,
        0 if status_match and kind_match and shared_paths else 1,
        -len(shared_terms),
        -last_round,
        history.failure_id,
    )
    return reasons, ranking


def _candidate_projection(
    *,
    history: FailureHistory,
    entry: FailureCatalogEntry,
    case_code: str,
    reasons: list[str],
) -> FailureCandidate:
    """Reduce a complete history to the facts allowed in Planner's prompt."""
    last_round = max(
        [
            *[item.round_number for item in history.observations],
            *[item.round_number for item in history.investigations],
            0,
        ]
    )
    return FailureCandidate(
        failure_id=history.failure_id,
        summary=history.summary,
        matched_case_codes=[case_code],
        match_reasons=reasons,
        observation_count=entry.observation_count,
        investigation_count=entry.investigation_count,
        applied_patch_count=entry.applied_patch_count,
        last_seen_round=last_round,
        recent_investigations=history.investigations[-2:],
    )


def _leaf_paths(value: Any, prefix: str = "") -> set[str]:
    """Return stable dotted leaf paths from bounded request evidence."""
    if isinstance(value, dict):
        output: set[str] = set()
        for key, child in value.items():
            root_key = {
                "path_parameters": "path",
                "query_parameters": "query",
                "query": "query",
                "headers": "header",
                "body": "body",
            }.get(str(key), str(key))
            path = f"{prefix}.{root_key}" if prefix else root_key
            output.update(_leaf_paths(child, path))
        return output
    if isinstance(value, list):
        return {prefix} if prefix else set()
    return {prefix} if prefix else set()


def _keywords(value: str) -> set[str]:
    """Extract discriminative lowercase terms and discard generic prompt noise."""
    return {
        token
        for token in re.findall(r"[a-z0-9_./-]{3,}", value.casefold())
        if token not in _GENERIC_TERMS and not token.isdigit()
    }


def _normalize_token_text(value: str) -> str:
    """Normalize a kind or transport phrase for deterministic comparison."""
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _searchable_evidence(value: Any) -> str:
    """Flatten stored response summaries for retrieval without prompt rendering."""
    if isinstance(value, dict):
        return " ".join(_searchable_evidence(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_searchable_evidence(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _integer_or_none(value: Any) -> int | None:
    """Convert stored status evidence without accepting booleans as integers."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _semantic_node_path(input_node_id: str) -> str:
    """Approximate a stored node identity as the handle used in Batch evidence.

    Generator node IDs are stable runtime identities such as
    ``request/body/properties/size`` or ``path/projectId``. Retrieval needs only
    a deterministic path signal; exact semantic-handle construction remains in
    the testing Module.
    """
    segments = [
        segment
        for segment in input_node_id.split("/")
        if segment not in {"request", "properties"}
    ]
    if not segments:
        return input_node_id
    root = {
        "headers": "header",
        "path_parameters": "path",
        "query_parameters": "query",
    }.get(segments[0], segments[0])
    return ".".join([root, *segments[1:]])
