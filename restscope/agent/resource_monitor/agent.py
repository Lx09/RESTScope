"""Synchronous resource classification and identifier extraction Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from restscope.llm import (
    LLMClient,
    LLMMessage,
    LLMModelConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
)
from restscope.observability import TracingRuntime

from .catalog import ResourceCatalog
from .schemas import (
    DetectedResourceGroup,
    LearnedResourceRule,
    MAX_CLASSIFICATION_GROUPS,
    MAX_RESOURCE_ALIAS_COUNT,
    MAX_RESOURCE_NAME_CHARS,
    MAX_RESOURCE_SELECTOR_CHARS,
    MonitoredOperation,
    ResourceClassificationBatch,
    ResourceClassificationDraft,
    ResourceLookupRequest,
    ResourceLookupResult,
    ResourceMonitorResult,
    ResourceMonitorWarning,
    ResourceNameSummary,
    ResourceObservation,
)


MAX_RESPONSE_GROUPS = MAX_CLASSIFICATION_GROUPS
MAX_CANDIDATE_FIELDS = 500
MAX_OBSERVED_SCALARS = 1000
MAX_VALUES_PER_FIELD = 100
MAX_IDENTIFIER_BYTES = 4096
MAX_SCHEMA_EVIDENCE_ITEMS = 1000
MAX_EXISTING_RESOURCES_IN_PROMPT = 100
MAX_PROMPT_CANDIDATES_PER_GROUP = 20
MAX_PROMPT_CANDIDATES_TOTAL = 100
MAX_SCHEMA_FORMAT_CHARS = 200


class ResourceMonitorOutputError(RuntimeError):
    """The FAST model could not return a trustworthy classification."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _EvidenceLimitExceeded(ValueError):
    pass


@dataclass(slots=True)
class _EvidenceBudget:
    groups: int = 0
    fields: int = 0
    values: int = 0
    schema_items: int = 0

    def add_group(self) -> None:
        self.groups += 1
        if self.groups > MAX_RESPONSE_GROUPS:
            raise _EvidenceLimitExceeded(
                f"response groups exceed {MAX_RESPONSE_GROUPS}"
            )

    def add_field(self) -> None:
        self.fields += 1
        if self.fields > MAX_CANDIDATE_FIELDS:
            raise _EvidenceLimitExceeded(
                f"candidate fields exceed {MAX_CANDIDATE_FIELDS}"
            )

    def add_value(self, current_field_values: int) -> None:
        self.values += 1
        if current_field_values >= MAX_VALUES_PER_FIELD:
            raise _EvidenceLimitExceeded(
                f"values for one field exceed {MAX_VALUES_PER_FIELD}"
            )
        if self.values > MAX_OBSERVED_SCALARS:
            raise _EvidenceLimitExceeded(
                f"observed scalar values exceed {MAX_OBSERVED_SCALARS}"
            )

    def add_schema_item(self) -> None:
        self.schema_items += 1
        if self.schema_items > MAX_SCHEMA_EVIDENCE_ITEMS:
            raise _EvidenceLimitExceeded(
                f"schema evidence items exceed {MAX_SCHEMA_EVIDENCE_ITEMS}"
            )


@dataclass(slots=True)
class _FieldCandidate:
    selector: str
    name: str
    types: set[str] = field(default_factory=set)
    values: list[Any] = field(default_factory=list)
    description: str | None = None
    schema_format: str | None = None


@dataclass(slots=True)
class _ResponseGroup:
    group_path: str
    suggested_alias: str
    fields: dict[str, _FieldCandidate]
    exact_id_selector: str | None = None


@dataclass(slots=True)
class _PromptGroup:
    """Ephemeral model-facing IDs mapped back to private response evidence."""

    group_id: str
    group: _ResponseGroup
    candidates: dict[str, _FieldCandidate]
    matched_canonical_name: str | None
    locked_identifier_candidate_id: str | None


@dataclass(slots=True)
class _ResourcePromptContext:
    canonical_names: list[str]
    alias_to_canonical: dict[str, str]


