"""IR-only OpenAPI investigation workspace and model-facing tools.

The workspace performs deterministic reads against ``OpenAPISpecIR``. The
tools adapt those reads to small model-facing payloads and keep the evidence
needed to verify the model's eventual conclusion. Neither layer reads the raw
OpenAPI file or builds a persistent search index.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from restscope.llm import ToolSpec
from restscope.openapi_parser.ir import OperationIR, OpenAPISpecIR, SchemaIR

from restscope.operations import OperationReference
from .schemas import RetrievalEvidence, TargetParameterMatch, TargetParameterSummary


_TEMPLATE_SEGMENT = re.compile(r"^\{[^{}]+\}$")


class OpenAPIRetrievalQueryError(ValueError):
    """Stable IR query error raised before or during model investigation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _Symbol:
    """One searchable IR fact, already tied to its owning operation.

    ``name`` is the short value searched by the model, while ``location`` is a
    canonical, citable pointer. ``details`` holds the larger payload that is
    returned only when the evidence is expanded.
    """

    scope: str
    name: str
    operation: OperationReference
    location: str
    summary: str
    details: dict[str, Any]


# Arguments accepted by the internal ``openapi.find_operation`` tool.
class _FindOperationInput(BaseModel):
    method: str
    path: str
    parameter_name: str | None = None


# Arguments for a case-insensitive scan over selected IR symbol kinds.
class _SymbolSearchInput(BaseModel):
    query: str = Field(min_length=1)
    scopes: list[
        Literal[
            "operation",
            "parameter",
            "response_field",
            "response_header",
            "link",
        ]
    ] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=50)


# Arguments for expanding selected sections of one operation.
class _ReadOperationInput(BaseModel):
    operation_key: str
    sections: list[Literal["request", "responses", "links"]] = Field(default_factory=list)


# Arguments for expanding evidence previously returned in this request.
class _ReadEvidenceInput(BaseModel):
    evidence_ids: list[str] = Field(min_length=1, max_length=20)


class OpenAPIInvestigationWorkspace:
    """Deterministic, read-only-by-convention access to one parsed IR.

    This object does not decide which producer is correct. It resolves the
    consumer operation and exposes normalized IR lookups used by the model-facing
    tool layer.
    """

    def __init__(self, *, ir: OpenAPISpecIR) -> None:
        self.ir = ir

    def find_operation(self, *, method: str, path: str) -> OperationReference:
        normalized_method = method.strip().upper()
        # Prefer the schema's exact template. If the caller supplied a concrete
        # path (``/orders/123``), fall back to matching ``/orders/{orderId}``.
        exact = [
            operation
            for operation in self.ir.operations.values()
            if operation.method.upper() == normalized_method and operation.path == path
        ]
        matches = exact or [
            operation
            for operation in self.ir.operations.values()
            if operation.method.upper() == normalized_method and _path_matches(operation.path, path)
        ]
        if not matches:
            raise OpenAPIRetrievalQueryError(
                "consumer_operation_not_found",
                f"Consumer operation not found: {normalized_method} {path}",
            )
        if len(matches) > 1:
            keys = ", ".join(sorted(operation.operation_key for operation in matches))
            raise OpenAPIRetrievalQueryError(
                "ambiguous_consumer_operation",
                f"Consumer path matches multiple operations: {keys}",
            )
        return _operation_reference(matches[0])

    def operation(self, reference_or_key: OperationReference | str) -> OperationIR:
        if isinstance(reference_or_key, str):
            normalized_key = reference_or_key.strip().upper()
            for operation in self.ir.operations.values():
                if operation.operation_key.upper() == normalized_key:
                    return operation
            raise OpenAPIRetrievalQueryError("operation_not_found", f"Operation not found: {reference_or_key}")
        for operation in self.ir.operations.values():
            if _operation_reference(operation).identity() == reference_or_key.identity():
                return operation
        raise OpenAPIRetrievalQueryError(
            "operation_not_found",
            f"Operation not found: {reference_or_key.method} {reference_or_key.path}",
        )

    def find_target_parameter(
        self,
        operation: OperationReference,
        parameter_name: str,
    ) -> TargetParameterSummary:
        operation_ir = self.operation(operation)
        matches: list[TargetParameterMatch] = []
        for item in _request_items(operation_ir):
            location = str(item["param_location"])
            field_path = str(item.get("field_path") or "")
            name = str(item.get("param_name") or "")
            if location == "body":
                terminal = field_path.removesuffix("[]").rsplit(".", 1)[-1]
                matched = parameter_name in {field_path, terminal, name}
            elif location == "header":
                matched = name.casefold() == parameter_name.casefold()
            else:
                matched = name == parameter_name
            if matched:
                matches.append(
                    TargetParameterMatch(
                        location=location,
                        field_path=field_path,
                        required=bool(item.get("required")),
                        description=item.get("description"),
                    )
                )
        if not matches:
            raise OpenAPIRetrievalQueryError(
                "target_parameter_not_found",
                f"Parameter {parameter_name!r} is not defined by {operation.method} {operation.path}",
            )
        return TargetParameterSummary(name=parameter_name, matches=matches)


