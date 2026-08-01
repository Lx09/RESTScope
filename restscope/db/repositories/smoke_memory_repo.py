"""SQLAlchemy adapter for stable Failures and append-only Solve Attempts.

The adapter normalizes Failure identity, validates operation-local input links,
and joins accepted Generator change events into read history.  It never stores
Batch identities, representative Test Cases, raw responses, or Patch samples.
"""

from __future__ import annotations

from hashlib import sha256
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from restscope.operation_smoke.memory.schemas import (
    FailureBatchWrite,
    FailureHistory,
    GeneratorChangeMemory,
    ParameterHistory,
    RecordedFailure,
    RecordedFailures,
    SolveAttemptMemory,
    SolveAttemptParameterWrite,
    SolveAttemptWrite,
)

from ..orm import (
    GeneratorChangeEventORM,
    InputGeneratorConfigORM,
    SmokeFailureORM,
    SmokeSolveAttemptORM,
    SmokeSolveAttemptParameterORM,
)
from ..time import utc_now


class SqlAlchemySmokeMemoryRepository:
    """Persist the narrow durable facts used by later Solve sessions."""

    def __init__(self, session: Session) -> None:
        """Use the session and transaction owned by the surrounding workflow."""

        self.session = session

    def record_failures(self, write: FailureBatchWrite) -> RecordedFailures:
        """Insert or update each stable Failure occurrence in input order."""

        now = utc_now()
        output: list[RecordedFailure] = []
        for item in write.failures:
            messages = _normalized_messages(item.messages)
            suspected = (
                None
                if item.suspected_input_node_ids is None
                else sorted(item.suspected_input_node_ids)
            )
            if suspected:
                self._validate_inputs(
                    operation_key=write.operation_key,
                    input_node_ids=suspected,
                )
            failure_key = _failure_key(
                operation_key=write.operation_key,
                messages=messages,
                suspected_input_node_ids=suspected,
            )
            row = self.session.scalar(
                select(SmokeFailureORM).where(
                    SmokeFailureORM.failure_key == failure_key
                )
            )
            if row is None:
                row = SmokeFailureORM(
                    id=f"failure_{uuid4().hex}",
                    failure_key=failure_key,
                    operation_key=write.operation_key,
                    normalized_messages=messages,
                    summary=item.summary,
                    suspected_input_node_ids=suspected,
                    occurrence_count=1,
                    first_seen_at=now,
                    last_seen_at=now,
                    last_status_code=item.last_status_code,
                )
                self.session.add(row)
            else:
                expected = (
                    write.operation_key,
                    messages,
                    suspected,
                )
                actual = (
                    row.operation_key,
                    list(row.normalized_messages),
                    row.suspected_input_node_ids,
                )
                if actual != expected:
                    raise RuntimeError("Stable Failure key collision")
                row.summary = item.summary
                row.occurrence_count += 1
                row.last_seen_at = now
                row.last_status_code = item.last_status_code
            self.session.flush()
            output.append(
                RecordedFailure(failure_id=row.id, summary=row.summary)
            )
        return RecordedFailures(failures=output)

    def record_solve_attempt(self, write: SolveAttemptWrite) -> str:
        """Append one validated terminal conclusion and its parameter links."""

        failure = self.session.get(SmokeFailureORM, write.failure_id)
        if failure is None or failure.operation_key != write.operation_key:
            raise ValueError("Solve Attempt Failure does not belong to the operation")
        self._validate_inputs(
            operation_key=write.operation_key,
            input_node_ids=[item.input_node_id for item in write.parameters],
        )
        attempt_id = f"solve_attempt_{uuid4().hex}"
        self.session.add(
            SmokeSolveAttemptORM(
                id=attempt_id,
                failure_id=write.failure_id,
                round_number=write.round_number,
                outcome=write.outcome,
                trigger_conditions=write.trigger_conditions,
                root_cause=write.root_cause,
                solution=write.solution,
                evidence_source=write.evidence_source,
                conflict_reason=write.conflict_reason,
            )
        )
        self.session.add_all(
            [
                SmokeSolveAttemptParameterORM(
                    solve_attempt_id=attempt_id,
                    input_node_id=item.input_node_id,
                    cause_summary=item.cause_summary,
                    position=position,
                )
                for position, item in enumerate(write.parameters)
            ]
        )
        self.session.flush()
        return attempt_id

    def failure_history(
        self,
        *,
        operation_key: str,
        failure_id: str,
    ) -> FailureHistory:
        """Return one Failure only when it belongs to the requested operation."""

        failure = self.session.get(SmokeFailureORM, failure_id)
        if failure is None or failure.operation_key != operation_key:
            raise KeyError(f"Unknown Failure for operation: {failure_id}")
        return self._history(failure)

    def parameter_history(
        self,
        *,
        operation_key: str,
        input_node_id: str,
    ) -> ParameterHistory:
        """Return distinct Failures with an Attempt attributed to one input."""

        self._validate_inputs(
            operation_key=operation_key,
            input_node_ids=[input_node_id],
        )
        failure_ids = self.session.scalars(
            select(SmokeSolveAttemptORM.failure_id)
            .join(
                SmokeSolveAttemptParameterORM,
                SmokeSolveAttemptParameterORM.solve_attempt_id
                == SmokeSolveAttemptORM.id,
            )
            .join(
                SmokeFailureORM,
                SmokeFailureORM.id == SmokeSolveAttemptORM.failure_id,
            )
            .where(
                SmokeFailureORM.operation_key == operation_key,
                SmokeSolveAttemptParameterORM.input_node_id == input_node_id,
            )
            .distinct()
            .order_by(SmokeSolveAttemptORM.failure_id)
        ).all()
        failures = [
            self.session.get(SmokeFailureORM, failure_id)
            for failure_id in failure_ids
        ]
        return ParameterHistory(
            input_node_id=input_node_id,
            failures=[
                self._history(failure)
                for failure in failures
                if failure is not None
            ],
        )

    def _history(self, failure: SmokeFailureORM) -> FailureHistory:
        """Assemble one Failure's Attempts and optional Generator diffs."""

        attempts = self.session.scalars(
            select(SmokeSolveAttemptORM)
            .where(SmokeSolveAttemptORM.failure_id == failure.id)
            .order_by(
                SmokeSolveAttemptORM.round_number,
                SmokeSolveAttemptORM.created_at,
                SmokeSolveAttemptORM.id,
            )
        ).all()
        return FailureHistory(
            failure_id=failure.id,
            summary=failure.summary,
            occurrence_count=failure.occurrence_count,
            attempts=[self._attempt_memory(item) for item in attempts],
        )

    def _attempt_memory(
        self,
        attempt: SmokeSolveAttemptORM,
    ) -> SolveAttemptMemory:
        """Project one Attempt, preserving Agent attribution order."""

        parameters = self.session.scalars(
            select(SmokeSolveAttemptParameterORM)
            .where(
                SmokeSolveAttemptParameterORM.solve_attempt_id == attempt.id
            )
            .order_by(SmokeSolveAttemptParameterORM.position)
        ).all()
        event = self.session.scalar(
            select(GeneratorChangeEventORM).where(
                GeneratorChangeEventORM.solve_attempt_id == attempt.id
            )
        )
        return SolveAttemptMemory(
            solve_attempt_id=attempt.id,
            round_number=attempt.round_number,
            outcome=attempt.outcome,
            trigger_conditions=attempt.trigger_conditions,
            root_cause=attempt.root_cause,
            solution=attempt.solution,
            evidence_source=attempt.evidence_source,
            conflict_reason=attempt.conflict_reason,
            parameters=[
                SolveAttemptParameterWrite(
                    input_node_id=item.input_node_id,
                    cause_summary=item.cause_summary,
                )
                for item in parameters
            ],
            generator_change=(
                GeneratorChangeMemory(
                    event_id=event.id,
                    reason=event.reason,
                    generator_changes=list(event.generator_changes),
                    constraint_changes=list(event.constraint_changes),
                )
                if event is not None
                else None
            ),
        )

    def _validate_inputs(
        self,
        *,
        operation_key: str,
        input_node_ids: list[str],
    ) -> None:
        """Reject unknown or cross-operation input attribution."""

        if not input_node_ids:
            return
        rows = self.session.scalars(
            select(InputGeneratorConfigORM).where(
                InputGeneratorConfigORM.input_node_id.in_(input_node_ids),
                InputGeneratorConfigORM.operation_key == operation_key,
            )
        ).all()
        if {row.input_node_id for row in rows} != set(input_node_ids):
            raise ValueError("Solve Attempt contains an unknown operation input")


def _normalized_messages(messages: list[str]) -> list[str]:
    """Normalize whitespace, deduplicate, and sort Failure messages."""

    normalized = {" ".join(message.split()) for message in messages}
    if not normalized or "" in normalized:
        raise ValueError("Failure messages cannot be blank")
    return sorted(normalized)


def _failure_key(
    *,
    operation_key: str,
    messages: list[str],
    suspected_input_node_ids: list[str] | None,
) -> str:
    """Build a stable digest while preserving null versus empty attribution."""

    payload = json.dumps(
        {
            "operation_key": operation_key,
            "messages": messages,
            "suspected_input_node_ids": suspected_input_node_ids,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return f"failure_key_{sha256(payload.encode()).hexdigest()}"
