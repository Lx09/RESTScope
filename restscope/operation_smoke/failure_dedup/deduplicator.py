"""Turn one failed Batch into unique, single-case Failure work items.

This deterministic Module owns message extraction, exact deduplication,
representative-case selection, LLM-output validation handoff, and Memory
recording. It calls :class:`FailureDedupAgent` only when several distinct
message Fingerprints require semantic Parameter grouping.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Protocol

from restscope.observability import TracingRuntime
from restscope.operation_smoke.memory import (
    FailureBatchWrite,
    FailureObservationWrite,
    FailureWrite,
    RecordedFailures,
)
from restscope.operation_smoke.test_case_catalog import (
    CatalogTestCase,
    TestCaseCatalog,
)

from .agent import FailureDedupAgent
from .schemas import (
    FailureDedupDecision,
    FailureDedupRequest,
    FailureDedupResult,
    FailureGroupDecision,
    FailureTodo,
)


_MAX_FINGERPRINTS = 100


class FailureMemoryWriter(Protocol):
    """Describe the only Memory mutation required by Failure Dedup."""

    def record_failures(self, write: FailureBatchWrite) -> RecordedFailures:
        """Persist validated Failures and return their new stable identities."""
        ...


class FailureDeduplicator:
    """Hide exact and semantic deduplication behind one Coordinator Interface."""

    def __init__(
        self,
        *,
        agent: FailureDedupAgent,
        memory: FailureMemoryWriter,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store the semantic Agent and deterministic Memory writer."""
        self.agent = agent
        self.memory = memory
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def deduplicate(
        self,
        request: FailureDedupRequest,
        *,
        catalog: TestCaseCatalog,
        max_outputs: int,
    ) -> FailureDedupResult:
        """Return one single-case Solve Todo for every distinct Failure.

        The first case producing each normalized message wins. After semantic
        grouping, the earliest original Batch case among a group's messages
        becomes that Failure's sole representative.
        """
        fingerprints = _fingerprints(
            catalog=catalog,
            case_ids=request.case_ids,
        )
        if not fingerprints:
            raise RuntimeError(
                "The unsuccessful Batch did not contain deduplicable failure evidence."
            )
        if len(fingerprints) > _MAX_FINGERPRINTS:
            raise RuntimeError(
                "The Batch exceeded the 100 unique Failure Fingerprint limit."
            )

        with self.tracing_runtime.span(
            "FailureDeduplicator.deduplicate",
            kind="CHAIN",
            input_value={
                "operation_key": request.operation_key,
                "failed_case_count": len(request.case_ids),
                "exact_fingerprint_count": len(fingerprints),
            },
        ) as span:
            if len(fingerprints) == 1:
                message = next(iter(fingerprints))
                decision = FailureDedupDecision(
                    failures=[
                        FailureGroupDecision(
                            summary=message,
                            suspected_parameters=[],
                            messages=[message],
                        )
                    ],
                    reason="One exact Failure Fingerprint requires no LLM grouping.",
                )
                result = self._record_and_expand(
                    request=request,
                    catalog=catalog,
                    fingerprints=fingerprints,
                    decision=decision,
                    outputs_used=0,
                    correction_count=0,
                    bypassed=True,
                )
            else:
                if max_outputs < 1:
                    result = FailureDedupResult(
                        status="dedup_budget_exhausted",
                        reason="The Failure Dedup output budget was exhausted.",
                        outputs_used=0,
                        correction_count=0,
                        exact_fingerprint_count=len(fingerprints),
                    )
                    span.set_output(result.model_dump(mode="json"))
                    return result
                observations = [
                    {
                        "message": message,
                        "case_id": case_id,
                    }
                    for message, case_id in fingerprints.items()
                ]
                decision, outputs, corrections, errors = self.agent.deduplicate(
                    operation_key=request.operation_key,
                    semantic_parameters=sorted(catalog.valid_parameters),
                    observations=observations,
                    catalog=catalog,
                    max_outputs=max_outputs,
                )
                if decision is None:
                    result = FailureDedupResult(
                        status="dedup_budget_exhausted",
                        reason="; ".join(
                            errors
                            or ["The Failure Dedup output budget was exhausted."]
                        ),
                        outputs_used=outputs,
                        correction_count=corrections,
                        exact_fingerprint_count=len(fingerprints),
                    )
                else:
                    result = self._record_and_expand(
                        request=request,
                        catalog=catalog,
                        fingerprints=fingerprints,
                        decision=decision,
                        outputs_used=outputs,
                        correction_count=corrections,
                        bypassed=False,
                    )
            span.set_output(
                {
                    "status": result.status,
                    "failure_count": len(result.todos),
                    "outputs_used": result.outputs_used,
                    "correction_count": result.correction_count,
                }
            )
            return result

    def _record_and_expand(
        self,
        *,
        request: FailureDedupRequest,
        catalog: TestCaseCatalog,
        fingerprints: OrderedDict[str, str],
        decision: FailureDedupDecision,
        outputs_used: int,
        correction_count: int,
        bypassed: bool,
    ) -> FailureDedupResult:
        """Persist one representative Observation, then create Solve Todos."""
        selections = [
            _select_representative(
                group=group,
                fingerprints=fingerprints,
                case_ids=request.case_ids,
            )
            for group in decision.failures
        ]
        recorded = self.memory.record_failures(
            FailureBatchWrite(
                operation_key=request.operation_key,
                round_number=request.round_number,
                batch_run_id=request.batch_run_id,
                failures=[
                    FailureWrite(
                        summary=group.summary,
                        suspected_parameters=(
                            None if bypassed else group.suspected_parameters
                        ),
                        observations=[
                            _observation_write(
                                # The model may list messages in any order. Use
                                # the message that actually produced the chosen
                                # earliest case so Memory evidence cannot pair
                                # one HTTP exchange with another error text.
                                message=message,
                                case=catalog.get_case(case_id),
                                observation_key=(
                                    f"{request.batch_run_id}:"
                                    f"{request.case_ids.index(case_id) + 1}"
                                ),
                            )
                        ],
                    )
                    for group, (message, case_id) in zip(
                        decision.failures,
                        selections,
                        strict=True,
                    )
                ],
            )
        )
        todos: list[FailureTodo] = []
        for index, (group, stable, (_, case_id)) in enumerate(
            zip(
                decision.failures,
                recorded.failures,
                selections,
                strict=True,
            ),
            start=1,
        ):
            todos.append(
                FailureTodo(
                    todo_id=f"T{index}",
                    failure_id=stable.failure_id,
                    failure=group.summary,
                    test_case_id=case_id,
                    suspected_parameters=(
                        None if bypassed else group.suspected_parameters
                    ),
                )
            )
        return FailureDedupResult(
            status="bypassed" if bypassed else "deduplicated",
            todos=todos,
            reason=decision.reason,
            outputs_used=outputs_used,
            correction_count=correction_count,
            exact_fingerprint_count=len(fingerprints),
        )


