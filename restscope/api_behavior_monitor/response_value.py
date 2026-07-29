"""IR-first registration and extraction for reusable response-value pools."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Literal

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
from .prompts import (
    ResponseSourceSelectionDecision,
    ResponseSourceView,
    build_response_source_prompt,
    validate_response_source_decision,
)
from .response_value_catalog import (
    PersistedResponseValueSource,
    ResponseValueCatalog,
    ResponseValueCatalogRegistration,
    ResponseValueSource,
)

_MAX_AVAILABLE_SOURCE_OPTIONS = 10


@dataclass(frozen=True, slots=True)
class ResponseValueRegistrationResult:
    """
    Carry validated response value registration result data across API response
    monitoring and its narrowly approved evidence catalog.

    The annotated fields form the contract; validation rejects missing, extra, or
    incorrectly typed values at the boundary.
    """
    status: Literal["registered", "existing"]
    monitor_id: str
    value_name: str
    sources: list[PersistedResponseValueSource]


@dataclass(frozen=True, slots=True)
class ResponseValueObservationResult:
    """
    Carry validated response value observation result data across API response
    monitoring and its narrowly approved evidence catalog.

    The annotated fields form the contract; validation rejects missing, extra, or
    incorrectly typed values at the boundary.
    """
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
        """
        Handle register as part of API response monitoring and its narrowly approved
        evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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
        parameter_name: str,
        expected_type: str | None,
    ) -> list[ResponseValueSourceOption]:
        """Return relevant IR fields backed by compatible historical values."""

        value_name = _value_name(
            consumer_operation_key,
            consumer_input_node_id,
        )
        backed: list[tuple[_SourceCandidate, ResponseValueSourceOption]] = []
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
            backed.append(
                (
                    candidate,
                    ResponseValueSourceOption(
                        value_name=value_name,
                        source=candidate.source,
                        compatible_scalar_type=expected_type,
                        value_count=len(values),
                    ),
                )
            )
        target_name = _normalize_identifier(parameter_name)
        exact = [
            option
            for candidate, option in backed
            if _normalize_identifier(candidate.source.field_name) == target_name
        ]
        if exact:
            return exact[:_MAX_AVAILABLE_SOURCE_OPTIONS]

        selected_sources = self._semantic_sources(
            parameter_name=parameter_name,
            expected_type=expected_type,
            candidates=[candidate for candidate, _ in backed],
        )
        option_by_source = {
            _source_identity(option.source): option
            for _, option in backed
        }
        return [
            option_by_source[_source_identity(source)]
            for source in selected_sources
            if _source_identity(source) in option_by_source
        ][:_MAX_AVAILABLE_SOURCE_OPTIONS]

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
        """
        Handle refresh sources as part of API response monitoring and its narrowly
        approved evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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
        """
        Handle semantic sources as part of API response monitoring and its narrowly
        approved evidence catalog.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        if (
            not candidates
            or self.client is None
            or self.model is None
            or not self.model.enabled
        ):
            return []
        bounded = candidates[:100]
        prompt = build_response_source_prompt(
            parameter_name=parameter_name,
            expected_type=expected_type,
            sources=[
                ResponseSourceView(
                    alias=f"S{index}",
                    producer_operation_key=(
                        candidate.source.producer_operation_key
                    ),
                    status_code=candidate.source.status_code,
                    media_type=candidate.source.media_type,
                    field_path=_display_field_path(candidate.source.selector),
                    field_name=candidate.source.field_name,
                    field_type=candidate.field_type,
                    schema_format=candidate.schema_format,
                    description=candidate.description,
                    source=candidate.source,
                )
                for index, candidate in enumerate(bounded, start=1)
            ],
        )
        response = self.client.invoke(
            LLMRequest(
                provider=self.model.provider,
                model=self.model.model,
                messages=[
                    LLMMessage(role="system", content=prompt.system),
                    LLMMessage(role="user", content=prompt.user),
                ],
                temperature=self.model.temperature,
                max_tokens=self.model.max_tokens,
                response_format="json",
                tool_choice="none",
                timeout_seconds=self.model.timeout_seconds,
                reasoning=self.model.reasoning,
                metadata={"role": "api_behavior_monitor"},
            )
        )
        validation = self.validator.validate(
            response=response,
            output_model=ResponseSourceSelectionDecision,
        )
        if not validation.valid:
            return []
        selection = ResponseSourceSelectionDecision.model_validate(
            validation.validated_object
        )
        if validate_response_source_decision(selection, prompt):
            return []
        return [
            prompt.source_by_alias[alias]
            for alias in selection.sources
        ]

    def observe(
        self,
        *,
        producer_operation_key: str,
        status_code: int,
        media_type: str | None,
        body: Any,
    ) -> ResponseValueObservationResult:
        """
        Handle observe as part of API response monitoring and its narrowly approved
        evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
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
    """
    Handle source candidates as part of API response monitoring and its narrowly
    approved evidence catalog.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
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
    """
    Handle schema leaves as part of API response monitoring and its narrowly approved
    evidence catalog.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
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
    """
    Handle flatten observed scalars as part of API response monitoring and its narrowly
    approved evidence catalog.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
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


def _source_identity(source: ResponseValueSource) -> tuple[str, str, str, str]:
    return (
        source.producer_operation_key,
        source.status_code,
        source.media_type,
        source.selector,
    )


def _display_field_path(selector: str) -> str:
    if selector == "$":
        return "body"
    if selector.startswith("$."):
        return "body." + selector[2:]
    return "body" + selector.removeprefix("$")


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
