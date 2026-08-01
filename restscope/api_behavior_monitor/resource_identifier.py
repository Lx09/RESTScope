"""Synchronous resource classification and identifier extraction tracker."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from restscope.context import AgentContext, CompactTextWriter, ContextLimits
from restscope.llm import (
    LLMClient,
    LLMModelConfig,
    LLMRequest,
    LLMResponse,
    OutputValidator,
)
from restscope.observability import TracingRuntime

from .resource_catalog import ResourceCatalog
from .prompts import (
    IdentifierCandidateView,
    IdentifierPrompt,
    IdentifierSelectionDecision,
    build_identifier_prompt,
    validate_identifier_decision,
)
from .resource_schemas import (
    DetectedResourceGroup,
    LearnedResourceRule,
    MAX_CLASSIFICATION_GROUPS,
    MAX_RESOURCE_ALIAS_COUNT,
    MAX_RESOURCE_NAME_CHARS,
    MAX_RESOURCE_SELECTOR_CHARS,
    MonitoredOperation,
    ResourceLookupRequest,
    ResourceLookupResult,
    ResourceMonitorResult,
    ResourceMonitorWarning,
    ResourceNameSummary,
    ResourceObservation,
)


MAX_RESPONSE_GROUPS = MAX_CLASSIFICATION_GROUPS
MAX_OBSERVED_SCALARS = 1000
MAX_RESOURCE_ITEMS = 1000
MAX_VALUES_PER_FIELD = MAX_RESOURCE_ITEMS
MAX_IDENTIFIER_BYTES = 4096
MAX_SCHEMA_EVIDENCE_ITEMS = 1000
MAX_EXISTING_RESOURCES_IN_PROMPT = 100
MAX_PROMPT_CANDIDATES_PER_GROUP = 50
MAX_PROMPT_CANDIDATES_TOTAL = 100
MAX_SCHEMA_FORMAT_CHARS = 200
GENERIC_RESOURCE_WRAPPERS = frozenset(
    {"collection", "data", "items", "results"}
)
_ACKNOWLEDGEMENT_FIELD_NAMES = frozenset(
    {"message", "status", "detail", "success"}
)


class ResourceIdentifierOutputError(RuntimeError):
    """The FAST model could not return a trustworthy classification."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _EvidenceLimitExceeded(ValueError):
    pass


@dataclass(slots=True)
class _EvidenceBudget:
    """Count untrusted response/schema items before building an LLM prompt.

    Target APIs control response size and nesting.  These counters make the
    prompt-building traversal fail closed when that untrusted input exceeds a
    deliberate bound, preventing one response from consuming unbounded memory
    or model context.
    """

    groups: int = 0
    schema_items: int = 0

    def add_group(self) -> None:
        """Record one response group and reject evidence beyond the group cap."""
        self.groups += 1
        if self.groups > MAX_RESPONSE_GROUPS:
            raise _EvidenceLimitExceeded(
                f"response groups exceed {MAX_RESPONSE_GROUPS}"
            )

    def add_schema_item(self) -> None:
        """Record one inspected schema item and reject evidence beyond its cap."""
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
    schema_resource_name: str | None = None
    evidence_issues: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _ResourcePromptContext:
    alias_to_canonical: dict[str, str]


@dataclass(slots=True)
class _FirstObservationOutcome:
    detections: list[DetectedResourceGroup] = field(default_factory=list)
    warnings: list[tuple[str, ResourceMonitorWarning]] = field(default_factory=list)


@dataclass(slots=True)
class _SelectorExtraction:
    values: list[str | int] = field(default_factory=list)
    missing_locations: list[str] = field(default_factory=list)
    evidence_issues: list[str] = field(default_factory=list)