class OpenAPIInvestigationTools:
    """Model-facing tools plus request-scoped evidence for one in-memory IR.

    A new instance is created for every retrieval. Consequently evidence IDs
    cannot leak between investigations, even when the App-level IR is reused.
    """

    def __init__(self, workspace: OpenAPIInvestigationWorkspace) -> None:
        self.workspace = workspace
        # The public evidence record stays small; the paired details payload is
        # retained here for ``openapi.read_evidence`` within this request only.
        self._evidence: dict[str, tuple[RetrievalEvidence, dict[str, Any]]] = {}

    def specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                name="openapi.inspect",
                description="Inspect metadata and diagnostics for the bound OpenAPI IR.",
                kind="local_function",
                input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            ToolSpec(
                name="openapi.find_operation",
                description="Resolve a consumer method and template or concrete path.",
                kind="local_function",
                input_schema=_FindOperationInput.model_json_schema(),
            ),
            ToolSpec(
                name="openapi.search_symbols",
                description="Search OpenAPI operations, parameters, response fields, headers, and links.",
                kind="local_function",
                input_schema=_SymbolSearchInput.model_json_schema(),
            ),
            ToolSpec(
                name="openapi.read_operation",
                description="Read selected request, response, or link sections of one operation.",
                kind="local_function",
                input_schema=_ReadOperationInput.model_json_schema(),
            ),
            ToolSpec(
                name="openapi.read_evidence",
                description="Expand evidence returned by earlier searches.",
                kind="local_function",
                input_schema=_ReadEvidenceInput.model_json_schema(),
            ),
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "openapi.inspect":
            return self._inspect()
        if name == "openapi.find_operation":
            return self._find_operation(_FindOperationInput.model_validate(arguments))
        if name == "openapi.search_symbols":
            return self._search_symbols(_SymbolSearchInput.model_validate(arguments))
        if name == "openapi.read_operation":
            return self._read_operation(_ReadOperationInput.model_validate(arguments))
        if name == "openapi.read_evidence":
            return self._read_evidence(_ReadEvidenceInput.model_validate(arguments))
        raise OpenAPIRetrievalQueryError("unknown_internal_tool", f"Unknown internal tool: {name}")

    def evidence(self) -> list[RetrievalEvidence]:
        """Return every fact observed through tools during this investigation."""

        return [item[0] for item in self._evidence.values()]

    def _inspect(self) -> dict[str, Any]:
        diagnostics = self.workspace.ir.diagnostics
        return {
            "format": self.workspace.ir.meta.spec_format,
            "version": self.workspace.ir.meta.spec_version,
            "operation_count": len(self.workspace.ir.operations),
            "diagnostics": {
                "spec_errors": len(diagnostics.spec_errors),
                "spec_warnings": len(diagnostics.spec_warnings),
                "path_errors": len(diagnostics.path_errors),
                "operation_errors": len(diagnostics.operation_errors),
            },
        }

    def _find_operation(self, query: _FindOperationInput) -> dict[str, Any]:
        reference = self.workspace.find_operation(method=query.method, path=query.path)
        operation = self.workspace.operation(reference)
        location = operation.operation_key
        evidence = self._record_evidence(
            kind="operation",
            location=location,
            summary=f"{operation.operation_key}: {operation.summary or operation.description or ''}".strip(),
            operation=reference,
            details=self._operation_payload(operation, sections=["request"]),
        )
        output: dict[str, Any] = {
            "operation": reference.model_dump(mode="json"),
            "operation_key": operation.operation_key,
            "evidence_id": evidence.id,
            "request_parameters": operation.to_request_schema_json(),
        }
        if query.parameter_name:
            output["target_parameter"] = self.workspace.find_target_parameter(
                reference,
                query.parameter_name,
            ).model_dump(mode="json")
        return output

    def _search_symbols(self, query: _SymbolSearchInput) -> dict[str, Any]:
        scopes = set(query.scopes)
        needle = query.query.casefold()
        matches: list[dict[str, Any]] = []
        # Search is deliberately rebuilt from the current IR on every call. The
        # haystack includes semantic text and the owning operation ID, but each
        # match already carries the operation reference used during validation.
        for symbol in self._iter_symbols():
            if scopes and symbol.scope not in scopes:
                continue
            haystack = " ".join(
                [symbol.name, symbol.location, symbol.summary, symbol.operation.operation_id or ""]
            ).casefold()
            if needle not in haystack:
                continue
            evidence = self._record_evidence(
                kind=symbol.scope,
                location=symbol.location,
                summary=symbol.summary,
                operation=symbol.operation,
                details=symbol.details,
            )
            matches.append(
                {
                    "evidence_id": evidence.id,
                    "scope": symbol.scope,
                    "name": symbol.name,
                    "operation": symbol.operation.model_dump(mode="json"),
                    "location": symbol.location,
                    "summary": symbol.summary,
                }
            )
            if len(matches) >= query.limit:
                break
        return {"query": query.query, "results": matches, "truncated": len(matches) >= query.limit}

    def _read_operation(self, query: _ReadOperationInput) -> dict[str, Any]:
        operation = self.workspace.operation(query.operation_key)
        sections = query.sections or ["request", "responses", "links"]
        payload = self._operation_payload(operation, sections=sections)
        evidence = self._record_evidence(
            kind="operation",
            location=operation.operation_key,
            summary=f"Read {', '.join(sections)} for {operation.operation_key}",
            operation=_operation_reference(operation),
            details=payload,
        )
        return {"evidence_id": evidence.id, **payload}

    def _read_evidence(self, query: _ReadEvidenceInput) -> dict[str, Any]:
        values = []
        missing = []
        for evidence_id in query.evidence_ids:
            item = self._evidence.get(evidence_id)
            if item is None:
                missing.append(evidence_id)
                continue
            evidence, details = item
            values.append({**evidence.model_dump(mode="json"), "details": details})
        return {"evidence": values, "missing": missing}

    def _record_evidence(
        self,
        *,
        kind: str,
        location: str,
        summary: str,
        operation: OperationReference | None,
        details: dict[str, Any],
    ) -> RetrievalEvidence:
        operation_key = "" if operation is None else "|".join(str(part) for part in operation.identity())
        # The ID is derived from fact identity rather than discovery order, so
        # repeated searches of the same IR fact return the same reference.
        digest = hashlib.sha256(f"{kind}\0{location}\0{operation_key}".encode()).hexdigest()[:16]
        evidence = RetrievalEvidence(
            id=f"evidence:{digest}",
            operation=operation,
            kind=kind,
            location=location,
            summary=summary[:1200],
        )
        self._evidence[evidence.id] = (evidence, details)
        return evidence

    def _iter_symbols(self):
        """Yield searchable facts directly from the IR without retaining an index.

        The outer operation loop is the important association boundary: every
        request parameter, 2xx response field/header, and Link yielded inside
        it receives the same ``OperationReference``.
        """

        for operation in self.workspace.ir.operations.values():
            reference = _operation_reference(operation)
            operation_summary = " ".join(
                value for value in [operation.summary, operation.description] if value
            )
            yield _Symbol(
                scope="operation",
                name=operation.operation_id or operation.operation_key,
                operation=reference,
                location=operation.operation_key,
                summary=operation_summary or operation.operation_key,
                details={"operation_key": operation.operation_key},
            )
            for item in _request_items(operation):
                name = str(item.get("field_path") or item.get("param_name") or "")
                yield _Symbol(
                    scope="parameter",
                    name=name,
                    operation=reference,
                    location=f"{operation.operation_key} request:{item['param_location']}:{name}",
                    summary=f"Request {item['param_location']} parameter {name}: {item.get('schema_text', '')}",
                    details=item,
                )
            for status, response in operation.responses.by_status.items():
                # Producer evidence is limited to successful response contracts.
                if not _is_success_status(status):
                    continue
                for header_name, header in response.headers.items():
                    location = f"{operation.operation_key} response:{status}:header:{header_name}"
                    yield _Symbol(
                        scope="response_header",
                        name=header_name,
                        operation=reference,
                        location=location,
                        summary=f"Response {status} header {header_name}: {header.description or ''}".strip(),
                        details={"status": status, "header": header_name, "description": header.description},
                    )
                for media_type, media in response.contents.items():
                    if media.schema is None:
                        continue
                    for field_path, schema in _schema_fields(media.schema):
                        location = f"{operation.operation_key} response:{status}:{media_type}:{field_path}"
                        yield _Symbol(
                            scope="response_field",
                            name=field_path,
                            operation=reference,
                            location=location,
                            summary=f"Response {status} field {field_path}: {schema.to_compact_text()}",
                            details={
                                "status": status,
                                "media_type": media_type,
                                "field_path": field_path,
                                "schema": schema.to_compact_text(),
                                "description": schema.description,
                            },
                        )
                for link_name, link in response.links.items():
                    details = {
                        "status": status,
                        "link_name": link_name,
                        "operation_id": link.operation_id,
                        "operation_ref": link.operation_ref,
                        "parameters": link.parameters,
                        "description": link.description,
                    }
                    yield _Symbol(
                        scope="link",
                        name=link_name,
                        operation=reference,
                        location=f"{operation.operation_key} response:{status}:link:{link_name}",
                        summary=json.dumps(details, ensure_ascii=False, default=str),
                        details=details,
                    )

    def _operation_payload(self, operation: OperationIR, *, sections: list[str]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "operation": _operation_reference(operation).model_dump(mode="json"),
            "operation_key": operation.operation_key,
            "summary": operation.summary,
            "description": operation.description,
        }
        if "request" in sections:
            payload["request"] = _request_items(operation)
        if "responses" in sections:
            payload["responses"] = [
                {
                    "scope": symbol.scope,
                    "name": symbol.name,
                    "location": symbol.location,
                    "summary": symbol.summary,
                }
                for symbol in self._iter_symbols()
                if symbol.operation.identity() == _operation_reference(operation).identity()
                and symbol.scope in {"response_field", "response_header"}
            ]
        if "links" in sections:
            payload["links"] = [
                symbol.details
                for symbol in self._iter_symbols()
                if symbol.operation.identity() == _operation_reference(operation).identity()
                and symbol.scope == "link"
            ]
        return payload