def _select_representative(
    *,
    group: FailureGroupDecision,
    fingerprints: OrderedDict[str, str],
    case_ids: list[str],
) -> tuple[str, str]:
    """Choose the earliest Batch case and retain its matching error message."""
    return min(
        ((message, fingerprints[message]) for message in group.messages),
        key=lambda item: case_ids.index(item[1]),
    )


def _fingerprints(
    *,
    catalog: TestCaseCatalog,
    case_ids: list[str],
) -> OrderedDict[str, str]:
    """Keep the first Catalog case for each already-normalized Failure message."""
    output: OrderedDict[str, str] = OrderedDict()
    for case_id in case_ids:
        case = catalog.get_case(case_id)
        if case.failure is None:
            continue
        for message in case.failure.messages:
            output.setdefault(message, case_id)
    return output


def _observation_write(
    *,
    message: str,
    case: CatalogTestCase,
    observation_key: str,
) -> FailureObservationWrite:
    """Reduce the representative case to bounded persistent evidence."""
    response_summary = {}
    if case.failure is not None and case.failure.kind == "http":
        response_summary["status_code"] = case.failure.status_code
    return FailureObservationWrite(
        observation_key=observation_key,
        trigger=message,
        response_summary=response_summary,
        # Concrete request values remain exclusively in the run-local Catalog.
        # Persisting them here would quietly turn one representative Test Case
        # into a database-backed test history.
        necessary_values={},
    )