class ResourceIdentifierTracker:
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
            "ResourceIdentifierTracker.observe",
            kind="CHAIN",
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
        """
        Look up bounded evidence used by API response monitoring and its narrowly
        approved evidence catalog.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return self.catalog.lookup(request)

    def _observe(self, observation: ResourceObservation) -> ResourceMonitorResult:
        """
        Handle observe as part of API response monitoring and its narrowly approved
        evidence catalog.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        if observation.body_truncated:
            return self._record_warning(
                operation=observation.operation,
                code="resource_monitor_body_truncated",
                message="Response body was truncated before resource monitoring",
            )

        rules = [
            rule
            for rule in self.catalog.list_rules(
                observation.operation
            )
            if rule.has_resource
        ]
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
            outcome = self._classify_first_observation(observation, groups)
        except _EvidenceLimitExceeded as exc:
            return self._record_warning(
                operation=observation.operation,
                code="resource_monitor_evidence_limit_exceeded",
                message="Response evidence exceeded Resource Monitor limits",
                issues=[str(exc)],
            )
        except ResourceIdentifierOutputError as exc:
            self.catalog.record_operation_error(
                operation=observation.operation,
                warning=ResourceMonitorWarning(
                    code=exc.code,
                    message=str(exc),
                ),
            )
            raise
        if not outcome.detections:
            if outcome.warnings:
                return self._persist_observation_warnings(
                    operation=observation.operation,
                    warnings=outcome.warnings,
                )
            return ResourceMonitorResult(status="ignored")
        self.catalog.record_groups(
            operation=observation.operation,
            groups=outcome.detections,
        )
        resource_groups = [
            item for item in outcome.detections if item.has_resource
        ]
        if outcome.warnings:
            return self._persist_observation_warnings(
                operation=observation.operation,
                warnings=outcome.warnings,
                groups_processed=len(resource_groups),
                identifiers_recorded=sum(
                    len(item.identifier_values) for item in resource_groups
                ),
            )
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
        """
        Apply rules for API response monitoring and its narrowly approved evidence
        catalog.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        detections: list[DetectedResourceGroup] = []
        warnings: list[tuple[str, ResourceMonitorWarning]] = []
        for rule in rules:
            if not rule.has_resource:
                continue
            assert rule.resource_name is not None
            assert rule.id_field_name is not None
            assert rule.id_selector is not None
            extraction = _extract_group_identifier_values(
                observation.body,
                group_path=rule.group_path,
                selector=rule.id_selector,
            )
            if extraction.values:
                detections.append(
                    DetectedResourceGroup(
                        group_path=rule.group_path,
                        resource_name=rule.resource_name,
                        resource_aliases=rule.resource_aliases,
                        id_field_name=rule.id_field_name,
                        id_selector=rule.id_selector,
                        identifier_values=extraction.values,
                        classification_source=rule.classification_source,
                    )
                )
            if extraction.missing_locations:
                warnings.append(
                    (
                        rule.group_path,
                        ResourceMonitorWarning(
                            code="expected_resource_id_missing",
                            message=(
                                "One or more resource items omitted a learned "
                                "identifier"
                            ),
                            issues=extraction.missing_locations[:20],
                        ),
                    )
                )
            if extraction.evidence_issues:
                warnings.append(
                    (
                        rule.group_path,
                        ResourceMonitorWarning(
                            code="resource_monitor_evidence_limit_exceeded",
                            message=(
                                "Some resource evidence was skipped or "
                                "truncated"
                            ),
                            issues=extraction.evidence_issues[:20],
                        ),
                    )
                )
        if detections:
            self.catalog.record_groups(
                operation=observation.operation,
                groups=detections,
            )
        if warnings:
            return self._persist_observation_warnings(
                operation=observation.operation,
                warnings=warnings,
                groups_processed=len(detections),
                identifiers_recorded=sum(
                    len(item.identifier_values) for item in detections
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

    def _persist_observation_warnings(
        self,
        *,
        operation: MonitoredOperation,
        warnings: list[tuple[str, ResourceMonitorWarning]],
        groups_processed: int = 0,
        identifiers_recorded: int = 0,
    ) -> ResourceMonitorResult:
        """
        Handle persist observation warnings as part of API response monitoring and its
        narrowly approved evidence catalog.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        for group_path, warning in warnings:
            self.catalog.record_error(
                operation=operation,
                group_path=group_path,
                warning=warning,
            )
        primary = next(
            (
                warning
                for _group_path, warning in warnings
                if warning.code == "expected_resource_id_missing"
            ),
            warnings[0][1],
        )
        return ResourceMonitorResult(
            status="warning",
            groups_processed=groups_processed,
            identifiers_recorded=identifiers_recorded,
            warning=primary,
        )

    def _classify_first_observation(
        self,
        observation: ResourceObservation,
        groups: list[_ResponseGroup],
    ) -> _FirstObservationOutcome:
        """
        Handle classify first observation as part of API response monitoring and its
        narrowly approved evidence catalog.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        existing_resources = self.catalog.list_resources(
            limit=MAX_EXISTING_RESOURCES_IN_PROMPT + 1,
            aliases_per_resource=MAX_RESOURCE_ALIAS_COUNT + 1,
        )
        resource_context = _resource_prompt_context(existing_resources)
        outcome = _FirstObservationOutcome()
        unresolved: list[_ResponseGroup] = []
        for group in groups:
            if group.evidence_issues:
                outcome.warnings.append(
                    (
                        group.group_path,
                        ResourceMonitorWarning(
                            code="resource_monitor_evidence_limit_exceeded",
                            message=(
                                "Some resource evidence was skipped or "
                                "truncated"
                            ),
                            issues=group.evidence_issues[:20],
                        ),
                    )
                )
            exact = [
                field
                for field in _identifier_candidates(group.fields.values())
                if _normalize_identifier_name(field.name) == "id"
            ]
            if exact:
                field = exact[0]
                extraction = _extract_group_identifier_values(
                    observation.body,
                    group_path=group.group_path,
                    selector=field.selector,
                )
                if not extraction.values:
                    if extraction.evidence_issues:
                        outcome.warnings.append(
                            (
                                group.group_path,
                                ResourceMonitorWarning(
                                    code=(
                                        "resource_monitor_evidence_limit_exceeded"
                                    ),
                                    message=(
                                        "Resource identifier evidence exceeded "
                                        "monitor limits"
                                    ),
                                    issues=extraction.evidence_issues[:20],
                                ),
                            )
                        )
                    else:
                        outcome.warnings.append(
                            (
                                group.group_path,
                                ResourceMonitorWarning(
                                    code="expected_resource_id_missing",
                                    message=(
                                        "The IR-declared exact resource "
                                        "identifier was not observed"
                                    ),
                                    issues=[field.selector],
                                ),
                            )
                        )
                    continue
                resource_name = _resolve_resource_name(
                    group,
                    operation=observation.operation,
                    resource_context=resource_context,
                )
                outcome.detections.append(
                    DetectedResourceGroup(
                        group_path=group.group_path,
                        resource_name=resource_name,
                        resource_aliases=[resource_name],
                        id_field_name=field.name,
                        id_selector=field.selector,
                        identifier_values=extraction.values,
                        classification_source="exact_id",
                    )
                )
                if extraction.missing_locations:
                    outcome.warnings.append(
                        (
                            group.group_path,
                            ResourceMonitorWarning(
                                code="expected_resource_id_missing",
                                message=(
                                    "One or more resource items omitted the "
                                    "exact identifier"
                                ),
                                issues=extraction.missing_locations[:20],
                            ),
                        )
                    )
                if extraction.evidence_issues:
                    outcome.warnings.append(
                        (
                            group.group_path,
                            ResourceMonitorWarning(
                                code="resource_monitor_evidence_limit_exceeded",
                                message=(
                                    "Some resource evidence was skipped or "
                                    "truncated"
                                ),
                                issues=extraction.evidence_issues[:20],
                            ),
                        )
                    )
            else:
                unresolved.append(group)

        for group in unresolved:
            resource_name = _resolve_resource_name(
                group,
                operation=observation.operation,
                resource_context=resource_context,
            )
            field = self._select_identifier_candidate(
                observation=observation,
                group=group,
                resource_name=resource_name,
            )
            if field is None:
                continue
            extraction = _extract_group_identifier_values(
                observation.body,
                group_path=group.group_path,
                selector=field.selector,
            )
            if not extraction.values:
                outcome.warnings.append(
                    (
                        group.group_path,
                        ResourceMonitorWarning(
                            code="expected_resource_id_missing",
                            message=(
                                "The selected resource identifier was not "
                                "observed"
                            ),
                            issues=[field.selector],
                        ),
                    )
                )
                continue
            outcome.detections.append(
                DetectedResourceGroup(
                    group_path=group.group_path,
                    resource_name=resource_name,
                    resource_aliases=[resource_name],
                    id_field_name=field.name,
                    id_selector=field.selector,
                    identifier_values=extraction.values,
                    classification_source="llm",
                )
            )
            if extraction.missing_locations:
                outcome.warnings.append(
                    (
                        group.group_path,
                        ResourceMonitorWarning(
                            code="expected_resource_id_missing",
                            message=(
                                "One or more resource items omitted the "
                                "selected identifier"
                            ),
                            issues=extraction.missing_locations[:20],
                        ),
                    )
                )
            if extraction.evidence_issues:
                outcome.warnings.append(
                    (
                        group.group_path,
                        ResourceMonitorWarning(
                            code="resource_monitor_evidence_limit_exceeded",
                            message=(
                                "Some resource evidence was skipped or "
                                "truncated"
                            ),
                            issues=extraction.evidence_issues[:20],
                        ),
                    )
                )
        return outcome

    def _select_identifier_candidate(
        self,
        *,
        observation: ResourceObservation,
        group: _ResponseGroup,
        resource_name: str,
    ) -> _FieldCandidate | None:
        """
        Select identifier candidate for API response monitoring and its narrowly
        approved evidence catalog.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        candidates = _identifier_candidates(group.fields.values())
        # Generic acknowledgement envelopes describe request processing, not
        # reusable resource identity. Skipping them avoids a model call and
        # deliberately does not persist a negative extraction rule.
        if (
            candidates
            and any(field.values for field in candidates)
            and all(
                _normalize_identifier_name(field.name)
                in _ACKNOWLEDGEMENT_FIELD_NAMES
                for field in candidates
            )
        ):
            return None
        suffix_candidates = [
            field
            for field in candidates
            if _normalize_identifier_name(field.name).endswith("id")
        ]
        selected_pool = (suffix_candidates or candidates)[
            :MAX_PROMPT_CANDIDATES_TOTAL
        ]
        if not selected_pool:
            return None
        numbered = [
            (f"I{index}", field)
            for index, field in enumerate(selected_pool, start=1)
        ]
        batches = [
            numbered[index : index + MAX_PROMPT_CANDIDATES_PER_GROUP]
            for index in range(0, len(numbered), MAX_PROMPT_CANDIDATES_PER_GROUP)
        ]
        first_prompt = _selection_prompt(
            observation=observation,
            group=group,
            resource_name=resource_name,
            candidates=batches[0],
        )
        first_context = _prompt_context(first_prompt)
        selection, errors, response = self._invoke_selection(
            first_context,
            first_prompt,
        )
        if errors:
            first_context.append_assistant(response)
            feedback = CompactTextWriter(max_value_chars=500)
            feedback.section("Correction Required")
            feedback.text("result", "The previous JSON could not be used.")
            feedback.section("Problems", untrusted=True)
            for error in errors[:10]:
                feedback.text("problem", error)
            feedback.section("Required Fix")
            feedback.text(
                "instruction",
                "Return one complete corrected JSON object.",
            )
            first_context.append_feedback(
                feedback.render(max_chars=3_000).text
            )
            repaired, repair_errors, _response = self._invoke_selection(
                first_context,
                first_prompt,
            )
            if repair_errors or repaired is None:
                raise ResourceIdentifierOutputError(
                    "resource_monitor_output_invalid",
                    "Resource Monitor output remained invalid: "
                    f"{'; '.join(repair_errors[:5])}",
                )
            return _selected_field(repaired, batches[0])
        assert selection is not None
        selected = _selected_field(selection, batches[0])
        if selected is not None or len(batches) == 1:
            return selected

        second_prompt = _selection_prompt(
            observation=observation,
            group=group,
            resource_name=resource_name,
            candidates=batches[1],
        )
        second_context = _prompt_context(second_prompt)
        second, second_errors, _response = self._invoke_selection(
            second_context,
            second_prompt,
        )
        if second_errors or second is None:
            raise ResourceIdentifierOutputError(
                "resource_monitor_output_invalid",
                "Resource Monitor second selection was invalid: "
                f"{'; '.join(second_errors[:5])}",
            )
        return _selected_field(second, batches[1])

    def _invoke_selection(
        self,
        context: AgentContext,
        prompt: IdentifierPrompt,
    ) -> tuple[
        IdentifierSelectionDecision | None,
        list[str],
        LLMResponse,
    ]:
        """
        Handle invoke selection as part of API response monitoring and its narrowly
        approved evidence catalog.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        if not self.model.enabled:
            raise ResourceIdentifierOutputError(
                "resource_monitor_model_not_configured",
                "The resource_monitor FAST model is not configured",
            )
        llm_request = self._selection_request(context)
        with self.tracing_runtime.span(
            "ResourceIdentifierTracker.select_identifier",
            kind="CHAIN",
            input_value={"candidate_count": len(prompt.candidate_aliases)},
        ) as span:
            for name, value in context.metrics.trace_attributes().items():
                span.set_attribute(name, value)
            response = self.client.invoke(llm_request)
            span.set_output(
                {
                    "has_identifier": bool(
                        isinstance(response.parsed_json, dict)
                        and response.parsed_json.get("identifier")
                    ),
                    "tool_call_count": len(response.tool_calls),
                }
            )
        validation = self.validator.validate(
            response=response,
            output_model=IdentifierSelectionDecision,
        )
        if not validation.valid:
            return (
                None,
                ["Return one JSON object with only the identifier field."],
                response,
            )
        selection = IdentifierSelectionDecision.model_validate(
            validation.validated_object
        )
        return selection, validate_identifier_decision(selection, prompt), response

    def _selection_request(
        self,
        context: AgentContext,
    ) -> LLMRequest:
        return LLMRequest(
            provider=self.model.provider,
            model=self.model.model,
            messages=context.messages_for_request(self.model),
            temperature=self.model.temperature,
            max_tokens=self.model.max_tokens,
            response_format="json",
            tool_choice="none",
            timeout_seconds=self.model.timeout_seconds,
            reasoning=self.model.reasoning,
            metadata={"role": "api_behavior_monitor"},
        )

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
    """
    Build groups for API response monitoring and its narrowly approved evidence catalog.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    body = observation.body
    groups: list[_ResponseGroup] = []
    budget = _EvidenceBudget()
    if isinstance(body, dict):
        collections = [
            (str(name), value)
            for name, value in body.items()
            if isinstance(value, list)
        ]
        if collections:
            for name, items in collections:
                _require_evidence_text(
                    name,
                    limit=MAX_RESOURCE_NAME_CHARS,
                    label="response collection field name",
                )
                _require_selector_safe_field_name(
                    name,
                    label="response collection field name",
                )
                normalized_name = _normalize_resource_name(name)
                alias = (
                    _operation_resource_alias(observation.operation.path)
                    if normalized_name in GENERIC_RESOURCE_WRAPPERS
                    else _singularize(name)
                )
                _append_group(
                    groups,
                    _build_item_group(
                        items,
                        group_path=f"$.{name}[]",
                        suggested_alias=alias,
                    ),
                    budget=budget,
                )
        else:
            _append_group(
                groups,
                _build_item_group(
                    [body],
                    group_path="$",
                    suggested_alias=_operation_resource_alias(
                        observation.operation.path
                    ),
                ),
                budget=budget,
            )
    elif isinstance(body, list):
        _append_group(
            groups,
            _build_item_group(
                body,
                group_path="$[]",
                suggested_alias=_operation_resource_alias(
                    observation.operation.path
                ),
            ),
            budget=budget,
        )
    _merge_schema_fields(groups, observation, budget=budget)
    return groups


