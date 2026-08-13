"""Generate and execute bounded request Batches against the App-bound target.

The service freezes Generator configuration, prepares every request before the
first network call, then executes the requests sequentially. Its output is a
``BatchExecutionResult`` contains inline canonical requests and bounded
HTTP/transport outcomes.  Before the first network call it persists one
immutable Abstract Test Case for the frozen Generator/Constraint state; every
persisted HTTP or transport Observation produced by that Batch points to this
identity and its stable zero-based Case index.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import secrets

from restscope.tools.context import ToolContext
from restscope.api_behavior_monitor.catalog import (
    AbstractTestCaseWrite,
    BatchWrite,
    OperationDefinition,
    APIBehaviorCatalog,
)
from restscope.target_api import (
    PreparedTargetRequest,
    TargetAPIClient,
    TargetAPIError,
    TargetAPITimeout,
    TargetResponseOperationContext,
    prepare_target_request,
)
from restscope.observability import TracingRuntime
from .failure import parse_http_failure, parse_transport_failure
from .outcomes import BatchCaseOutcome

from restscope.request_generation import RequestGenerationConfigStore
from restscope.request_generation.store import RequestGenerationState
from restscope.request_generation.constraints import (
    ConstraintSet,
    ConstraintValidationError,
)
from restscope.request_generation.constraint_solver import ConstraintSolveError
from restscope.request_generation.generation import GenerationError, generate_test_case
from restscope.request_generation.models import (
    GeneratedTestCase,
    PreparedTestRequest,
    ResourceIdentifierGenerator,
    ResponseValueGenerator,
)
from restscope.request_generation.ports import ReferenceValueProvider
from restscope.request_generation.selection import (
    CaseGeneratorSelection,
    GeneratorChoice,
    TestMode,
    choose_case_generators,
)
from restscope.request_generation.serialization import (
    SerializationError,
    serialize_test_case,
)


FAILURE_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_INLINE_REQUEST_CHARACTERS = 2_400


@dataclass(frozen=True, slots=True)
class BatchExecutionResult:
    """Return one frozen generation revision and every inline case outcome."""

    operation_key: str
    generation_revision: int
    generation_state_digest: str
    abstract_test_case_id: str
    batch_id: str
    seed: int
    test_mode: TestMode
    requested_case_count: int
    skipped_case_count: int
    cases: tuple[BatchCaseOutcome, ...]
    batch_persistence_warnings: tuple[str, ...] = ()

    @property
    def success_count(self) -> int:
        """Count HTTP 2xx cases, represented by no normalized Failure."""
        return sum(case.status_code is not None and case.failure is None for case in self.cases)

    @property
    def success_rate(self) -> float:
        """Return the 0-to-1 success fraction for this non-empty batch."""
        return self.success_count / len(self.cases)


class TestingExecutionError(RuntimeError):
    """Stable preflight error raised before any target request is sent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _FrozenReferenceValues:
    """Serve immutable reference values captured with one Generation revision."""

    def __init__(
        self,
        values: dict[tuple[str, ...], tuple[object, ...]],
        records: dict[str, tuple[dict[str, object], ...]],
        resources: dict[tuple[str, ...], str],
        identity_fields: dict[str, tuple[str, ...]],
    ) -> None:
        self._values = values
        self._records = records
        self._resources = resources
        self._identity_fields = identity_fields

    def values_for(
        self,
        strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
    ) -> tuple[object, ...]:
        """Return values captured for this exact Generator identity."""
        return self._values.get(_reference_key(strategy), ())

    def resource_key(self, strategy: ResourceIdentifierGenerator) -> str:
        """Return the resource identity frozen for one exact source."""

        return self._resources[_reference_key(strategy)]

    def resource_records(
        self,
        strategy: ResourceIdentifierGenerator,
    ) -> tuple[dict[str, object], ...]:
        """Return complete current states captured once for the Batch."""

        return self._records.get(self.resource_key(strategy), ())

    def resource_identity_fields(
        self,
        strategy: ResourceIdentifierGenerator,
    ) -> tuple[str, ...]:
        """Return immutable identity fields captured with resource records."""

        return self._identity_fields.get(self.resource_key(strategy), ())


