"""SQLAlchemy adapter for current per-input Generator configuration."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from restscope.request_generation.models import InputGeneratorConfig
from restscope.request_generation.constraints import OperationConstraintRecord
from restscope.request_generation.ports import GeneratorConfigConcurrentWrite

from ..orm import GeneratorChangeEventORM, InputGeneratorConfigORM, OperationConstraintORM
from ._transaction import _SqlAlchemyUnitOfWork


class SqlAlchemyGeneratorConfigRepository:
    """Persist Generator input rows without operation snapshots or revisions."""

    def __init__(self, session: Session) -> None:
        """Use the caller-owned session and transaction."""

        self.session = session

    def initialize(
        self,
        records: list[tuple[str, list[InputGeneratorConfig]]],
    ) -> None:
        """Insert every initial input once into an empty one-shot database."""

        existing = self.session.scalar(
            select(func.count()).select_from(InputGeneratorConfigORM)
        )
        if existing:
            raise GeneratorConfigConcurrentWrite("generator_catalog_initialized")
        for operation_key, configs in records:
            self._insert_inputs(operation_key, configs)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise GeneratorConfigConcurrentWrite("generator_catalog_initialized") from exc

    def get_inputs(self, operation_key: str) -> list[InputGeneratorConfig]:
        """Return current inputs in their deterministic snapshot order."""

        rows = self.session.scalars(
            select(InputGeneratorConfigORM)
            .where(InputGeneratorConfigORM.operation_key == operation_key)
            .order_by(InputGeneratorConfigORM.position)
        ).all()
        return [_to_input(row) for row in rows]

    def replace_inputs(
        self,
        *,
        operation_key: str,
        expected: list[InputGeneratorConfig],
        updated: list[InputGeneratorConfig],
    ) -> None:
        """Compare current content, then update only rows that actually changed."""

        current_rows = self.session.scalars(
            select(InputGeneratorConfigORM)
            .where(InputGeneratorConfigORM.operation_key == operation_key)
            .order_by(InputGeneratorConfigORM.position)
        ).all()
        current = [_to_input(row) for row in current_rows]
        if current != expected:
            raise GeneratorConfigConcurrentWrite(operation_key)
        if len(updated) != len(current_rows):
            raise ValueError("A Generator Patch cannot add or remove input nodes")
        by_id = {row.input_node_id: row for row in current_rows}
        if set(by_id) != {item.input_node_id for item in updated}:
            raise ValueError("A Generator Patch must preserve the complete input set")
        for item in updated:
            row = by_id[item.input_node_id]
            if _to_input(row) == item:
                continue
            row.inclusion_probability = item.inclusion_probability
            row.strategy = item.strategy.model_dump(mode="json")
        self.session.flush()

    def get_constraints(self, operation_key: str) -> list[OperationConstraintRecord]:
        """Return current normalized Constraints for one operation."""

        rows = self.session.scalars(
            select(OperationConstraintORM)
            .where(OperationConstraintORM.operation_key == operation_key)
            .order_by(OperationConstraintORM.id)
        ).all()
        return [
            OperationConstraintRecord.model_validate(
                {
                    "id": row.id,
                    "operation_key": row.operation_key,
                    "owner_input_node_ids": row.owner_input_node_ids,
                    "kind": row.kind,
                    "constraint": row.expression,
                }
            )
            for row in rows
        ]

    def replace_constraints(
        self,
        *,
        operation_key: str,
        expected: list[OperationConstraintRecord],
        updated: list[OperationConstraintRecord],
    ) -> None:
        """Compare current Constraint content, then insert/delete its exact diff."""

        current = self.get_constraints(operation_key)
        if current != sorted(expected, key=lambda item: item.id):
            raise GeneratorConfigConcurrentWrite(operation_key)
        current_by_id = {item.id: item for item in current}
        updated_by_id = {item.id: item for item in updated}
        if len(updated_by_id) != len(updated):
            raise ValueError("Constraint identities must be unique")
        for constraint_id in sorted(set(current_by_id) - set(updated_by_id)):
            self.session.execute(
                delete(OperationConstraintORM).where(
                    OperationConstraintORM.id == constraint_id
                )
            )
        for constraint_id in sorted(set(updated_by_id) - set(current_by_id)):
            item = updated_by_id[constraint_id]
            if item.operation_key != operation_key:
                raise ValueError("A Constraint cannot move between operations")
            self.session.add(
                OperationConstraintORM(
                    id=item.id,
                    operation_key=item.operation_key,
                    owner_input_node_ids=list(item.owner_input_node_ids),
                    kind=item.kind,
                    expression=item.constraint.model_dump(mode="json"),
                )
            )
        for constraint_id in set(current_by_id) & set(updated_by_id):
            if current_by_id[constraint_id] != updated_by_id[constraint_id]:
                raise ValueError("A stable Constraint ID cannot change content")
        self.session.flush()

    def record_change_event(
        self,
        *,
        solve_attempt_id: str,
        operation_key: str,
        reason: str,
        generator_changes: list[dict],
        constraint_changes: list[dict],
    ) -> str:
        """Append one accepted deterministic Patch diff and return its identity."""

        event_id = f"generator_change_{uuid4().hex}"
        self.session.add(
            GeneratorChangeEventORM(
                id=event_id,
                solve_attempt_id=solve_attempt_id,
                operation_key=operation_key,
                reason=reason,
                generator_changes=generator_changes,
                constraint_changes=constraint_changes,
            )
        )
        self.session.flush()
        return event_id

    def _insert_inputs(
        self,
        operation_key: str,
        configs: list[InputGeneratorConfig],
    ) -> None:
        """Add a complete ordered input set to the active session."""

        self.session.add_all(
            [
                InputGeneratorConfigORM(
                    input_node_id=config.input_node_id,
                    operation_key=operation_key,
                    position=position,
                    inclusion_probability=config.inclusion_probability,
                    strategy=config.strategy.model_dump(mode="json"),
                )
                for position, config in enumerate(configs)
            ]
        )


def _to_input(row: InputGeneratorConfigORM) -> InputGeneratorConfig:
    """Validate a stored JSON strategy before it reaches generation code."""

    return InputGeneratorConfig.model_validate(
        {
            "input_node_id": row.input_node_id,
            "inclusion_probability": row.inclusion_probability,
            "strategy": row.strategy,
        }
    )


class SqlAlchemyGeneratorConfigUnitOfWork(_SqlAlchemyUnitOfWork):
    """Open one transaction for current request-generation configuration."""

    def __enter__(self) -> "SqlAlchemyGeneratorConfigUnitOfWork":
        """Bind the Generator repository to a newly opened session."""

        self.generator_configs = SqlAlchemyGeneratorConfigRepository(
            self._open_session()
        )
        return self
