"""SQLAlchemy Adapter for the Operation Smoke Memory Interface.

The Adapter translates normalized rows into domain projections in one place.
Agent code never imports these ORM mappings, and callers never need to assemble
Failure-to-Observation or Parameter-to-Investigation joins themselves.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from restscope.operation_smoke.memory import (
    AppliedPatchMemory,
    FailureCatalogEntry,
    FailureHistory,
    FailureObservationMemory,
    InvestigationMemory,
    InvestigationParameterWrite,
    InvestigationWrite,
    ParameterHistory,
    PlanMemoryWrite,
    RecordedFailure,
    RecordedPlan,
    SmokeMemoryReferenceError,
)

from ..orm.smoke_memory_orm import (
    SmokeAppliedPatchORM,
    SmokeFailureObservationORM,
    SmokeFailureORM,
    SmokeInvestigationORM,
    SmokeInvestigationParameterORM,
    SmokeObservationORM,
    SmokeParameterORM,
)


class SqlAlchemySmokeMemoryRepository:
    """Persist and project structured Smoke memory within one active session."""

    def __init__(self, session: Session) -> None:
        """Bind the active transaction without committing it."""
        self.session = session

    def record_plan(self, write: PlanMemoryWrite) -> RecordedPlan:
        """Record classifications while reusing shared Observation rows."""
        recorded: list[RecordedFailure] = []
        for classification in write.classifications:
            failure = self._resolve_failure(
                operation_key=write.operation_key,
                failure_id=classification.failure_id,
                summary=classification.summary,
            )
            recorded.append(
                RecordedFailure(
                    failure_id=failure.id,
                    summary=failure.summary,
                )
            )
            for observation in classification.observations:
                observation_row = self._resolve_observation(
                    write=write,
                    observation=observation,
                )
                existing_link = self.session.scalar(
                    select(SmokeFailureObservationORM).where(
                        SmokeFailureObservationORM.failure_id == failure.id,
                        SmokeFailureObservationORM.observation_id
                        == observation_row.id,
                    )
                )
                if existing_link is None:
                    self.session.add(
                        SmokeFailureObservationORM(
                            id=f"smoke_fol_{uuid4().hex}",
                            failure_id=failure.id,
                            observation_id=observation_row.id,
                            disposition=classification.disposition,
                            disposition_reason=classification.disposition_reason,
                        )
                    )
        self.session.flush()
        return RecordedPlan(failures=recorded)

    def record_investigation(self, write: InvestigationWrite) -> str:
        """Append one Investigation and its optional applied Patch."""
        failure = self._require_failure(
            operation_key=write.operation_key,
            failure_id=write.failure_id,
        )
        investigation_id = f"smoke_inv_{uuid4().hex}"
        self.session.add(
            SmokeInvestigationORM(
                id=investigation_id,
                failure_id=failure.id,
                round_number=write.round_number,
                outcome=write.outcome,
                trigger_conditions=write.trigger_conditions,
                root_cause=write.root_cause,
                solution=write.solution,
                evidence_source=write.evidence_source,
                conflict_reason=write.conflict_reason,
            )
        )

        # Parameters are operation-local identities. Reusing the row makes the
        # reverse query deterministic even when many Failures involve one input.
        for parameter in write.parameters:
            parameter_row = self._resolve_parameter(
                operation_key=write.operation_key,
                input_node_id=parameter.input_node_id,
            )
            self.session.add(
                SmokeInvestigationParameterORM(
                    id=f"smoke_ipl_{uuid4().hex}",
                    investigation_id=investigation_id,
                    parameter_id=parameter_row.id,
                    cause_summary=parameter.cause_summary,
                )
            )

        if write.applied_patch is not None:
            patch = write.applied_patch
            self.session.add(
                SmokeAppliedPatchORM(
                    id=f"smoke_patch_{uuid4().hex}",
                    investigation_id=investigation_id,
                    generator_revision=patch.generator_revision,
                    patch=patch.patch,
                    before_generators=patch.before_generators,
                    after_generators=patch.after_generators,
                    samples=patch.samples,
                )
            )
        self.session.flush()
        return investigation_id

    def list_operation_failures(
        self,
        operation_key: str,
    ) -> list[FailureCatalogEntry]:
        """Return stable catalog order with counts computed by the Adapter."""
        failures = self.session.scalars(
            select(SmokeFailureORM)
            .where(SmokeFailureORM.operation_key == operation_key)
            .order_by(SmokeFailureORM.created_at, SmokeFailureORM.id)
        ).all()
        return [
            FailureCatalogEntry(
                failure_id=failure.id,
                summary=failure.summary,
                observation_count=self._count_observations(failure.id),
                investigation_count=self._count_investigations(failure.id),
                applied_patch_count=self._count_applied_patches(failure.id),
            )
            for failure in failures
        ]

    def lookup_failure_history(
        self,
        operation_key: str,
        failure_ids: list[str],
    ) -> list[FailureHistory]:
        """Return histories in caller order after validating every identity."""
        return [
            self._failure_history(
                self._require_failure(
                    operation_key=operation_key,
                    failure_id=failure_id,
                )
            )
            for failure_id in failure_ids
        ]

    def lookup_parameter_history(
        self,
        operation_key: str,
        input_node_ids: list[str],
    ) -> list[ParameterHistory]:
        """Traverse Parameter → Investigation → Failure for Solve context."""
        histories: list[ParameterHistory] = []
        for input_node_id in input_node_ids:
            parameter = self.session.scalar(
                select(SmokeParameterORM).where(
                    SmokeParameterORM.operation_key == operation_key,
                    SmokeParameterORM.input_node_id == input_node_id,
                )
            )
            if parameter is None:
                histories.append(ParameterHistory(input_node_id=input_node_id))
                continue
            failure_ids = self.session.scalars(
                select(SmokeInvestigationORM.failure_id)
                .join(
                    SmokeInvestigationParameterORM,
                    SmokeInvestigationParameterORM.investigation_id
                    == SmokeInvestigationORM.id,
                )
                .where(
                    SmokeInvestigationParameterORM.parameter_id == parameter.id
                )
                .distinct()
                .order_by(SmokeInvestigationORM.failure_id)
            ).all()
            histories.append(
                ParameterHistory(
                    input_node_id=input_node_id,
                    failures=self.lookup_failure_history(
                        operation_key,
                        list(failure_ids),
                    ),
                )
            )
        return histories

    def _count_observations(self, failure_id: str) -> int:
        """Count linked Observations without loading their JSON projections."""
        value = self.session.scalar(
            select(func.count())
            .select_from(SmokeFailureObservationORM)
            .where(SmokeFailureObservationORM.failure_id == failure_id)
        )
        return int(value or 0)

    def _count_investigations(self, failure_id: str) -> int:
        """Count chronological Solve conclusions for the compact catalog."""
        value = self.session.scalar(
            select(func.count())
            .select_from(SmokeInvestigationORM)
            .where(SmokeInvestigationORM.failure_id == failure_id)
        )
        return int(value or 0)

    def _count_applied_patches(self, failure_id: str) -> int:
        """Count only durable Patches, never rejected session candidates."""
        value = self.session.scalar(
            select(func.count())
            .select_from(SmokeAppliedPatchORM)
            .join(
                SmokeInvestigationORM,
                SmokeInvestigationORM.id
                == SmokeAppliedPatchORM.investigation_id,
            )
            .where(SmokeInvestigationORM.failure_id == failure_id)
        )
        return int(value or 0)

    def _resolve_failure(
        self,
        *,
        operation_key: str,
        failure_id: str | None,
        summary: str,
    ) -> SmokeFailureORM:
        """Create a Failure or validate one request-local alias resolution."""
        if failure_id is not None:
            failure = self._require_failure(
                operation_key=operation_key,
                failure_id=failure_id,
            )
            # Planner may improve wording without changing semantic identity.
            failure.summary = summary
            return failure
        failure = SmokeFailureORM(
            id=f"smoke_failure_{uuid4().hex}",
            operation_key=operation_key,
            summary=summary,
        )
        self.session.add(failure)
        self.session.flush()
        return failure

    def _require_failure(
        self,
        *,
        operation_key: str,
        failure_id: str,
    ) -> SmokeFailureORM:
        """Reject missing and cross-operation Failure references uniformly."""
        failure = self.session.get(SmokeFailureORM, failure_id)
        if failure is None or failure.operation_key != operation_key:
            raise SmokeMemoryReferenceError(
                f"Failure {failure_id!r} does not belong to {operation_key}"
            )
        return failure

    def _resolve_observation(self, *, write, observation) -> SmokeObservationORM:
        """Reuse a Batch case so multiple Failures can link to one Observation."""
        row = self.session.scalar(
            select(SmokeObservationORM).where(
                SmokeObservationORM.operation_key == write.operation_key,
                SmokeObservationORM.batch_run_id == write.batch_run_id,
                SmokeObservationORM.observation_key
                == observation.observation_key,
            )
        )
        if row is None:
            row = SmokeObservationORM(
                id=f"smoke_obs_{uuid4().hex}",
                operation_key=write.operation_key,
                batch_run_id=write.batch_run_id,
                round_number=write.round_number,
                observation_key=observation.observation_key,
                trigger=observation.trigger,
                response_summary=observation.response_summary,
                necessary_values=observation.necessary_values,
            )
            self.session.add(row)
            self.session.flush()
        return row

    def _resolve_parameter(
        self,
        *,
        operation_key: str,
        input_node_id: str,
    ) -> SmokeParameterORM:
        """Return the one row for an operation-local input identity."""
        row = self.session.scalar(
            select(SmokeParameterORM).where(
                SmokeParameterORM.operation_key == operation_key,
                SmokeParameterORM.input_node_id == input_node_id,
            )
        )
        if row is None:
            row = SmokeParameterORM(
                id=f"smoke_parameter_{uuid4().hex}",
                operation_key=operation_key,
                input_node_id=input_node_id,
            )
            self.session.add(row)
            self.session.flush()
        return row

    def _failure_history(self, failure: SmokeFailureORM) -> FailureHistory:
        """Assemble one deep read projection from normalized rows."""
        link_rows = self.session.execute(
            select(SmokeFailureObservationORM, SmokeObservationORM)
            .join(
                SmokeObservationORM,
                SmokeObservationORM.id
                == SmokeFailureObservationORM.observation_id,
            )
            .where(SmokeFailureObservationORM.failure_id == failure.id)
            .order_by(
                SmokeObservationORM.round_number,
                SmokeObservationORM.created_at,
            )
        ).all()
        observations = [
            FailureObservationMemory(
                batch_run_id=observation.batch_run_id,
                round_number=observation.round_number,
                observation_key=observation.observation_key,
                trigger=observation.trigger,
                response_summary=dict(observation.response_summary),
                necessary_values=dict(observation.necessary_values),
                disposition=link.disposition,
                disposition_reason=link.disposition_reason,
            )
            for link, observation in link_rows
        ]
        investigations = self.session.scalars(
            select(SmokeInvestigationORM)
            .where(SmokeInvestigationORM.failure_id == failure.id)
            .order_by(
                SmokeInvestigationORM.round_number,
                SmokeInvestigationORM.created_at,
            )
        ).all()
        return FailureHistory(
            failure_id=failure.id,
            summary=failure.summary,
            observations=observations,
            investigations=[
                self._investigation_memory(investigation)
                for investigation in investigations
            ],
        )

    def _investigation_memory(
        self,
        investigation: SmokeInvestigationORM,
    ) -> InvestigationMemory:
        """Assemble Parameter links and optional applied Patch for one Solve."""
        parameter_rows = self.session.execute(
            select(SmokeInvestigationParameterORM, SmokeParameterORM)
            .join(
                SmokeParameterORM,
                SmokeParameterORM.id
                == SmokeInvestigationParameterORM.parameter_id,
            )
            .where(
                SmokeInvestigationParameterORM.investigation_id
                == investigation.id
            )
            .order_by(SmokeParameterORM.input_node_id)
        ).all()
        patch = self.session.scalar(
            select(SmokeAppliedPatchORM).where(
                SmokeAppliedPatchORM.investigation_id == investigation.id
            )
        )
        return InvestigationMemory(
            investigation_id=investigation.id,
            round_number=investigation.round_number,
            outcome=investigation.outcome,
            trigger_conditions=investigation.trigger_conditions,
            root_cause=investigation.root_cause,
            solution=investigation.solution,
            evidence_source=investigation.evidence_source,
            conflict_reason=investigation.conflict_reason,
            parameters=[
                InvestigationParameterWrite(
                    input_node_id=parameter.input_node_id,
                    cause_summary=link.cause_summary,
                )
                for link, parameter in parameter_rows
            ],
            applied_patch=(
                AppliedPatchMemory(
                    generator_revision=patch.generator_revision,
                    patch=dict(patch.patch),
                    before_generators=dict(patch.before_generators),
                    after_generators=dict(patch.after_generators),
                    samples=list(patch.samples),
                )
                if patch is not None
                else None
            ),
        )