class OperationTestingService:
    """Generate, preflight, and sequentially execute one operation batch.

    Every case is generated and serialized before the first request is sent.
    Configuration or Constraint errors therefore fail without partial network
    effects. Requests then run sequentially so behavior-monitor learning has a
    stable order.
    """

    def __init__(
        self,
        *,
        config_store: RequestGenerationConfigStore,
        api_behavior_catalog: APIBehaviorCatalog,
        target_api_client: TargetAPIClient | None = None,
        tracing_runtime: TracingRuntime | None = None,
        reference_values: ReferenceValueProvider | None = None,
    ) -> None:
        self.config_store = config_store
        self.target_api_client = target_api_client or TargetAPIClient()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()
        self.reference_values = reference_values
        self.api_behavior_catalog = api_behavior_catalog

    def run_batch(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        test_mode: TestMode,
        case_count: int = 1,
        seed: int | None = None,
    ) -> BatchExecutionResult:
        """Execute 1–5 cases from one frozen Generation Store revision."""

        mode = TestMode(test_mode)
        return self._run_batch_traced(
            context,
            operation_key=operation_key,
            test_mode=mode,
            case_count=case_count,
            seed=seed,
        )

    def _run_batch_traced(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        test_mode: TestMode,
        case_count: int,
        seed: int | None,
    ) -> BatchExecutionResult:
        """
        Trace deterministic request generation, constraint solving, and execution.
        """
        with self.tracing_runtime.span(
            "OperationTestingService.run_batch",
            kind="CHAIN",
            input_value={
                "operation_key": operation_key,
                "test_mode": test_mode.value,
                "case_count": case_count,
                "seed": seed,
            },
            attributes={
                "restscope.operation.key": operation_key,
                "restscope.test.case_count": case_count,
            },
        ) as span:
            outcome = self._execute_batch(
                context,
                operation_key=operation_key,
                test_mode=test_mode,
                case_count=case_count,
                seed=seed,
            )
            span.set_output(
                {
                    "generation_revision": outcome.generation_revision,
                    "case_count": len(outcome.cases),
                    "success_count": outcome.success_count,
                }
            )
            # The random seed is needed to understand and reproduce the Batch,
            # but adding it to the established Phoenix output would change that
            # tracing contract. Send it only to the App-owned semantic observer.
            set_live_detail = getattr(span, "set_live_detail", None)
            if callable(set_live_detail):
                set_live_detail("seed", outcome.seed)
            span.set_attribute(
                "restscope.request_generation.revision",
                outcome.generation_revision,
            )
            span.set_attribute(
                "restscope.test.observed_2xx",
                outcome.success_count > 0,
            )
            return outcome

    def _execute_batch(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        test_mode: TestMode,
        case_count: int = 1,
        seed: int | None = None,
    ) -> BatchExecutionResult:
        """Perform fail-before-send preflight, then execute prepared cases."""
        if not 1 <= case_count <= 5:
            raise TestingExecutionError(
                "invalid_case_count",
                "case_count must be between 1 and 5",
            )
        generation_state, frozen_references, statistics = self.config_store._snapshot_with(
            operation_key,
            self._capture_reference_values,
        )
        config = generation_state.config
        operation = config.snapshot
        run_seed = seed if seed is not None else secrets.randbits(63)
        # Build the complete request list before any network call. If one case
        # cannot be solved or serialized, the target sees none of the batch.
        prepared: list[
            tuple[
                GeneratedTestCase,
                PreparedTestRequest,
                PreparedTargetRequest,
                dict[str, object],
                CaseGeneratorSelection,
            ]
        ] = []
        skipped_case_count = 0
        for case_index in range(case_count):
            selection = choose_case_generators(
                config=config,
                constraints=generation_state.constraints,
                test_mode=test_mode,
                statistics=statistics,
                run_seed=run_seed,
                case_index=case_index,
            )
            if selection is None:
                skipped_case_count += 1
                continue
            try:
                generated = self._generate_selected_case(
                    operation=operation,
                    selection=selection,
                    run_seed=run_seed,
                    case_index=case_index,
                    frozen_references=frozen_references,
                )
                request = serialize_test_case(operation, generated)
                target_request = prepare_target_request(
                    method=request.method,
                    base_url=context.base_url,
                    path=request.path,
                    query_items=_target_query_items(request),
                    context_headers=context.headers,
                    request_headers=request.headers,
                    override_context_headers=True,
                    allowed_sensitive_request_headers={"cookie"},
                )
                inline_request = _catalog_request(generated)
                if len(
                    json.dumps(
                        inline_request,
                        allow_nan=False,
                        separators=(",", ":"),
                    )
                ) > MAX_INLINE_REQUEST_CHARACTERS:
                    raise TestingExecutionError(
                        "test_case_request_too_large",
                        "Generated request evidence exceeds 2400 characters",
                    )
                prepared.append(
                    (
                        generated,
                        request,
                        target_request,
                        inline_request,
                        selection,
                    )
                )
            except (
                ConstraintValidationError,
                ConstraintSolveError,
                GenerationError,
                SerializationError,
            ) as exc:
                if test_mode is TestMode.HAPPY_PATH:
                    raise TestingExecutionError(
                        getattr(exc, "code", "test_case_preflight_failed"),
                        str(exc),
                    ) from exc
                # A negative rule can be meaningful but unrepresentable for a
                # particular OpenAPI serialization, such as omitting a path
                # placeholder. Preserve the requested branch by skipping only
                # that slot instead of substituting another test action.
                skipped_case_count += 1
        if not prepared:
            raise TestingExecutionError(
                "test_batch_has_no_executable_cases",
                "No requested Batch slot produced an executable test case",
            )

        # The audit identity is created only after every request has passed
        # generation and serialization preflight, but still before the first
        # target side effect. Production App wiring always supplies the Catalog.
        abstract_test_case_id = self._ensure_abstract_test_case(
            generation_state,
        )
        initial_summary = _batch_summary(
            status="running",
            operation_key=operation_key,
            generation_revision=generation_state.revision,
            generation_state_digest=generation_state.state_digest,
            abstract_test_case_id=abstract_test_case_id,
            seed=run_seed,
            test_mode=test_mode,
            requested_case_count=case_count,
            skipped_case_count=skipped_case_count,
            cases=(),
            persisted_observation_count=0,
            persistence_warnings=(),
        )
        batch = self.api_behavior_catalog.create_batch(
            BatchWrite(summary=initial_summary)
        )

        # Network execution begins only after preflight succeeds for every case.
        cases: list[BatchCaseOutcome] = []
        persistence_warnings: list[str] = []
        try:
            feedback: list[tuple[GeneratorChoice, int]] = []
            for (
                generated,
                request,
                target_request,
                inline_request,
                selection,
            ) in prepared:
                case = self._execute_case(
                    context,
                    case_number=generated.case_index + 1,
                    case_index=generated.case_index,
                    generated=generated,
                    request=request,
                    target_request=target_request,
                    catalog_request=inline_request,
                    operation_path=operation.path,
                    abstract_test_case_id=abstract_test_case_id,
                    batch_id=batch.batch_id,
                    selection=selection,
                )
                cases.append(case)
                feedback.extend(_case_feedback(generated, selection, case))
                self._update_batch_summary(
                    batch_id=batch.batch_id,
                    status="running",
                    operation_key=operation_key,
                    generation_state=generation_state,
                    abstract_test_case_id=abstract_test_case_id,
                    seed=run_seed,
                    requested_case_count=case_count,
                    test_mode=test_mode,
                    skipped_case_count=skipped_case_count,
                    cases=tuple(cases),
                    persistence_warnings=persistence_warnings,
                )
        except Exception as exc:
            persistence_warnings.append(
                f"batch_execution_failed:{type(exc).__name__}"
            )
            self._update_batch_summary(
                batch_id=batch.batch_id,
                status="failed",
                operation_key=operation_key,
                generation_state=generation_state,
                abstract_test_case_id=abstract_test_case_id,
                seed=run_seed,
                requested_case_count=case_count,
                test_mode=test_mode,
                skipped_case_count=skipped_case_count,
                cases=tuple(cases),
                persistence_warnings=persistence_warnings,
            )
            raise

        self._update_batch_summary(
            batch_id=batch.batch_id,
            status="completed",
            operation_key=operation_key,
            generation_state=generation_state,
            abstract_test_case_id=abstract_test_case_id,
            seed=run_seed,
            requested_case_count=case_count,
            test_mode=test_mode,
            skipped_case_count=skipped_case_count,
            cases=tuple(cases),
            persistence_warnings=persistence_warnings,
        )

        self.config_store._record_generator_feedback(
            operation_key=operation_key,
            revision=generation_state.revision,
            feedback=feedback,
        )

        return BatchExecutionResult(
            operation_key=operation_key,
            generation_revision=generation_state.revision,
            generation_state_digest=generation_state.state_digest,
            abstract_test_case_id=abstract_test_case_id,
            batch_id=batch.batch_id,
            seed=run_seed,
            test_mode=test_mode,
            requested_case_count=case_count,
            skipped_case_count=skipped_case_count,
            cases=tuple(cases),
            batch_persistence_warnings=tuple(persistence_warnings),
        )

    def _generate_selected_case(
        self,
        *,
        operation,
        selection: CaseGeneratorSelection,
        run_seed: int,
        case_index: int,
        frozen_references: _FrozenReferenceValues | None,
    ) -> GeneratedTestCase:
        """Generate one selection and retry once with no Constraints.

        An unsatisfiable positive combination or the remaining unrelated
        Constraints of a negative case must not trigger gradual weakening.
        The approved fallback removes all Constraints in one retry.
        """

        expressions = [
            expression
            for item in selection.constraints
            for expression in item.constraint.constraints
        ]
        constraints = ConstraintSet(constraints=expressions) if expressions else None
        try:
            return generate_test_case(
                operation,
                selection.config,
                run_seed=run_seed,
                case_index=case_index,
                reference_values=frozen_references,
                constraints=constraints,
            )
        except (ConstraintValidationError, ConstraintSolveError):
            return generate_test_case(
                operation,
                selection.config,
                run_seed=run_seed,
                case_index=case_index,
                reference_values=frozen_references,
            )

    def _update_batch_summary(
        self,
        *,
        batch_id: str,
        status: str,
        operation_key: str,
        generation_state: RequestGenerationState,
        abstract_test_case_id: str,
        seed: int,
        requested_case_count: int,
        test_mode: TestMode,
        skipped_case_count: int,
        cases: tuple[BatchCaseOutcome, ...],
        persistence_warnings: list[str],
    ) -> None:
        """Best-effort update one Batch without hiding already executed cases."""

        try:
            _rows, persisted_count = self.api_behavior_catalog.list_batch_observations(
                batch_id=batch_id,
                offset=0,
                limit=1,
            )
            if persisted_count < len(cases):
                warning = (
                    "observation_persistence_incomplete:"
                    f"expected={len(cases)},stored={persisted_count}"
                )
                if warning not in persistence_warnings:
                    persistence_warnings.append(warning)
            summary = _batch_summary(
                status=status,
                operation_key=operation_key,
                generation_revision=generation_state.revision,
                generation_state_digest=generation_state.state_digest,
                abstract_test_case_id=abstract_test_case_id,
                seed=seed,
                test_mode=test_mode,
                requested_case_count=requested_case_count,
                skipped_case_count=skipped_case_count,
                cases=cases,
                persisted_observation_count=persisted_count,
                persistence_warnings=tuple(persistence_warnings),
            )
            updated = self.api_behavior_catalog.update_batch_summary(
                batch_id=batch_id,
                summary=summary,
            )
            if updated is None:
                raise RuntimeError("Batch disappeared during execution")
        except Exception as exc:
            warning = f"batch_summary_persistence_failed:{type(exc).__name__}"
            if warning not in persistence_warnings:
                persistence_warnings.append(warning)

    def _capture_reference_values(
        self,
        state: RequestGenerationState,
    ) -> _FrozenReferenceValues | None:
        """Read each configured reference source once under its Store lock."""
        if self.reference_values is None:
            return None
        captured: dict[tuple[str, ...], tuple[object, ...]] = {}
        records: dict[str, tuple[dict[str, object], ...]] = {}
        resources: dict[tuple[str, ...], str] = {}
        identity_fields: dict[str, tuple[str, ...]] = {}
        for item in state.config.positive_generators:
            strategy = item.strategy
            if not isinstance(
                strategy,
                ResourceIdentifierGenerator | ResponseValueGenerator,
            ):
                continue
            key = _reference_key(strategy)
            if key not in captured:
                captured[key] = tuple(self.reference_values.values_for(strategy))
            if isinstance(strategy, ResourceIdentifierGenerator):
                resource = self.reference_values.resource_key(strategy)
                resources[key] = resource
                if resource not in records:
                    records[resource] = tuple(
                        dict(item)
                        for item in self.reference_values.resource_records(strategy)
                    )
                    identity_fields[resource] = tuple(
                        self.reference_values.resource_identity_fields(strategy)
                    )
        return _FrozenReferenceValues(
            captured,
            records,
            resources,
            identity_fields,
        )

    def _ensure_abstract_test_case(self, state: RequestGenerationState) -> str:
        """Persist the exact frozen generation meaning before network execution.

        The Catalog is required: inability to create this audit identity is a
        preflight failure, so the target receives no partial Batch.
        """
        operation = state.config.snapshot
        self.api_behavior_catalog.ensure_operation(
            OperationDefinition(
                operation_id=state.config.operation_key,
                method=operation.method,
                path=operation.path,
            )
        )
        generators_json = {
            "active_media_type": state.config.active_media_type,
            "positive_generators": [
                item.model_dump(mode="json")
                for item in state.config.positive_generators
            ],
            "negative_generators": [
                item.model_dump(mode="json")
                for item in state.config.negative_generators
            ],
            "reference_bindings": [
                item.model_dump(mode="json") for item in state.reference_bindings
            ],
        }
        constraints_json = {
            "constraints": [
                item.model_dump(mode="json") for item in state.constraints
            ]
        }
        record = self.api_behavior_catalog.ensure_abstract_test_case(
            AbstractTestCaseWrite(
                operation_id=state.config.operation_key,
                state_digest=state.state_digest,
                generators_json=generators_json,
                constraints_json=constraints_json,
            )
        )
        return record.abstract_test_case_id

    def _execute_case(
        self,
        context: ToolContext,
        *,
        case_number: int,
        case_index: int,
        generated: GeneratedTestCase,
        request: PreparedTestRequest,
        target_request: PreparedTargetRequest,
        catalog_request: dict[str, object],
        operation_path: str,
        abstract_test_case_id: str,
        batch_id: str,
        selection: CaseGeneratorSelection,
    ) -> BatchCaseOutcome:
        """Execute one prepared request and retain bounded inline facts."""
        with self.tracing_runtime.span(
            "RESTScopeTestCase.execute",
            kind="TOOL",
            input_value={
                "operation_key": generated.operation_key,
                "case_number": case_number,
                "method": request.method,
                "path_template": operation_path,
            },
            attributes={
                "restscope.operation.key": generated.operation_key,
                "restscope.test.case_number": case_number,
                "restscope.test.case_index": case_index,
            },
        ) as span:
            try:
                response = self.target_api_client.send(
                    target_request,
                    timeout_seconds=30,
                    request_kwargs=(
                        {"content": request.content}
                        if request.content is not None
                        else {}
                    ),
                    success_body_limit=None,
                    failure_body_limit=FAILURE_RESPONSE_BYTES,
                    truncate_body=True,
                    response_context=TargetResponseOperationContext(
                        ir=context.ir,
                        operation_key=generated.operation_key,
                        operation_method=request.method,
                        operation_path=operation_path,
                        abstract_test_case_id=abstract_test_case_id,
                        batch_id=batch_id,
                        batch_case_index=case_index,
                        input_validity=(
                            "valid"
                            if selection.action == "happy_path"
                            else "invalid"
                        ),
                        validity_provenance=(
                            "positive_generator"
                            if selection.action == "happy_path"
                            else selection.action
                        ),
                    ),
                )
                media_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    or None
                )
                response_body = _decode_failure_body(
                    response.body,
                    media_type=media_type,
                    encoding=response.encoding,
                ) if 400 <= response.status_code < 600 else None
                failure = parse_http_failure(
                    status_code=response.status_code,
                    reason_phrase=response.reason_phrase,
                    media_type=media_type,
                    response_body=response_body,
                    body_truncated=response.body_truncated,
                )
                bug_found, bug_categories = self._bug_assessment(response)
                result = BatchCaseOutcome(
                    case_number=case_number,
                    test_action=selection.action,
                    negative_rule=(
                        selection.negative_choice.rule
                        if selection.negative_choice is not None
                        else None
                    ),
                    ignored_constraint_count=len(selection.ignored_constraint_ids),
                    bug_found=bug_found,
                    bug_categories=bug_categories,
                    request=catalog_request,
                    status_code=response.status_code,
                    reason_phrase=response.reason_phrase or None,
                    failure=failure,
                )
                span.set_output(
                    {
                        "case_number": case_number,
                        "status_code": response.status_code,
                        "failed": failure is not None,
                        "body_retained": response_body is not None,
                        "body_truncated": response.body_truncated,
                    }
                )
                return result
            except TargetAPITimeout:
                failure = parse_transport_failure(
                    code="request_timeout",
                    message="HTTP request timed out",
                )
                span.mark_error(failure.messages[0])
            except TargetAPIError as exc:
                failure = parse_transport_failure(
                    code=exc.code,
                    message=str(exc),
                )
                span.mark_error(failure.messages[0])

        return BatchCaseOutcome(
            case_number=case_number,
            test_action=selection.action,
            negative_rule=(
                selection.negative_choice.rule
                if selection.negative_choice is not None
                else None
            ),
            ignored_constraint_count=len(selection.ignored_constraint_ids),
            request=catalog_request,
            failure=failure,
        )

    def _bug_assessment(self, response) -> tuple[bool, list[str]]:
        """Read the final replay-confirmed Bug verdict for a Primary response."""

        processor = response.processor_result
        if processor is not None and processor.details is not None:
            bug_found = processor.details.get("bug_found")
            categories = processor.details.get("bug_categories")
            if isinstance(bug_found, bool) and isinstance(categories, list):
                return bug_found, [
                    item for item in categories if isinstance(item, str)
                ][:3]
        observation_id = (
            processor.details.get("observation_id")
            if processor is not None and processor.details is not None
            else None
        )
        if not isinstance(observation_id, str):
            return False, []
        record = self.api_behavior_catalog.get_oracle_assessment(observation_id)
        if record is None:
            return False, []
        categories = [
            check.name
            for check in record.assessment.checks
            if check.status == "reproduced"
        ]
        return record.is_bug, categories