def _path_matches(template: str, concrete: str) -> bool:
    template_segments = template.strip("/").split("/") if template != "/" else []
    concrete_segments = concrete.strip("/").split("/") if concrete != "/" else []
    if len(template_segments) != len(concrete_segments):
        return False
    return all(
        _TEMPLATE_SEGMENT.fullmatch(template_segment) is not None
        or template_segment == concrete_segment
        for template_segment, concrete_segment in zip(template_segments, concrete_segments, strict=True)
    )


def _operation_reference(operation: OperationIR) -> OperationReference:
    return OperationReference(
        method=operation.method,
        path=operation.path,
        operation_id=operation.operation_id,
    )


def _is_success_status(status: str) -> bool:
    normalized = status.upper()
    return normalized.startswith("2") and (normalized == "2XX" or normalized.isdigit())


def _request_items(operation: OperationIR) -> list[dict[str, Any]]:
    """Flatten request parameters and nested body leaves into searchable items."""

    items: list[dict[str, Any]] = []
    for location, parameters in (
        ("path", operation.path_parameters),
        ("query", operation.query_parameters),
        ("header", operation.header_parameters),
        ("cookie", operation.cookie_parameters),
    ):
        for parameter in parameters:
            items.append(
                {
                    "param_location": location,
                    "param_name": parameter.name,
                    "field_path": "",
                    "schema_text": (
                        parameter.schema.to_compact_text()
                        if parameter.schema is not None
                        else "type: unknown"
                    ),
                    "required": parameter.required,
                    "description": parameter.description,
                }
            )

    seen_body_fields: set[str] = set()
    if operation.request_body is not None:
        for media_type, media in operation.request_body.contents.items():
            if media.schema is None:
                continue
            for field_path, schema, required in _request_schema_fields(media.schema):
                if field_path in seen_body_fields:
                    continue
                seen_body_fields.add(field_path)
                items.append(
                    {
                        "param_location": "body",
                        "param_name": "request_body",
                        "field_path": field_path,
                        "schema_text": schema.to_compact_text(),
                        "required": required,
                        "description": schema.description,
                        "media_type": media_type,
                    }
                )
    return items