def _build_item_group(
    items: list[Any],
    *,
    group_path: str,
    suggested_alias: str,
) -> _ResponseGroup:
    """
    Build item group for API response monitoring and its narrowly approved evidence
    catalog.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    fields: dict[str, _FieldCandidate] = {}
    issues: list[str] = []
    bounded_items = items[:MAX_RESOURCE_ITEMS]
    if len(items) > MAX_RESOURCE_ITEMS:
        issues.append(
            f"{group_path}: collection truncated after "
            f"{MAX_RESOURCE_ITEMS} items"
        )
    for index, item in enumerate(bounded_items):
        if not isinstance(item, dict):
            continue
        location = _group_item_location(group_path, index)
        if _json_scalar_count_exceeds(item, MAX_OBSERVED_SCALARS):
            if len(issues) < 20:
                issues.append(
                    f"{location}: resource item exceeds "
                    f"{MAX_OBSERVED_SCALARS} scalar values"
                )
            continue
        _collect_item_fields(
            item,
            group_path=group_path,
            fields=fields,
            issues=issues,
        )
    return _ResponseGroup(
        group_path=group_path,
        suggested_alias=suggested_alias,
        fields=fields,
        evidence_issues=issues,
    )


def _collect_item_fields(
    item: dict[Any, Any],
    *,
    group_path: str,
    fields: dict[str, _FieldCandidate],
    issues: list[str],
) -> None:
    """
    Handle collect item fields as part of API response monitoring and its narrowly
    approved evidence catalog.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    for raw_name, value in item.items():
        name = str(raw_name)
        _require_evidence_text(
            name,
            limit=MAX_RESOURCE_NAME_CHARS,
            label="response field name",
        )
        _require_selector_safe_field_name(name, label="response field name")
        if isinstance(value, (dict, list)):
            continue
        selector = f"{group_path}.{name}"
        _require_evidence_text(
            selector,
            limit=MAX_RESOURCE_SELECTOR_CHARS,
            label="response field selector",
        )
        candidate = fields.get(selector)
        if candidate is None:
            if len(fields) >= MAX_OBSERVED_SCALARS:
                if len(issues) < 20:
                    issues.append(
                        f"{group_path}: candidate fields exceed "
                        f"{MAX_OBSERVED_SCALARS}"
                    )
                continue
            candidate = _FieldCandidate(selector=selector, name=name)
            fields[selector] = candidate
        if len(candidate.values) < MAX_RESOURCE_ITEMS:
            candidate.values.append(value)
        candidate.types.add(_json_type(value))


