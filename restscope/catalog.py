"""One-time OpenAPI catalog initialization for database-only agents."""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.exc import IntegrityError

from restscope.db import UnitOfWork, create_engine_from_config
from restscope.db.ids import new_operation_edge_id, new_operation_id, new_schema_id
from restscope.db.session import make_session_factory
from restscope.db.time import utc_now
from restscope.openapi_parser import OpenAPIParser
from restscope.openapi_parser.loader import load_parse_input
from restscope.restscope_config import RESTScopeConfig


PARSER_VERSION = "restscope-openapi-parser-v1"


class OpenAPIInitializationRequest(BaseModel):
    """Input accepted by the one-time catalog bootstrap."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: Any
    name: str
    version: str | None = None


class OpenAPIInitializationResult(BaseModel):
    schema_id: str
    spec_hash: str
    operation_count: int
    warning_count: int
    status: Literal["ready"] = "ready"


class CatalogInitializationError(RuntimeError):
    """Stable initialization failure surfaced by the public bootstrap API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def initialize_openapi_catalog(
    config: RESTScopeConfig,
    request: OpenAPIInitializationRequest,
) -> OpenAPIInitializationResult:
    """Load and persist one OpenAPI catalog, rejecting every later attempt."""

    engine = create_engine_from_config(config.db)
    session_factory = make_session_factory(engine)

    # This check intentionally precedes all source handling. Once initialized,
    # raw_spec_uri is provenance only and must never be dereferenced again.
    with UnitOfWork(session_factory) as uow:
        if uow.schemas.get_ready() is not None:
            raise CatalogInitializationError(
                "schema_already_initialized",
                "An OpenAPI catalog is already initialized.",
            )

    try:
        parse_input = load_parse_input(request.source)
        raw_document = parse_input.raw_document
        ir = OpenAPIParser.parse(raw_document)
    except Exception as exc:
        raise CatalogInitializationError("openapi_parse_failed", str(exc)) from exc

    diagnostics = asdict(ir.diagnostics)
    errors = [
        *ir.diagnostics.spec_errors,
        *ir.diagnostics.path_errors,
        *ir.diagnostics.operation_errors,
    ]
    if errors:
        raise CatalogInitializationError(
            "openapi_parse_failed",
            f"OpenAPI parsing produced {len(errors)} error diagnostic(s).",
        )

    canonical = json.dumps(raw_document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    spec_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    schema_id = new_schema_id()
    operation_ids = {key: new_operation_id() for key in ir.operations}

    try:
        with UnitOfWork(session_factory) as uow:
            if uow.schemas.get_ready() is not None:
                raise CatalogInitializationError(
                    "schema_already_initialized",
                    "An OpenAPI catalog is already initialized.",
                )

            uow.schemas.add(
                id=schema_id,
                name=request.name,
                version=request.version or ir.meta.version,
                spec_hash=spec_hash,
                raw_spec_uri=_source_uri(parse_input.source_location),
                openapi_version=ir.meta.spec_version,
                operation_count=len(ir.operations),
                normalized_spec_json=raw_document,
                parse_diagnostics_json=diagnostics,
                catalog_status="ready",
                catalog_slot="default",
                parser_version=PARSER_VERSION,
                initialized_at=utc_now(),
            )

            for operation_key, operation in ir.operations.items():
                card = ir.indexes.operation_cards[operation_key]
                operation_db_id = operation_ids[operation_key]
                raw_operation = raw_document.get("paths", {}).get(operation.path, {}).get(
                    operation.method, {}
                )
                request_refs = _collect_refs(
                    {
                        "parameters": raw_operation.get("parameters", []),
                        "requestBody": raw_operation.get("requestBody"),
                    }
                )
                response_refs = _collect_refs(raw_operation.get("responses", {}))
                uow.operations.add(
                    id=operation_db_id,
                    schema_id=schema_id,
                    operation_id=operation.operation_id,
                    method=operation.method.upper(),
                    path=operation.path,
                    tags=operation.tags,
                    summary=operation.summary,
                    resource=card.resource_name,
                    mutability=card.action,
                    security=asdict(operation.security),
                    request_schema_refs=request_refs,
                    response_schema_refs=response_refs,
                    card_json=asdict(card),
                    static_risk_score=Decimal(str(min(1.0, len(card.risk_signals) / 10))),
                )
                uow.intelligence.add(operation_id=operation_db_id, schema_id=schema_id)

            for edge in ir.indexes.flow_graph.edges:
                source_id = operation_ids.get(edge.source_operation_key)
                target_id = operation_ids.get(edge.target_operation_key)
                if source_id is None or target_id is None:
                    continue
                uow.operation_edges.add(
                    id=new_operation_edge_id(),
                    schema_id=schema_id,
                    source_operation_id=source_id,
                    target_operation_id=target_id,
                    edge_type=edge.edge_type,
                    value=edge.value,
                    confidence=edge.confidence,
                    status=edge.status,
                    reason=edge.reason,
                )
            uow.commit()
    except CatalogInitializationError:
        raise
    except IntegrityError as exc:
        raise CatalogInitializationError(
            "schema_already_initialized",
            "An OpenAPI catalog is already initialized.",
        ) from exc

    warning_count = len(ir.diagnostics.spec_warnings)
    return OpenAPIInitializationResult(
        schema_id=schema_id,
        spec_hash=spec_hash,
        operation_count=len(ir.operations),
        warning_count=warning_count,
    )


def _source_uri(source_location: str | None) -> str:
    if source_location is None:
        return "memory://openapi"
    if source_location.startswith(("http://", "https://")):
        return source_location
    return Path(source_location).resolve().as_uri()


def _collect_refs(value: Any) -> list[str]:
    refs: set[str] = set()

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            ref = item.get("$ref")
            if isinstance(ref, str):
                refs.add(ref)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return sorted(refs)