def _reference_key(
    strategy: ResourceIdentifierGenerator | ResponseValueGenerator,
) -> tuple[str, ...]:
    """Return a stable identity for one exact source-backed Generator."""

    source = strategy.source
    return (
        strategy.type,
        source.producer_operation_id,
        str(source.status_code),
        source.media_type,
        source.selector,
        source.field_name,
    )


def _catalog_request(generated: GeneratedTestCase) -> dict[str, object]:
    """Copy one generated case into canonical direct-name request JSON.

    Generator control state and internal node identities stay outside the Test
    Case. Header names are lowercased because HTTP makes them case-insensitive
    and semantic handles use that canonical spelling. The optional Body key
    preserves the difference between omission and an explicitly sent null.
    """
    request: dict[str, object] = {
        "path": deepcopy(generated.path_parameters),
        "query": deepcopy(generated.query_parameters),
        "header": {
            name.lower(): deepcopy(value)
            for name, value in generated.header_parameters.items()
        },
        "cookie": deepcopy(generated.cookie_parameters),
    }
    if generated.body_present:
        request["body"] = deepcopy(generated.body)
    return request


def _case_feedback(
    generated: GeneratedTestCase,
    selection: CaseGeneratorSelection,
    outcome: BatchCaseOutcome,
) -> list[tuple[GeneratorChoice, int]]:
    """Build binary arm rewards from one completed request outcome.

    Happy-path positive arms receive one for a 2xx response. Exceptional
    negative arms receive one only for the final replay-confirmed Bug verdict.
    Positive candidates used to fill the rest of an exceptional request do not
    share the negative reward, keeping the two greedy policies independent.
    """

    if selection.action == "happy_path":
        omitted = set(generated.omitted_input_node_ids)
        reward = int(outcome.status_code is not None and outcome.failure is None)
        return [
            (choice, reward)
            for choice in selection.positive_choices
            if choice.input_node_id not in omitted
        ]
    if selection.negative_choice is not None:
        return [(selection.negative_choice, int(outcome.bug_found))]
    return []