def _json_scalar_count_exceeds(value: Any, limit: int) -> bool:
    count = 0
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
        else:
            count += 1
            if count > limit:
                return True
    return False


def _group_item_location(group_path: str, index: int) -> str:
    if group_path == "$":
        return "$"
    if group_path == "$[]":
        return f"$[{index}]"
    return f"{group_path.removesuffix('[]')}[{index}]"


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
    """
    Merge schema fields for API response monitoring and its narrowly approved evidence
    catalog.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
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
            continue
        candidate = group.fields.get(selector)
        if candidate is None:
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
        resource_name = item.get("resource_name")
        if isinstance(resource_name, str) and resource_name.strip():
            _require_evidence_text(
                resource_name,
                limit=MAX_RESOURCE_NAME_CHARS,
                label="schema resource name",
            )
            group.schema_resource_name = _singularize(resource_name)


def _group_for_schema_selector(
    groups: list[_ResponseGroup],
    selector: str,
) -> _ResponseGroup | None:
    return next(
        (
            group
            for group in groups
            if _selector_is_immediate(group.group_path, selector)
        ),
        None,
    )


def _selector_is_immediate(group_path: str, selector: str) -> bool:
    prefix = f"{group_path}."
    if not selector.startswith(prefix):
        return False
    remainder = selector.removeprefix(prefix)
    return bool(remainder) and "." not in remainder and "[]" not in remainder


def _resolve_resource_name(
    group: _ResponseGroup,
    *,
    operation: MonitoredOperation,
    resource_context: _ResourcePromptContext,
) -> str:
    """
    Resolve resource name for API response monitoring and its narrowly approved evidence
    catalog.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    operation_alias = _operation_resource_alias(operation.path)
    aliases = [
        value
        for value in (
            group.schema_resource_name,
            group.suggested_alias,
            operation_alias,
        )
        if value
        and _normalize_resource_name(value) not in GENERIC_RESOURCE_WRAPPERS
    ]
    for alias in aliases:
        known = resource_context.alias_to_canonical.get(
            _normalize_resource_name(alias)
        )
        if known is not None:
            return known
    if group.schema_resource_name is not None:
        return group.schema_resource_name
    if (
        group.suggested_alias
        and _normalize_resource_name(group.suggested_alias)
        not in GENERIC_RESOURCE_WRAPPERS
    ):
        return group.suggested_alias
    return operation_alias


