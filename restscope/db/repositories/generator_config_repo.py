"""SQLAlchemy adapter for generator configuration persistence."""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from restscope.testing.models import InputGeneratorConfig, OperationGeneratorConfig
from restscope.testing.ports import GeneratorConfigConcurrentWrite

from ..orm import (
    GeneratorCatalogStateORM,
    InputGeneratorConfigORM,
    OperationGeneratorConfigORM,
)


class SqlAlchemyGeneratorConfigRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def is_initialized(self) -> bool:
        return self.session.get(GeneratorCatalogStateORM, 1) is not None

    def initialize(self, records: list[OperationGeneratorConfig]) -> None:
        self.session.add(GeneratorCatalogStateORM(id=1))
        for record in records:
            self._insert_record(record)
        try:
            self.session.flush()
        except IntegrityError as exc:
            raise GeneratorConfigConcurrentWrite("generator_catalog_state") from exc

    def get(self, operation_key: str) -> OperationGeneratorConfig | None:
        operation = self.session.get(OperationGeneratorConfigORM, operation_key)
        if operation is None:
            return None
        rows = self.session.scalars(
            select(InputGeneratorConfigORM)
            .where(InputGeneratorConfigORM.operation_key == operation_key)
            .order_by(InputGeneratorConfigORM.position)
        ).all()
        return OperationGeneratorConfig(
            operation_key=operation.operation_key,
            revision=operation.revision,
            snapshot=operation.snapshot,
            enabled=operation.enabled,
            disabled_reasons=operation.disabled_reasons,
            active_media_type=operation.active_media_type,
            configs=[
                InputGeneratorConfig(
                    input_node_id=row.input_node_id,
                    inclusion_probability=row.inclusion_probability,
                    strategy=row.strategy,
                )
                for row in rows
            ],
        )

    def replace(
        self,
        *,
        operation_key: str,
        expected_revision: int,
        revision: int,
        snapshot: dict,
        enabled: bool,
        disabled_reasons: list[dict],
        active_media_type: str | None,
        configs: list[InputGeneratorConfig],
    ) -> OperationGeneratorConfig:
        updated = self.session.execute(
            update(OperationGeneratorConfigORM)
            .where(
                OperationGeneratorConfigORM.operation_key == operation_key,
                OperationGeneratorConfigORM.revision == expected_revision,
            )
            .values(
                revision=revision,
                snapshot=snapshot,
                enabled=enabled,
                disabled_reasons=disabled_reasons,
                active_media_type=active_media_type,
            )
        )
        if updated.rowcount != 1:
            raise GeneratorConfigConcurrentWrite(operation_key)
        self.session.execute(
            delete(InputGeneratorConfigORM).where(
                InputGeneratorConfigORM.operation_key == operation_key
            )
        )
        self._insert_input_configs(operation_key, configs)
        self.session.flush()
        record = self.get(operation_key)
        assert record is not None
        return record

    def _insert_record(self, record: OperationGeneratorConfig) -> None:
        self.session.add(
            OperationGeneratorConfigORM(
                operation_key=record.operation_key,
                revision=record.revision,
                snapshot=record.snapshot.model_dump(mode="json"),
                enabled=record.enabled,
                disabled_reasons=[
                    item.model_dump(mode="json") for item in record.disabled_reasons
                ],
                active_media_type=record.active_media_type,
            )
        )
        self._insert_input_configs(record.operation_key, record.configs)

    def _insert_input_configs(
        self,
        operation_key: str,
        configs: list[InputGeneratorConfig],
    ) -> None:
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
