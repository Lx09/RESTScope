"""IR-first registration and extraction for reusable response-value pools."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from restscope.llm import (
    LLMClient,
    LLMMessage,
    LLMModelConfig,
    LLMRequest,
    OutputValidator,
)
from restscope.openapi_parser import OpenAPISpecIR
from restscope.openapi_parser.ir import SchemaIR

from .contract_tracker import normalize_media_type
from .response_value_catalog import (
    PersistedResponseValueSource,
    ResponseValueCatalog,
    ResponseValueCatalogRegistration,
    ResponseValueSource,
)


@dataclass(frozen=True, slots=True)
class ResponseValueRegistrationResult:
    status: str
    monitor_id: str
    value_name: str
    sources: list[PersistedResponseValueSource]


@dataclass(frozen=True, slots=True)
class ResponseValueObservationResult:
    sources_processed: int = 0
    values_recorded: int = 0


@dataclass(frozen=True, slots=True)
class ResponseValuePreview:
    """Non-persistent proof that selected IR sources already have values."""

    value_name: str
    value_count: int
    sources: list[ResponseValueSource]


@dataclass(frozen=True, slots=True)
class ResponseValueSourceOption:
    """One IR response field backed by compatible persisted scalar evidence."""

    value_name: str
    source: ResponseValueSource
    compatible_scalar_type: str | None
    value_count: int


class ResponseValueUnavailableError(RuntimeError):
    """The current IR and observation history cannot supply a non-empty pool."""

    code = "response_value_pool_unavailable"


@dataclass(frozen=True, slots=True)
class _SourceCandidate:
    source: ResponseValueSource
    field_type: str | list[str] | None
    schema_format: str | None
    description: str | None


class _SourceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_ids: list[str] = Field(max_length=100)


class ResponseValueTracker:
    """Bind consumer inputs to IR-selected producer fields and observed values."""

    def __init__(
        self,
        *,
        catalog: ResponseValueCatalog,
        client: LLMClient | None = None,
        model: LLMModelConfig | None = None,
        validator: OutputValidator | None = None,
    ) -> None:
        self.catalog = catalog
        self.client = client
        self.model = model
        self.validator = validator or OutputValidator()

    def register(
        self,
        *,
        ir: OpenAPISpecIR,
        consumer_operation_key: str,
        consumer_input_node_id: str,
        parameter_name: str,
        expected_type: str | None,
    ) -> ResponseValueRegistrationResult:
        preview = self.preview(
            ir=ir,
            consumer_operation_key=consumer_operation_key,
            consumer_input_node_id=consumer_input_node_id,
            parameter_name=parameter_name,
            expected_type=expected_type,
        )
        if preview is None:
            raise ResponseValueUnavailableError(
                "No IR response source has compatible persisted values"
            )
        return self.register_selected_sources(
            consumer_operation_key=consumer_operation_key,
            consumer_input_node_id=consumer_input_node_id,
            parameter_name=parameter_name,
            expected_type=expected_type,
            sources=preview.sources,
        )

    def register_selected_sources(
        self,
        *,
        consumer_operation_key: str,
        consumer_input_node_id: str,
        parameter_name: str,
        expected_type: str | None,
        sources: list[ResponseValueSource],
    ) -> ResponseValueRegistrationResult:
        """Atomically register only the source fields selected by Smoke."""

        value_name = _value_name(
            consumer_operation_key,
            consumer_input_node_id,
        )
        registration = ResponseValueCatalogRegistration(
            value_name=value_name,
            consumer_operation_key=consumer_operation_key,
            consumer_input_node_id=consumer_input_node_id,
            parameter_name=parameter_name,
            expected_type=expected_type,
        )
        try:
            monitor, sources = self.catalog.register_with_backfill(
                registration,
                sources,
            )
        except ValueError as exc:
            raise ResponseValueUnavailableError(str(exc)) from exc
        return ResponseValueRegistrationResult(
            status="registered" if monitor.created else "existing",
            monitor_id=monitor.monitor_id,
            value_name=monitor.value_name,
            sources=sources,
        )

    def available_source_options(
        self,
        *,
        ir: OpenAPISpecIR,
        consumer_operation_key: str,
        consumer_input_node_id: str,
        expected_type: str | None,
    ) -> list[ResponseValueSourceOption]:
        """Return every IR field that already has compatible historical values."""

        value_name = _value_name(
            consumer_operation_key,
            consumer_input_node_id,
        )
        options: list[ResponseValueSourceOption] = []
        for candidate in _source_candidates(ir, expected_type=expected_type):
            values = [
                value
                for value in self.catalog.historical_values_for_source(
                    candidate.source,
                    limit=100,
                )
                if _observed_type_compatible(expected_type, value)
            ]
            values = _deduplicate_typed_values(values)
            if not values:
                continue
            options.append(
                ResponseValueSourceOption(
                    value_name=value_name,
                    source=candidate.source,
                    compatible_scalar_type=expected_type,
                    value_count=len(values),
                )
            )
        return options[:100]

    def preview(
        self,
        *,
        ir: OpenAPISpecIR,
        consumer_operation_key: str,
        consumer_input_node_id: str,
        parameter_name: str,
        expected_type: str | None,
    ) -> ResponseValuePreview | None:
        """Return only IR sources backed by currently persisted observations."""

        sources = self._select_sources(
            ir,
            parameter_name=parameter_name,
            expected_type=expected_type,
        )
        backed_sources: list[ResponseValueSource] = []
        observed_values: list[object] = []
        for source in sources:
            values = self.catalog.historical_values_for_source(
                source,
                limit=100,
            )
            values = [
                value
                for value in values
                if _observed_type_compatible(expected_type, value)
            ]
            if not values:
                continue
            backed_sources.append(source)
            observed_values.extend(values)
        deduplicated = _deduplicate_typed_values(observed_values)
        if not backed_sources or not deduplicated:
            return None
        return ResponseValuePreview(
            value_name=_value_name(
                consumer_operation_key,
                consumer_input_node_id,
            ),
            value_count=len(deduplicated),
            sources=backed_sources,
        )

    def refresh_sources(
        self,
        *,
        ir: OpenAPISpecIR,
        producer_operation_key: str,
    ) -> int:
        selected_count = 0
        for monitor in self.catalog.list_active_monitors():
            sources = self._select_sources(
                ir,
                parameter_name=monitor.parameter_name,
                expected_type=monitor.expected_type,
                producer_operation_key=producer_operation_key,
            )
            if not sources:
                continue
            self.catalog.add_sources(monitor.monitor_id, sources)
            selected_count += len(sources)
        return selected_count

    def _select_sources(
        self,
        ir: OpenAPISpecIR,
        *,
        parameter_name: str,
        expected_type: str | None,
        producer_operation_key: str | None = None,
    ) -> list[ResponseValueSource]:
        all_candidates = _source_candidates(
            ir,
            expected_type=expected_type,
            producer_operation_key=producer_operation_key,
        )
        target_name = _normalize_identifier(parameter_name)
        exact = [
            item.source
            for item in all_candidates
            if _normalize_identifier(item.source.field_name) == target_name
        ]
        return exact or self._semantic_sources(
            parameter_name=parameter_name,
            expected_type=expected_type,
            candidates=all_candidates,
        )

    def _semantic_sources(
        self,
        *,
        parameter_name: str,
        expected_type: str | None,
        candidates: list[_SourceCandidate],
    ) -> list[ResponseValueSource]:
        if (
            not candidates
            or self.client is None
            or self.model is None
            or not self.model.enabled
        ):
            return []
        bounded = candidates[:100]
        by_id = {
            f"c{index}": candidate
            for index, candidate in enumerate(bounded, start=1)
        }
        payload = {
            "consumer": {
                "parameter_name": parameter_name,
                "expected_type": expected_type,
            },
            "candidates": [
                {
                    "candidate_id": candidate_id,
                    "producer_operation_key": candidate.source.producer_operation_key,
                    "status_code": candidate.source.status_code,
                    "media_type": candidate.source.media_type,
                    "field_path": candidate.source.selector,
                    "field_name": candidate.source.field_name,
                    "type": candidate.field_type,
                    "format": candidate.schema_format,
                    "description": (
                        candidate.description[:200]
                        if candidate.description is not None
                        else None
                    ),
                }
                for candidate_id, candidate in by_id.items()
            ],
        }
        response = self.client.invoke(
            LLMRequest(
                provider=self.model.provider,
                model=self.model.model,
                messages=[
                    LLMMessage(
                        role="system",
                        content=(
                            "Select only response fields that can supply the "
                            "consumer parameter. Return candidate_ids only. "
                            "Do not explain, invent fields, or use actual values."
                        ),
                    ),
                    LLMMessage(
                        role="user",
                        content=json.dumps(
                            payload,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    ),
                ],
                temperature=self.model.temperature,
                max_tokens=self.model.max_tokens,
                response_format="json_schema",
                json_schema=_SourceSelection.model_json_schema(),
                json_schema_name="ResponseValueSourceSelection",
                tool_choice="none",
                timeout_seconds=self.model.timeout_seconds,
                reasoning=self.model.reasoning,
                metadata={"role": "api_behavior_monitor"},
            )
        )
        validation = self.validator.validate(
            response=response,
            output_model=_SourceSelection,
        )
        if not validation.valid:
            return []
        selection = _SourceSelection.model_validate(validation.validated_object)
        if len(selection.candidate_ids) != len(set(selection.candidate_ids)):
            return []
        if any(candidate_id not in by_id for candidate_id in selection.candidate_ids):
            return []
        return [
            by_id[candidate_id].source
            for candidate_id in selection.candidate_ids
        ]

    def observe(
        self,
        *,
        producer_operation_key: str,
        status_code: int,
        media_type: str | None,
        body: Any,
    ) -> ResponseValueObservationResult:
        normalized_media = normalize_media_type(media_type)
        if (
            normalized_media is None
            or not 200 <= status_code < 300
            or not _is_json_media_type(normalized_media)
        ):
            return ResponseValueObservationResult()
        self.catalog.record_observation(
            operation_key=producer_operation_key,
            status_code=status_code,
            media_type=normalized_media,
            scalars=_flatten_observed_scalars(body),
        )
        sources = [
            source
            for source in self.catalog.list_sources_for_operation(
                producer_operation_key
            )
            if _status_matches(source.status_code, status_code)
            and source.media_type == normalized_media
        ]
        values_recorded = 0
        for source in sources:
            values_recorded += self.catalog.record_values(
                source.monitor_id,
                _extract_selector_values(body, source.selector),
            )
        return ResponseValueObservationResult(
            sources_processed=len(sources),
            values_recorded=values_recorded,
        )


def _source_candidates(
    ir: OpenAPISpecIR,
    *,
    expected_type: str | None,
    producer_operation_key: str | None = None,
) -> list[_SourceCandidate]:
    output: list[_SourceCandidate] = []
    for operation_key, operation in ir.operations.items():
        if (
            producer_operation_key is not None
            and operation_key != producer_operation_key
        ):
            continue
        for status_code, response in operation.responses.by_status.items():
            if not _declared_success(status_code):
                continue
            for media_type, media in response.contents.items():
                normalized_media = normalize_media_type(media_type)
                if not _is_json_media_type(normalized_media) or media.schema is None:
                    continue
                for (
                    selector,
                    field_name,
                    field_type,
                    schema_format,
                    description,
                ) in _schema_leaves(media.schema):
                    if not _type_compatible(expected_type, field_type):
                        continue
                    output.append(
                        _SourceCandidate(
                            source=ResponseValueSource(
                                producer_operation_key=operation_key,
                                status_code=status_code,
                                media_type=normalized_media,
                                selector=selector,
                                field_name=field_name,
                            ),
                            field_type=field_type,
                            schema_format=schema_format,
                            description=description,
                        )
                    )
    return output[:100]


def _schema_leaves(
    schema: SchemaIR,
    *,
    selector: str = "$",
    visited: set[int] | None = None,
) -> list[
    tuple[
        str,
        str,
        str | list[str] | None,
        str | None,
        str | None,
    ]
]:
    visited = set() if visited is None else set(visited)
    if id(schema) in visited:
        return []
    visited.add(id(schema))
    if schema.type == "object" or schema.properties:
        output: list[
            tuple[
                str,
                str,
                str | list[str] | None,
                str | None,
                str | None,
            ]
        ] = []
        for name, child in schema.properties.items():
            output.extend(
                _schema_leaves(
                    child,
                    selector=f"{selector}.{name}",
                    visited=visited,
                )
            )
        return output
    if schema.type == "array" and schema.items is not None:
        return _schema_leaves(
            schema.items,
            selector=f"{selector}[]",
            visited=visited,
        )
    field_name = selector.rsplit(".", 1)[-1].removesuffix("[]")
    return [
        (
            selector,
            field_name,
            schema.type,
            schema.format,
            schema.description,
        )
    ]


def _extract_selector_values(body: Any, selector: str) -> list[object]:
    if not selector.startswith("$"):
        return []
    tokens = [token for token in selector[1:].split(".") if token]
    current = [body]
    for token in tokens:
        expand = token.endswith("[]")
        name = token.removesuffix("[]")
        next_values: list[Any] = []
        for value in current:
            child = value.get(name) if isinstance(value, dict) else None
            if expand:
                if isinstance(child, list):
                    next_values.extend(child)
            elif child is not None:
                next_values.append(child)
        current = next_values
    return [
        value
        for value in current
        if isinstance(value, (str, int, float, bool)) and value is not None
    ]


def _flatten_observed_scalars(
    value: Any,
    *,
    selector: str = "$",
) -> list[tuple[str, object]]:
    if isinstance(value, dict):
        output: list[tuple[str, object]] = []
        for name, child in value.items():
            output.extend(
                _flatten_observed_scalars(
                    child,
                    selector=f"{selector}.{name}",
                )
            )
        return output
    if isinstance(value, list):
        output = []
        for child in value:
            output.extend(
                _flatten_observed_scalars(
                    child,
                    selector=f"{selector}[]",
                )
            )
        return output
    if isinstance(value, (str, int, float, bool)) and value is not None:
        return [(selector, value)]
    return []


def _value_name(operation_key: str, input_node_id: str) -> str:
    digest = sha256(f"{operation_key}\0{input_node_id}".encode()).hexdigest()[:24]
    return f"response_{digest}"


def _deduplicate_typed_values(values: list[object]) -> list[object]:
    output: list[object] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        key = (
            type(value).__name__,
            json.dumps(value, ensure_ascii=False, sort_keys=True),
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
    return output


def _normalize_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _declared_success(status_code: str) -> bool:
    normalized = status_code.upper()
    return normalized == "2XX" or (
        len(normalized) == 3
        and normalized.isdigit()
        and 200 <= int(normalized) < 300
    )


def _status_matches(declared: str, actual: int) -> bool:
    normalized = declared.upper()
    return normalized == str(actual) or normalized == f"{actual // 100}XX"


def _type_compatible(
    expected: str | None,
    produced: str | list[str] | None,
) -> bool:
    if expected is None or produced is None:
        return True
    produced_types = {produced} if isinstance(produced, str) else set(produced)
    if expected in produced_types:
        return True
    return expected == "number" and "integer" in produced_types


def _observed_type_compatible(
    expected: str | None,
    value: object,
) -> bool:
    if expected is None:
        return True
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
    return True


def _is_json_media_type(media_type: str | None) -> bool:
    return media_type == "application/json" or bool(
        media_type and media_type.endswith("+json")
    )