def _request_schema_fields(
    schema: SchemaIR,
    *,
    parent: str = "",
    required: bool = False,
    visited: set[int] | None = None,
) -> list[tuple[str, SchemaIR, bool]]:
    """Return request-body leaf paths while avoiding recursive-schema cycles."""

    # Copying ``visited`` makes cycle detection local to this recursion branch;
    # a schema reused by a sibling branch can still appear at both valid paths.
    visited = set() if visited is None else set(visited)
    if id(schema) in visited:
        return []
    visited.add(id(schema))
    output: list[tuple[str, SchemaIR, bool]] = []
    if schema.type == "object" and schema.properties:
        for name, child in schema.properties.items():
            path = f"{parent}.{name}" if parent else name
            output.extend(
                _request_schema_fields(
                    child,
                    parent=path,
                    required=name in schema.required,
                    visited=visited,
                )
            )
    for branch in [*schema.all_of, *schema.any_of, *schema.one_of]:
        output.extend(
            _request_schema_fields(
                branch,
                parent=parent,
                required=required,
                visited=visited,
            )
        )
    if output:
        return output
    if schema.type == "array" and schema.items is not None:
        path = f"{parent}[]" if parent else "[]"
        return _request_schema_fields(
            schema.items,
            parent=path,
            required=required,
            visited=visited,
        )
    return [(parent, schema, required)]


def _schema_fields(
    schema: SchemaIR,
    *,
    parent: str = "",
    visited: set[int] | None = None,
) -> list[tuple[str, SchemaIR]]:
    """Expand response schemas into citable dotted paths, including arrays/unions."""

    visited = set() if visited is None else set(visited)
    if id(schema) in visited:
        return []
    visited.add(id(schema))
    output: list[tuple[str, SchemaIR]] = []
    for name, child in schema.properties.items():
        path = f"{parent}.{name}" if parent else name
        output.append((path, child))
        output.extend(_schema_fields(child, parent=path, visited=visited))
    if schema.items is not None:
        item_path = f"{parent}[]" if parent else "[]"
        output.extend(_schema_fields(schema.items, parent=item_path, visited=visited))
    for branch in [*schema.all_of, *schema.any_of, *schema.one_of]:
        output.extend(_schema_fields(branch, parent=parent, visited=visited))
    return output
