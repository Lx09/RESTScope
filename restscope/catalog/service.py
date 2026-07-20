"""Application service for validated OpenAPI source persistence."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from restscope.openapi_parser import OpenAPIParser, OpenAPISpecIR
from restscope.openapi_parser.loader import load_parse_input

from .models import SchemaRecord, SchemaSourceInput
from .ports import SchemaUnitOfWorkFactory


class SchemaNotFoundError(LookupError):
    """Raised when a requested stored schema does not exist."""


class SchemaSourceValidationError(ValueError):
    """Raised when a schema source cannot produce a valid OpenAPI IR."""


class SchemaCatalog:
    """Validate schema sources and persist them through an injected port."""

    def __init__(self, unit_of_work_factory: SchemaUnitOfWorkFactory) -> None:
        self.unit_of_work_factory = unit_of_work_factory

    def register(self, source: SchemaSourceInput) -> SchemaRecord:
        file_path, raw_content = self._prepare(source)
        self._parse(file_path=file_path, raw_content=raw_content)
        with self.unit_of_work_factory() as uow:
            record = uow.schemas.add(
                id=f"schema_{uuid4().hex}",
                file_path=file_path,
                raw_content=raw_content,
            )
            uow.commit()
            return record

    def get(self, schema_id: str) -> SchemaRecord:
        with self.unit_of_work_factory() as uow:
            record = uow.schemas.get(schema_id)
        if record is None:
            raise SchemaNotFoundError(f"Schema not found: {schema_id}")
        return record

    def list(self) -> list[SchemaRecord]:
        with self.unit_of_work_factory() as uow:
            return uow.schemas.list()

    def replace(self, schema_id: str, source: SchemaSourceInput) -> SchemaRecord:
        file_path, raw_content = self._prepare(source)
        self._parse(file_path=file_path, raw_content=raw_content)
        with self.unit_of_work_factory() as uow:
            record = uow.schemas.replace_source(
                schema_id,
                file_path=file_path,
                raw_content=raw_content,
            )
            if record is None:
                raise SchemaNotFoundError(f"Schema not found: {schema_id}")
            uow.commit()
            return record

    def load(self, schema_id: str) -> OpenAPISpecIR:
        record = self.get(schema_id)
        return self._parse(file_path=record.file_path, raw_content=record.raw_content)

    @staticmethod
    def _prepare(source: SchemaSourceInput) -> tuple[str | None, str | None]:
        if source.file_path is None:
            return None, source.raw_content

        path = source.file_path.expanduser().resolve()
        if not path.is_file() or not os.access(path, os.R_OK):
            raise SchemaSourceValidationError(f"OpenAPI file is not readable: {path}")
        return str(path), None

    @staticmethod
    def _parse(*, file_path: str | None, raw_content: str | None) -> OpenAPISpecIR:
        source: object = Path(file_path) if file_path is not None else raw_content
        try:
            parse_input = load_parse_input(source)
            ir = OpenAPIParser.parse(parse_input.raw_document)
        except Exception as exc:
            raise SchemaSourceValidationError(str(exc)) from exc

        errors = [
            *ir.diagnostics.spec_errors,
            *ir.diagnostics.path_errors,
            *ir.diagnostics.operation_errors,
        ]
        if errors:
            raise SchemaSourceValidationError(
                f"OpenAPI parsing produced {len(errors)} error diagnostic(s)"
            )
        return ir