def _extract_group_identifier_values(
    body: Any,
    *,
    group_path: str,
    selector: str,
) -> _SelectorExtraction:
    """
    Handle extract group identifier values as part of API response monitoring and its
    narrowly approved evidence catalog.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    prefix = f"{group_path}."
    if not selector.startswith(prefix):
        return _SelectorExtraction(
            values=_identifier_values(_extract_selector_values(body, selector))
        )
    field_name = selector.removeprefix(prefix)
    if not field_name or "." in field_name or "[]" in field_name:
        return _SelectorExtraction(
            values=_identifier_values(_extract_selector_values(body, selector))
        )
    items = _items_for_group(body, group_path)
    extraction = _SelectorExtraction()
    if len(items) > MAX_RESOURCE_ITEMS:
        extraction.evidence_issues.append(
            f"{group_path}: collection truncated after "
            f"{MAX_RESOURCE_ITEMS} items"
        )
    seen: set[tuple[type[object], object]] = set()
    for index, item in enumerate(items[:MAX_RESOURCE_ITEMS]):
        if not isinstance(item, dict):
            continue
        location = _group_item_location(group_path, index)
        if _json_scalar_count_exceeds(item, MAX_OBSERVED_SCALARS):
            if len(extraction.evidence_issues) < 20:
                extraction.evidence_issues.append(
                    f"{location}: resource item exceeds "
                    f"{MAX_OBSERVED_SCALARS} scalar values"
                )
            continue
        raw_value = item.get(field_name)
        try:
            values = _identifier_values([raw_value])
        except _EvidenceLimitExceeded as exc:
            if len(extraction.evidence_issues) < 20:
                extraction.evidence_issues.append(
                    f"{location}: {exc}"
                )
            continue
        if not values:
            if len(extraction.missing_locations) < 20:
                extraction.missing_locations.append(location)
            continue
        value = values[0]
        key = (type(value), value)
        if key not in seen:
            seen.add(key)
            extraction.values.append(value)
    return extraction


def _items_for_group(body: Any, group_path: str) -> list[Any]:
    if group_path == "$":
        return [body] if isinstance(body, dict) else []
    if group_path == "$[]":
        return list(body) if isinstance(body, list) else []
    match = re.fullmatch(r"\$\.([^.\[\]]+)\[\]", group_path)
    if match is None or not isinstance(body, dict):
        return []
    value = body.get(match.group(1))
    return list(value) if isinstance(value, list) else []


def _extract_selector_values(body: Any, selector: str) -> list[Any]:
    """
    Handle extract selector values as part of API response monitoring and its narrowly
    approved evidence catalog.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
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