def _decode_failure_body(
    body: bytes | None,
    *,
    media_type: str | None,
    encoding: str | None,
) -> object | None:
    """Decode one retained 4xx/5xx body without losing arbitrary byte content."""
    if body is None:
        return None
    normalized_media = (media_type or "").lower()
    text_encoding = encoding or "utf-8"
    if normalized_media == "application/json" or normalized_media.endswith(
        "+json"
    ):
        try:
            return json.loads(body.decode(text_encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # A malformed or size-truncated JSON response is still valuable
            # evidence. Preserve readable text when possible and raw bytes
            # otherwise instead of silently replacing it with ``None``.
            try:
                return body.decode(text_encoding)
            except UnicodeDecodeError:
                return body
    if normalized_media.startswith("text/"):
        try:
            return body.decode(text_encoding)
        except UnicodeDecodeError:
            return body
    return body


def _batch_summary(
    *,
    status: str,
    operation_key: str,
    generation_revision: int,
    generation_state_digest: str,
    abstract_test_case_id: str,
    seed: int,
    test_mode: TestMode,
    requested_case_count: int,
    skipped_case_count: int,
    cases: tuple[BatchCaseOutcome, ...],
    persisted_observation_count: int,
    persistence_warnings: tuple[str, ...],
) -> dict[str, object]:
    """Build the bounded durable progress record for one Batch.

    The summary contains identifiers and aggregate evidence only. It deliberately
    excludes response bodies, target secrets, exception messages, and stack traces;
    those facts either belong to individual Observations or human-only logs.
    """

    status_counts: dict[str, int] = {}
    transport_failure_count = 0
    for case in cases:
        if case.status_code is None:
            transport_failure_count += 1
            continue
        status_key = str(case.status_code)
        status_counts[status_key] = status_counts.get(status_key, 0) + 1

    success_count = sum(
        case.status_code is not None and case.failure is None for case in cases
    )
    # Warnings are generated by RESTScope itself, but are still bounded so a
    # repeatedly failing persistence adapter cannot grow this JSON indefinitely.
    safe_logs = [message[:300] for message in persistence_warnings[-20:]]
    return {
        "schema_version": 1,
        "status": status,
        "operation_key": operation_key,
        "generation_revision": generation_revision,
        "generation_state_digest": generation_state_digest,
        "abstract_test_case_id": abstract_test_case_id,
        "seed": seed,
        "test_mode": test_mode.value,
        "requested_case_count": requested_case_count,
        "executed_case_count": len(cases),
        "skipped_case_count": skipped_case_count,
        "persisted_observation_count": persisted_observation_count,
        "success_count": success_count,
        "failure_count": len(cases) - success_count,
        "http_status_counts": status_counts,
        "transport_failure_count": transport_failure_count,
        "bug_count": sum(case.bug_found for case in cases),
        "logs": safe_logs,
    }


def _target_query_items(
    request: PreparedTestRequest,
) -> list[tuple[str, str] | tuple[str, str, bool]]:
    allow_reserved = set(request.query_allow_reserved_indices)
    return [
        (name, value, True) if index in allow_reserved else (name, value)
        for index, (name, value) in enumerate(request.query_items)
    ]