class ResourceMonitorAgent:
    """Learn one extraction rule per operation response group and reuse it."""

    def __init__(
        self,
        *,
        catalog: ResourceCatalog,
        client: LLMClient,
        model: LLMModelConfig,
        validator: OutputValidator | None = None,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        self.catalog = catalog
        self.client = client
        self.model = model
        self.validator = validator or OutputValidator()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def observe(self, observation: ResourceObservation) -> ResourceMonitorResult:
        """Synchronously update resource facts for one resolved 2xx response."""

        attributes = {
            "restscope.operation.key": observation.operation.operation_key,
            "restscope.operation.method": observation.operation.method,
            "restscope.operation.path": observation.operation.path,
            "http.response.status_code": observation.status_code,
        }
        with self.tracing_runtime.span(
            "ResourceMonitorAgent.observe",
            kind="AGENT",
            input_value={
                "operation_key": observation.operation.operation_key,
                "status_code": observation.status_code,
                "body_truncated": observation.body_truncated,
            },
            attributes=attributes,
        ) as span:
            result = self._observe(observation)
            if result.warning is None:
                self.catalog.clear_operation_errors(
                    observation.operation.operation_key
                )
            span.set_output(result)
            span.set_attribute("restscope.resource_monitor.status", result.status)
            return result

    def lookup(self, request: ResourceLookupRequest) -> ResourceLookupResult:
        return self.catalog.lookup(request)

    def _observe(self, observation: ResourceObservation) -> ResourceMonitorResult:
        if observation.body_truncated:
            return self._record_warning(
                operation=observation.operation,
                code="resource_monitor_body_truncated",
                message="Response body was truncated before resource monitoring",
            )

        rules = self.catalog.list_rules(observation.operation.operation_key)
        if rules:
            try:
                return self._apply_rules(observation, rules)
            except _EvidenceLimitExceeded as exc:
                return self._record_warning(
                    operation=observation.operation,
                    code="resource_monitor_evidence_limit_exceeded",
                    message="Response evidence exceeded Resource Monitor limits",
                    issues=[str(exc)],
                )

        if not isinstance(observation.body, (dict, list)):
            return ResourceMonitorResult(status="ignored")

        try:
            groups = _build_groups(observation)
        except _EvidenceLimitExceeded as exc:
            return self._record_warning(
                operation=observation.operation,
                code="resource_monitor_evidence_limit_exceeded",
                message="Response evidence exceeded Resource Monitor limits",
                issues=[str(exc)],
            )
        if not groups:
            return ResourceMonitorResult(status="ignored")
        try:
            detections = self._classify_first_observation(observation, groups)
        except _EvidenceLimitExceeded as exc:
            return self._record_warning(
                operation=observation.operation,
                code="resource_monitor_evidence_limit_exceeded",
                message="Response evidence exceeded Resource Monitor limits",
                issues=[str(exc)],
            )
        except ResourceMonitorOutputError as exc:
            self.catalog.record_operation_error(
                operation=observation.operation,
                warning=ResourceMonitorWarning(
                    code=exc.code,
                    message=str(exc),
                ),
            )
            raise
        if not detections:
            return ResourceMonitorResult(status="ignored")
        self.catalog.record_groups(
            operation=observation.operation,
            groups=detections,
        )
        resource_groups = [item for item in detections if item.has_resource]
        return ResourceMonitorResult(
            status="updated" if resource_groups else "ignored",
            groups_processed=len(resource_groups),
            identifiers_recorded=sum(
                len(item.identifier_values) for item in resource_groups
            ),
        )

    def _apply_rules(
        self,
        observation: ResourceObservation,
        rules: list[LearnedResourceRule],
    ) -> ResourceMonitorResult:
        detections: list[DetectedResourceGroup] = []
        missing: list[LearnedResourceRule] = []
        identifier_count = 0
        for rule in rules:
            if not rule.has_resource:
                continue
            assert rule.resource_name is not None
            assert rule.id_field_name is not None
            assert rule.id_selector is not None
            values = _identifier_values(
                _extract_selector_values(observation.body, rule.id_selector)
            )
            identifier_count += len(values)
            if identifier_count > MAX_OBSERVED_SCALARS:
                raise _EvidenceLimitExceeded(
                    f"identifier values exceed {MAX_OBSERVED_SCALARS}"
                )
            if rule.id_observed and not values:
                missing.append(rule)
                continue
            detections.append(
                DetectedResourceGroup(
                    group_path=rule.group_path,
                    resource_name=rule.resource_name,
                    resource_aliases=rule.resource_aliases,
                    id_field_name=rule.id_field_name,
                    id_selector=rule.id_selector,
                    identifier_values=values,
                    classification_source=rule.classification_source,
                )
            )
        if detections:
            self.catalog.record_groups(
                operation=observation.operation,
                groups=detections,
            )
        for rule in missing:
            assert rule.id_selector is not None
            warning = ResourceMonitorWarning(
                code="expected_resource_id_missing",
                message="A learned resource identifier was missing from a 2xx response",
                issues=[rule.id_selector],
            )
            self.catalog.record_error(
                operation=observation.operation,
                group_path=rule.group_path,
                warning=warning,
            )
        if missing:
            return ResourceMonitorResult(
                status="warning",
                groups_processed=len(detections),
                identifiers_recorded=sum(
                    len(item.identifier_values) for item in detections
                ),
                warning=ResourceMonitorWarning(
                    code="expected_resource_id_missing",
                    message="One or more learned resource identifiers were missing",
                    issues=[
                        rule.id_selector
                        for rule in missing
                        if rule.id_selector is not None
                    ][:20],
                ),
            )
        if not detections:
            return ResourceMonitorResult(status="ignored")
        return ResourceMonitorResult(
            status="updated",
            groups_processed=len(detections),
            identifiers_recorded=sum(
                len(item.identifier_values) for item in detections
            ),
        )

    def _classify_first_observation(
        self,
        observation: ResourceObservation,
        groups: list[_ResponseGroup],
    ) -> list[DetectedResourceGroup]:
        existing_resources = self.catalog.list_resources(
            limit=MAX_EXISTING_RESOURCES_IN_PROMPT + 1,
            aliases_per_resource=MAX_RESOURCE_ALIAS_COUNT + 1,
        )
        resource_context = _resource_prompt_context(existing_resources)
        detections: list[DetectedResourceGroup] = []
        unresolved: list[_ResponseGroup] = []
        for group in groups:
            exact = [
                field
                for field in group.fields.values()
                if _normalize_identifier_name(field.name) == "id"
                and _identifier_values(field.values)
            ]
            if len(exact) == 1:
                group.exact_id_selector = exact[0].selector
                known_canonical_name = resource_context.alias_to_canonical.get(
                    _normalize_resource_name(group.suggested_alias)
                )
                if known_canonical_name is not None:
                    detections.append(
                        DetectedResourceGroup(
                            group_path=group.group_path,
                            resource_name=known_canonical_name,
                            resource_aliases=[group.suggested_alias],
                            id_field_name=exact[0].name,
                            id_selector=exact[0].selector,
                            identifier_values=_identifier_values(exact[0].values),
                            classification_source="exact_id",
                        )
                    )
                elif not resource_context.canonical_names:
                    detections.append(
                        DetectedResourceGroup(
                            group_path=group.group_path,
                            resource_name=group.suggested_alias,
                            resource_aliases=[group.suggested_alias],
                            id_field_name=exact[0].name,
                            id_selector=exact[0].selector,
                            identifier_values=_identifier_values(exact[0].values),
                            classification_source="exact_id",
                        )
                    )
                else:
                    unresolved.append(group)
            else:
                unresolved.append(group)

        if unresolved:
            prompt_groups = self._prompt_groups(
                unresolved,
                alias_to_canonical=resource_context.alias_to_canonical,
            )
            empty_candidate_groups = [
                group for group in prompt_groups if not group.candidates
            ]
            for prompt_group in empty_candidate_groups:
                detections.append(
                    DetectedResourceGroup(
                        group_path=prompt_group.group.group_path,
                        has_resource=False,
                        classification_source="llm",
                    )
                )
            prompt_groups = [
                group for group in prompt_groups if group.candidates
            ]
            if not prompt_groups:
                return detections
            drafts = self._classify_with_model(
                observation=observation,
                groups=prompt_groups,
                known_resource_names=resource_context.canonical_names,
            )
            by_id = {group.group_id: group for group in prompt_groups}
            for draft in drafts:
                prompt_group = by_id[draft.group_id]
                group = prompt_group.group
                if not draft.represents_resource:
                    detections.append(
                        DetectedResourceGroup(
                            group_path=group.group_path,
                            has_resource=False,
                            classification_source="llm",
                        )
                    )
                    continue
                assert draft.canonical_resource_name is not None
                assert draft.identifier_candidate_id is not None
                field = prompt_group.candidates[draft.identifier_candidate_id]
                detections.append(
                    DetectedResourceGroup(
                        group_path=group.group_path,
                        resource_name=draft.canonical_resource_name,
                        resource_aliases=[group.suggested_alias],
                        id_field_name=field.name,
                        id_selector=field.selector,
                        identifier_values=_identifier_values(field.values),
                        classification_source=(
                            "exact_id" if group.exact_id_selector else "llm"
                        ),
                    )
                )
        return detections

    def _prompt_groups(
        self,
        groups: list[_ResponseGroup],
        *,
        alias_to_canonical: dict[str, str],
    ) -> list[_PromptGroup]:
        prompt_groups: list[_PromptGroup] = []
        total_candidates = 0
        for index, group in enumerate(groups, start=1):
            candidates = {
                f"c{candidate_index}": field
                for candidate_index, field in enumerate(
                    _identifier_candidates(group.fields.values()),
                    start=1,
                )
            }
            if len(candidates) > MAX_PROMPT_CANDIDATES_PER_GROUP:
                raise _EvidenceLimitExceeded(
                    "identifier candidates for one group exceed "
                    f"{MAX_PROMPT_CANDIDATES_PER_GROUP}"
                )
            total_candidates += len(candidates)
            if total_candidates > MAX_PROMPT_CANDIDATES_TOTAL:
                raise _EvidenceLimitExceeded(
                    "identifier candidates exceed "
                    f"{MAX_PROMPT_CANDIDATES_TOTAL}"
                )
            exact_candidate_id = next(
                (
                    candidate_id
                    for candidate_id, field in candidates.items()
                    if field.selector == group.exact_id_selector
                ),
                None,
            )
            prompt_groups.append(
                _PromptGroup(
                    group_id=f"g{index}",
                    group=group,
                    candidates=candidates,
                    matched_canonical_name=alias_to_canonical.get(
                        _normalize_resource_name(group.suggested_alias)
                    ),
                    locked_identifier_candidate_id=exact_candidate_id,
                )
            )
        return prompt_groups

    def _classify_with_model(
        self,
        *,
        observation: ResourceObservation,
        groups: list[_PromptGroup],
        known_resource_names: list[str],
    ) -> list[ResourceClassificationDraft]:
        if not self.model.enabled:
            raise ResourceMonitorOutputError(
                "resource_monitor_model_not_configured",
                "The resource_monitor FAST model is not configured",
            )
        messages = [
            LLMMessage(role="system", content=_classification_instructions()),
            LLMMessage(
                role="user",
                content=json.dumps(
                    {
                        "operation": {
                            "method": observation.operation.method,
                            "path": observation.operation.path,
                        },
                        "known_resource_names": known_resource_names,
                        "groups": [_group_prompt(group) for group in groups],
                    },
                    ensure_ascii=False,
                ),
            ),
        ]
        response = self.client.invoke(self._request(messages))
        parsed, errors = self._validate_response(response, groups)
        if not errors:
            assert parsed is not None
            return parsed.groups

        repair_messages = [
            *messages,
            LLMMessage(
                role="assistant",
                content=json.dumps(
                    response.parsed_json
                    if response.parsed_json is not None
                    else response.content,
                    ensure_ascii=False,
                ),
            ),
            LLMMessage(
                role="user",
                content=json.dumps(
                    {
                        "instruction": (
                            "Repair the classification and return the complete batch."
                        ),
                        "validation_errors": errors[:10],
                    },
                    ensure_ascii=False,
                ),
            ),
        ]
        repaired = self.client.invoke(self._request(repair_messages))
        parsed, errors = self._validate_response(repaired, groups)
        if errors or parsed is None:
            raise ResourceMonitorOutputError(
                "resource_monitor_output_invalid",
                f"Resource Monitor output remained invalid: {'; '.join(errors[:5])}",
            )
        return parsed.groups

    def _request(self, messages: list[LLMMessage]) -> LLMRequest:
        return LLMRequest(
            provider=self.model.provider,
            model=self.model.model,
            messages=messages,
            temperature=self.model.temperature,
            max_tokens=self.model.max_tokens,
            response_format="json_schema",
            json_schema=ResourceClassificationBatch.model_json_schema(),
            json_schema_name="ResourceClassificationBatch",
            tool_choice="none",
            timeout_seconds=self.model.timeout_seconds,
            reasoning=self.model.reasoning,
            metadata={"role": "resource_monitor"},
        )

    def _validate_response(
        self,
        response: LLMResponse,
        groups: list[_PromptGroup],
    ) -> tuple[ResourceClassificationBatch | None, list[str]]:
        validation = self.validator.validate(
            response=response,
            output_model=ResourceClassificationBatch,
        )
        if not validation.valid:
            return None, _model_validation_errors(
                validation.errors,
                validation.raw_json,
                groups,
            )
        batch = ResourceClassificationBatch.model_validate(
            validation.validated_object
        )
        expected = {group.group_id for group in groups}
        actual = [draft.group_id for draft in batch.groups]
        errors: list[str] = []
        if set(actual) != expected or len(actual) != len(expected):
            errors.append(f"group ids must exactly match {sorted(expected)}")
        by_id = {group.group_id: group for group in groups}
        for draft in batch.groups:
            group = by_id.get(draft.group_id)
            if group is None:
                continue
            if not draft.represents_resource:
                supplied_fields = {
                    "canonical_resource_name",
                    "identifier_candidate_id",
                } & draft.model_fields_set
                if supplied_fields:
                    errors.append(
                        f"{draft.group_id}: non-resource result must omit "
                        f"{sorted(supplied_fields)}"
                    )
                if group.locked_identifier_candidate_id is not None:
                    errors.append(
                        f"{draft.group_id}: locked identifier candidate cannot "
                        "be declared non-resource"
                    )
                if group.matched_canonical_name is not None:
                    errors.append(
                        f"{draft.group_id}: matched canonical resource cannot "
                        "be declared non-resource"
                    )
                continue
            if not all(
                (
                    draft.canonical_resource_name,
                    draft.identifier_candidate_id,
                )
            ):
                errors.append(
                    f"{draft.group_id}: resource fields are incomplete"
                )
                continue
            if draft.identifier_candidate_id not in group.candidates:
                errors.append(
                    f"{draft.group_id}: unknown candidate id "
                    f"{draft.identifier_candidate_id}"
                )
                continue
            if (
                group.locked_identifier_candidate_id is not None
                and draft.identifier_candidate_id
                != group.locked_identifier_candidate_id
            ):
                errors.append(
                    f"{draft.group_id}: locked identifier candidate must be preserved"
                )
            if (
                group.matched_canonical_name is not None
                and draft.canonical_resource_name != group.matched_canonical_name
            ):
                errors.append(
                    f"{draft.group_id}: matched canonical resource name must "
                    "be preserved"
                )
        return batch, errors

    def _record_warning(
        self,
        *,
        operation: MonitoredOperation,
        code: str,
        message: str,
        issues: list[str] | None = None,
    ) -> ResourceMonitorResult:
        warning = ResourceMonitorWarning(
            code=code,
            message=message,
            issues=issues or [],
        )
        self.catalog.record_operation_error(
            operation=operation,
            warning=warning,
        )
        return ResourceMonitorResult(
            status="warning",
            warning=warning,
        )


def _build_groups(observation: ResourceObservation) -> list[_ResponseGroup]:
    body = observation.body
    groups: list[_ResponseGroup] = []
    budget = _EvidenceBudget()
    if isinstance(body, dict):
        root_fields: dict[str, _FieldCandidate] = {}
        _collect_fields(
            body,
            "$",
            fields=root_fields,
            recursive=False,
            budget=budget,
        )
        if root_fields:
            _append_group(
                groups,
                _ResponseGroup(
                    group_path="$",
                    suggested_alias=_operation_resource_alias(
                        observation.operation.path
                    ),
                    fields=root_fields,
                ),
                budget=budget,
            )
        for name, value in body.items():
            if isinstance(value, dict):
                path = f"$.{name}"
                fields: dict[str, _FieldCandidate] = {}
                _collect_fields(
                    value,
                    path,
                    fields=fields,
                    recursive=True,
                    budget=budget,
                )
                if fields:
                    _append_group(
                        groups,
                        _ResponseGroup(
                            group_path=path,
                            suggested_alias=_singularize(name),
                            fields=fields,
                        ),
                        budget=budget,
                    )
            elif isinstance(value, list):
                path = f"$.{name}[]"
                fields = {}
                _collect_fields(
                    value,
                    f"$.{name}",
                    fields=fields,
                    recursive=True,
                    budget=budget,
                )
                if fields:
                    _append_group(
                        groups,
                        _ResponseGroup(
                            group_path=path,
                            suggested_alias=_singularize(name),
                            fields=fields,
                        ),
                        budget=budget,
                    )
    elif isinstance(body, list):
        fields = {}
        _collect_fields(
            body,
            "$",
            fields=fields,
            recursive=True,
            budget=budget,
        )
        if fields:
            _append_group(
                groups,
                _ResponseGroup(
                    group_path="$[]",
                    suggested_alias=_operation_resource_alias(
                        observation.operation.path
                    ),
                    fields=fields,
                ),
                budget=budget,
            )
    _merge_schema_fields(groups, observation, budget=budget)
    return groups


def _append_group(
    groups: list[_ResponseGroup],
    group: _ResponseGroup,
    *,
    budget: _EvidenceBudget,
) -> None:
    _require_evidence_text(
        group.group_path,
        limit=MAX_RESOURCE_SELECTOR_CHARS,
        label="group path",
    )
    _require_evidence_text(
        group.suggested_alias,
        limit=MAX_RESOURCE_NAME_CHARS,
        label="suggested resource alias",
    )
    budget.add_group()
    groups.append(group)


def _merge_schema_fields(
    groups: list[_ResponseGroup],
    observation: ResourceObservation,
    *,
    budget: _EvidenceBudget,
) -> None:
    for item in observation.response_schema_fields:
        budget.add_schema_item()
        selector = str(item.get("selector") or "")
        name = str(item.get("name") or "")
        if not selector.startswith("$") or not name:
            continue
        _require_evidence_text(
            selector,
            limit=MAX_RESOURCE_SELECTOR_CHARS,
            label="schema selector",
        )
        _require_evidence_text(
            name,
            limit=MAX_RESOURCE_NAME_CHARS,
            label="schema field name",
        )
        _require_selector_safe_field_name(name, label="schema field name")
        path_segments = item.get("path_segments")
        if isinstance(path_segments, list):
            for segment in path_segments:
                if not isinstance(segment, str):
                    raise _EvidenceLimitExceeded(
                        "schema field path segments must be strings"
                    )
                _require_evidence_text(
                    segment,
                    limit=MAX_RESOURCE_NAME_CHARS,
                    label="schema field path segment",
                )
                _require_selector_safe_field_name(
                    segment,
                    label="schema field path segment",
                )
        group = _group_for_schema_selector(groups, selector)
        if group is None:
            group_path, alias = _schema_group_identity(
                selector,
                operation_path=observation.operation.path,
            )
            group = _ResponseGroup(
                group_path=group_path,
                suggested_alias=alias,
                fields={},
            )
            _append_group(groups, group, budget=budget)
        candidate = group.fields.get(selector)
        if candidate is None:
            budget.add_field()
            candidate = _FieldCandidate(selector=selector, name=name)
            group.fields[selector] = candidate
        raw_type = item.get("type")
        if isinstance(raw_type, list):
            candidate.types.update(str(value) for value in raw_type)
        elif raw_type is not None:
            candidate.types.add(str(raw_type))
        description = item.get("description")
        if isinstance(description, str) and description:
            candidate.description = description
        schema_format = item.get("format")
        if isinstance(schema_format, str) and schema_format:
            _require_evidence_text(
                schema_format,
                limit=MAX_SCHEMA_FORMAT_CHARS,
                label="schema field format",
            )
            candidate.schema_format = schema_format


def _group_for_schema_selector(
    groups: list[_ResponseGroup],
    selector: str,
) -> _ResponseGroup | None:
    matches: list[_ResponseGroup] = []
    for group in groups:
        if group.group_path == "$":
            remainder = selector.removeprefix("$.")
            if selector.startswith("$.") and "." not in remainder and "[]" not in remainder:
                matches.append(group)
        elif selector == group.group_path or selector.startswith(
            f"{group.group_path}."
        ):
            matches.append(group)
    return max(matches, key=lambda item: len(item.group_path)) if matches else None


def _schema_group_identity(
    selector: str,
    *,
    operation_path: str,
) -> tuple[str, str]:
    if selector.startswith("$[]"):
        return "$[]", _operation_resource_alias(operation_path)
    remainder = selector.removeprefix("$.")
    if "." not in remainder and "[]" not in remainder:
        return "$", _operation_resource_alias(operation_path)
    first = remainder.split(".", 1)[0]
    group_path = f"$.{first}"
    return group_path, _singularize(first.removesuffix("[]"))


def _collect_fields(
    value: Any,
    selector: str,
    *,
    fields: dict[str, _FieldCandidate],
    recursive: bool,
    budget: _EvidenceBudget,
) -> None:
    if isinstance(value, dict):
        for raw_name, child in value.items():
            name = str(raw_name)
            _require_evidence_text(
                name,
                limit=MAX_RESOURCE_NAME_CHARS,
                label="response field name",
            )
            _require_selector_safe_field_name(name, label="response field name")
            child_selector = f"{selector}.{name}"
            _require_evidence_text(
                child_selector,
                limit=MAX_RESOURCE_SELECTOR_CHARS,
                label="response field selector",
            )
            if isinstance(child, (dict, list)):
                if recursive:
                    _collect_fields(
                        child,
                        child_selector,
                        fields=fields,
                        recursive=True,
                        budget=budget,
                    )
                continue
            candidate = fields.get(child_selector)
            if candidate is None:
                budget.add_field()
                candidate = _FieldCandidate(
                    selector=child_selector,
                    name=name,
                )
                fields[child_selector] = candidate
            budget.add_value(len(candidate.values))
            candidate.types.add(_json_type(child))
            candidate.values.append(child)
        return
    if isinstance(value, list):
        array_selector = f"{selector}[]"
        for child in value:
            if isinstance(child, (dict, list)):
                _collect_fields(
                    child,
                    array_selector,
                    fields=fields,
                    recursive=recursive,
                    budget=budget,
                )


def _extract_selector_values(body: Any, selector: str) -> list[Any]:
    if not selector.startswith("$"):
        return []
    tokens = [
        (match.group(1), bool(match.group(2)))
        for match in re.finditer(r"\.([^.\[\]]+)(\[\])?", selector[1:])
    ]
    root_array = selector.startswith("$[]")
    current = list(body) if root_array and isinstance(body, list) else [body]
    if len(current) > MAX_VALUES_PER_FIELD:
        raise _EvidenceLimitExceeded(
            f"identifier values exceed {MAX_VALUES_PER_FIELD}"
        )
    for name, is_array in tokens:
        next_values: list[Any] = []
        for value in current:
            if not isinstance(value, dict) or name not in value:
                continue
            child = value[name]
            if is_array:
                if isinstance(child, list):
                    if len(next_values) + len(child) > MAX_VALUES_PER_FIELD:
                        raise _EvidenceLimitExceeded(
                            f"identifier values exceed {MAX_VALUES_PER_FIELD}"
                        )
                    next_values.extend(child)
            else:
                if len(next_values) >= MAX_VALUES_PER_FIELD:
                    raise _EvidenceLimitExceeded(
                        f"identifier values exceed {MAX_VALUES_PER_FIELD}"
                    )
                next_values.append(child)
        current = next_values
    return current


def _identifier_values(values: list[Any]) -> list[str | int]:
    output: list[str | int] = []
    seen: set[tuple[type[object], object]] = set()
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            candidate: str | int = value
        elif isinstance(value, str) and value.strip():
            if len(value.encode("utf-8")) > MAX_IDENTIFIER_BYTES:
                raise _EvidenceLimitExceeded(
                    f"identifier string exceeds {MAX_IDENTIFIER_BYTES} bytes"
                )
            candidate = value
        else:
            continue
        key = (type(candidate), candidate)
        if key not in seen:
            seen.add(key)
            output.append(candidate)
    return output


def _existing_resource_prompt(
    resources: list[ResourceNameSummary],
) -> list[str]:
    return _resource_prompt_context(resources).canonical_names


def _resource_prompt_context(
    resources: list[ResourceNameSummary],
) -> _ResourcePromptContext:
    if len(resources) > MAX_EXISTING_RESOURCES_IN_PROMPT:
        raise _EvidenceLimitExceeded(
            "existing resources exceed "
            f"{MAX_EXISTING_RESOURCES_IN_PROMPT}"
        )
    canonical_names: list[str] = []
    alias_to_canonical: dict[str, str] = {}
    for resource in resources:
        _require_evidence_text(
            resource.canonical_name,
            limit=MAX_RESOURCE_NAME_CHARS,
            label="existing canonical resource name",
        )
        if len(resource.aliases) > MAX_RESOURCE_ALIAS_COUNT:
            raise _EvidenceLimitExceeded(
                "existing aliases per resource exceed "
                f"{MAX_RESOURCE_ALIAS_COUNT}"
            )
        canonical_names.append(resource.canonical_name)
        for alias in (resource.canonical_name, *resource.aliases):
            _require_evidence_text(
                alias,
                limit=MAX_RESOURCE_NAME_CHARS,
                label="existing resource alias",
            )
            normalized_alias = _normalize_resource_name(alias)
            previous = alias_to_canonical.get(normalized_alias)
            if previous is not None and previous != resource.canonical_name:
                raise _EvidenceLimitExceeded(
                    "existing resource aliases map to conflicting canonical names"
                )
            alias_to_canonical[normalized_alias] = resource.canonical_name
    return _ResourcePromptContext(
        canonical_names=canonical_names,
        alias_to_canonical=alias_to_canonical,
    )


def _group_prompt(group: _PromptGroup) -> dict[str, Any]:
    result: dict[str, Any] = {
        "group_id": group.group_id,
        "response_location": group.group.group_path,
        "resource_name_hint": group.group.suggested_alias,
        "identifier_candidates": [
            {
                "candidate_id": candidate_id,
                "field_path": _relative_field_path(
                    group.group.group_path,
                    field.selector,
                ),
                "value_types": sorted(field.types & {"string", "integer"}),
                **(
                    {"schema_format": field.schema_format}
                    if field.schema_format is not None
                    else {}
                ),
                **(
                    {"description": field.description[:200]}
                    if field.description
                    else {}
                ),
                "observed_in_response": bool(_identifier_values(field.values)),
            }
            for candidate_id, field in group.candidates.items()
        ],
    }
    if group.matched_canonical_name is not None:
        result["matched_canonical_name"] = group.matched_canonical_name
    if group.locked_identifier_candidate_id is not None:
        result["locked_identifier_candidate_id"] = (
            group.locked_identifier_candidate_id
        )
    return result


def _classification_instructions() -> str:
    return (
        "Return JSON only. A resource is a persistent business entity that a "
        "later API can reference. An identifier uniquely identifies one such "
        "entity and can be reused later. resource_name_hint is only a hint, not "
        "a fact. Reuse a matched_canonical_name exactly; otherwise prefer a "
        "known_resource_names value when semantically equal. "
        "observed_in_response only means this response contained a usable value. "
        "locked_identifier_candidate_id must not change. For every group_id "
        "return exactly one result. Set represents_resource=false with no other "
        "fields for non-resources. For resources, provide a canonical resource "
        "name and exactly one supplied identifier_candidate_id. Do not invent "
        "candidates. Do not include explanations."
    )


def _identifier_candidates(
    fields: Any,
) -> list[_FieldCandidate]:
    output: list[_FieldCandidate] = []
    for field in fields:
        known_types = field.types
        if not known_types or known_types <= {"string", "integer"}:
            output.append(field)
    return output


def _model_validation_errors(
    errors: list[Any],
    raw_json: Any,
    groups: list[_PromptGroup],
) -> list[str]:
    """Translate validation offsets using the actual raw result group IDs."""

    output: list[str] = []
    expected_ids = {group.group_id for group in groups}
    for issue in errors:
        location = issue.location or "output"
        match = re.fullmatch(r"groups\.(\d+)(?:\.(.*))?", location)
        if match is not None:
            group_index = int(match.group(1))
            group_id = _raw_group_id(
                raw_json,
                group_index=group_index,
                expected_ids=expected_ids,
            )
            if group_id is not None:
                suffix = match.group(2)
                location = group_id
                if suffix:
                    location = f"{location}.{suffix}"
            else:
                location = "output"
        elif location.startswith("groups."):
            location = "output"
        output.append(f"{location}: {issue.message}")
    return output


def _raw_group_id(
    raw_json: Any,
    *,
    group_index: int,
    expected_ids: set[str],
) -> str | None:
    if not isinstance(raw_json, dict):
        return None
    raw_groups = raw_json.get("groups")
    if not isinstance(raw_groups, list) or group_index >= len(raw_groups):
        return None
    raw_group = raw_groups[group_index]
    if not isinstance(raw_group, dict):
        return None
    group_id = raw_group.get("group_id")
    if not isinstance(group_id, str) or group_id not in expected_ids:
        return None
    return group_id


def _relative_field_path(group_path: str, selector: str) -> str:
    if group_path == "$":
        return selector.removeprefix("$.")
    prefix = f"{group_path}."
    if selector.startswith(prefix):
        return selector.removeprefix(prefix)
    return selector.removeprefix("$.")


def _operation_resource_alias(path: str) -> str:
    segments = [
        segment
        for segment in path.strip("/").split("/")
        if segment and not (segment.startswith("{") and segment.endswith("}"))
    ]
    alias = _singularize(segments[-1] if segments else "resource")
    _require_evidence_text(
        alias,
        limit=MAX_RESOURCE_NAME_CHARS,
        label="operation resource alias",
    )
    return alias


def _singularize(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").casefold()
    if normalized.endswith("ies") and len(normalized) > 3:
        return f"{normalized[:-3]}y"
    if normalized.endswith("s") and not normalized.endswith("ss") and len(normalized) > 1:
        return normalized[:-1]
    return normalized or "resource"


def _normalize_identifier_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def _normalize_resource_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "", value.casefold())
    if not normalized:
        raise _EvidenceLimitExceeded("resource name has no identifier characters")
    return normalized


def _require_evidence_text(
    value: str,
    *,
    limit: int,
    label: str,
) -> None:
    if len(value) > limit:
        raise _EvidenceLimitExceeded(f"{label} exceeds {limit} characters")


def _require_selector_safe_field_name(value: str, *, label: str) -> None:
    if any(character in value for character in ".[]"):
        raise _EvidenceLimitExceeded(
            f"{label} contains selector-reserved characters"
        )


def _json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__