def _resource_prompt_context(
    resources: list[ResourceNameSummary],
) -> _ResourcePromptContext:
    """
    Handle resource prompt context as part of API response monitoring and its narrowly
    approved evidence catalog.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    if len(resources) > MAX_EXISTING_RESOURCES_IN_PROMPT:
        raise _EvidenceLimitExceeded(
            "existing resources exceed "
            f"{MAX_EXISTING_RESOURCES_IN_PROMPT}"
        )
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
    return _ResourcePromptContext(alias_to_canonical=alias_to_canonical)


def _selection_prompt(
    *,
    observation: ResourceObservation,
    group: _ResponseGroup,
    resource_name: str,
    candidates: list[tuple[str, _FieldCandidate]],
) -> IdentifierPrompt:
    """
    Handle selection prompt as part of API response monitoring and its narrowly approved
    evidence catalog.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    return build_identifier_prompt(
        method=observation.operation.method,
        path=observation.operation.path,
        resource_name=resource_name,
        response_location=group.group_path,
        candidates=[
            IdentifierCandidateView(
                alias=alias,
                field_path=_relative_field_path(group.group_path, field.selector),
                value_types=tuple(
                    sorted(field.types & {"string", "integer"})
                ),
                observed=bool(_identifier_values(field.values)),
                schema_format=field.schema_format,
                description=field.description,
            )
            for alias, field in candidates
        ],
    )


def _prompt_context(prompt: IdentifierPrompt) -> AgentContext:
    """Create the bounded one- or two-output identifier-selection session."""
    return AgentContext(
        system=prompt.system,
        user=prompt.user,
        limits=ContextLimits(
            system_chars=1_600,
            initial_user_chars=8_000,
            feedback_chars=3_000,
            conversation_chars=12_000,
            required_output_tokens=512,
        ),
        metrics=prompt.metrics,
    )


def _selected_field(
    selection: IdentifierSelectionDecision,
    candidates: list[tuple[str, _FieldCandidate]],
) -> _FieldCandidate | None:
    if selection.identifier is None:
        return None
    return dict(candidates)[selection.identifier]


def _identifier_candidates(
    fields: Any,
) -> list[_FieldCandidate]:
    output: list[_FieldCandidate] = []
    for field in fields:
        known_types = field.types
        if not known_types or known_types <= {"string", "integer"}:
            output.append(field)
    return output


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
