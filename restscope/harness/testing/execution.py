"""Generate and execute bounded Test Case batches against the App-bound target.

The service freezes Generator configuration, prepares every request before the
first network call, then executes the requests sequentially. Its output is a
``BatchExecutionResult``: structured direct-name request JSON and the outcome
of each case, ready to enter Operation Smoke's run-local Test Case Catalog. It
does not build a second reporting representation.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import json
import secrets
from typing import Any
from uuid import uuid4

from restscope.tools.context import ToolContext
from restscope.http_transport import (
    PreparedTargetRequest,
    TargetHTTPTimeout,
    TargetHTTPTransport,
    TargetHTTPTransportError,
    TargetResponseOperationContext,
)
from restscope.observability import TracingRuntime
from restscope.harness.testing.test_case_catalog import (
    CatalogTestCase,
    parse_http_failure,
    parse_transport_failure,
)

from .catalog import GeneratorConfigCatalog
from .constraints import ConstraintSet, ConstraintValidationError
from .constraint_solver import ConstraintSolveError
from .generation import generate_test_case
from .models import (
    GeneratedTestCase,
    PreparedTestRequest,
)
from .ports import ReferenceValueProvider
from .serialization import serialize_test_case


BEHAVIOR_MONITOR_RESPONSE_BYTES = 1024 * 1024
SMOKE_FAILURE_RESPONSE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class BatchExecutionResult:
    """Return one prepared-and-executed batch without constructing a report.

    ``cases`` contains every attempted request, including successes and
    transport failures. A Coordinator can record those immutable cases in its
    run-local Catalog without translating a second evidence structure.
    """

    run_id: str
    operation_key: str
    seed: int
    cases: tuple[CatalogTestCase, ...]

    @property
    def success_count(self) -> int:
        """Count HTTP 2xx cases, represented by the absence of a Failure."""
        return sum(case.failure is None for case in self.cases)

    @property
    def success_rate(self) -> float:
        """Return the 0-to-1 success fraction for this non-empty batch."""
        return self.success_count / len(self.cases)


class TestingExecutionError(RuntimeError):
    """Stable preflight error raised before any target request is sent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


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
        config_catalog: GeneratorConfigCatalog,
        transport: TargetHTTPTransport | None = None,
        tracing_runtime: TracingRuntime | None = None,
        reference_values: ReferenceValueProvider | None = None,
    ) -> None:
        self.config_catalog = config_catalog
        self.transport = transport or TargetHTTPTransport()
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()
        self.reference_values = reference_values

    def run_smoke_batch(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        case_count: int = 1,
        seed: int | None = None,
        constraints: ConstraintSet | None = None,
        case_id_factory: Callable[[], str] | None = None,
    ) -> BatchExecutionResult:
        """Execute one complete Smoke batch and return Catalog-ready cases.

        ``operation_key`` selects the frozen operation snapshot. ``case_count`` and
        ``seed`` control deterministic generation, while optional runtime
        ``constraints`` express relationships learned during the current App
        lifetime. ``case_id_factory`` lets a run-level Catalog assign identities
        continuously across Batch rounds and Solve probes. Generation or
        serialization failures abort before any request is sent; transport
        failures are recorded per case.
        """

        return self._run_smoke_batch_traced(
            context,
            operation_key=operation_key,
            case_count=case_count,
            seed=seed,
            constraints=constraints,
            case_id_factory=case_id_factory,
        )

    def _run_smoke_batch_traced(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        case_count: int,
        seed: int | None,
        constraints: ConstraintSet | None,
        case_id_factory: Callable[[], str] | None,
    ) -> BatchExecutionResult:
        """
        Trace deterministic request generation, constraint solving, and execution.

        This private helper keeps one transformation or policy decision explicit so the
        surrounding orchestration remains readable.
        """
        with self.tracing_runtime.span(
            "OperationTestingService.run_smoke_batch",
            kind="CHAIN",
            input_value={
                "operation_key": operation_key,
                "case_count": case_count,
                "seed": seed,
                "constraint_count": (
                    len(constraints.constraints)
                    if constraints is not None
                    else 0
                ),
            },
            attributes={
                "restscope.operation.key": operation_key,
                "restscope.test.case_count": case_count,
                "restscope.test.constraint_count": (
                    len(constraints.constraints)
                    if constraints is not None
                    else 0
                ),
            },
        ) as span:
            outcome = self._execute_smoke_batch(
                context,
                operation_key=operation_key,
                case_count=case_count,
                seed=seed,
                constraints=constraints,
                case_id_factory=case_id_factory,
            )
            span.set_output(
                {
                    "run_id": outcome.run_id,
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
            span.set_attribute("restscope.test.run_id", outcome.run_id)
            span.set_attribute(
                "restscope.test.observed_2xx",
                outcome.success_count > 0,
            )
            return outcome

    def _execute_smoke_batch(
        self,
        context: ToolContext,
        /,
        *,
        operation_key: str,
        case_count: int = 1,
        seed: int | None = None,
        constraints: ConstraintSet | None = None,
        case_id_factory: Callable[[], str] | None = None,
    ) -> BatchExecutionResult:
        """Perform fail-before-send preflight, then execute prepared cases."""
        if not 1 <= case_count <= 20:
            raise TestingExecutionError(
                "invalid_case_count",
                "case_count must be between 1 and 20",
            )
        config = self.config_catalog.require_operation(operation_key)
        operation = config.snapshot
        run_seed = seed if seed is not None else secrets.randbits(63)
        # Build the complete request list before any network call. If one case
        # cannot be solved or serialized, the target sees none of the batch.
        prepared: list[
            tuple[GeneratedTestCase, PreparedTestRequest, PreparedTargetRequest]
        ] = []
        try:
            for case_index in range(case_count):
                generated = generate_test_case(
                    operation,
                    config,
                    run_seed=run_seed,
                    case_index=case_index,
                    reference_values=self.reference_values,
                    constraints=constraints,
                )
                request = serialize_test_case(operation, generated)
                target_request = self.transport.prepare(
                    method=request.method,
                    base_url=context.base_url,
                    path=request.path,
                    query_items=_target_query_items(request),
                    context_headers=context.headers,
                    request_headers=request.headers,
                    override_context_headers=True,
                    allowed_sensitive_request_headers={"cookie"},
                )
                prepared.append((generated, request, target_request))
        except (ConstraintValidationError, ConstraintSolveError) as exc:
            raise TestingExecutionError(exc.code, str(exc)) from exc

        # Network execution begins only after preflight succeeds for every case.
        run_id = f"test_run_{uuid4().hex}"
        if case_id_factory is None:
            next_case_number = 1

            def case_id_factory() -> str:
                """Assign simple identities when no shared Catalog owns the run."""
                nonlocal next_case_number
                case_id = f"TC{next_case_number}"
                next_case_number += 1
                return case_id

        cases: list[CatalogTestCase] = []
        for case_index, (generated, request, target_request) in enumerate(
            prepared
        ):
            cases.append(
                self._execute_case(
                    context,
                    run_id=run_id,
                    case_id=case_id_factory(),
                    case_index=case_index,
                    generated=generated,
                    request=request,
                    target_request=target_request,
                    catalog_request=_catalog_request(generated),
                )
            )

        return BatchExecutionResult(
            run_id=run_id,
            operation_key=operation_key,
            seed=run_seed,
            cases=tuple(cases),
        )

    def _execute_case(
        self,
        context: ToolContext,
        *,
        run_id: str,
        case_id: str,
        case_index: int,
        generated: GeneratedTestCase,
        request: PreparedTestRequest,
        target_request: PreparedTargetRequest,
        catalog_request: dict[str, Any],
    ) -> CatalogTestCase:
        """Execute one prepared request and retain only Catalog-approved facts."""
        with self.tracing_runtime.span(
            "RESTScopeTestCase.execute",
            kind="TOOL",
            input_value={
                "operation_key": generated.operation_key,
                "run_id": run_id,
                "case_id": case_id,
                "method": request.method,
                "path_template": self.config_catalog.require_operation(
                    generated.operation_key
                ).snapshot.path,
            },
            attributes={
                "restscope.operation.key": generated.operation_key,
                "restscope.test.run_id": run_id,
                "restscope.test.case_id": case_id,
                "restscope.test.case_index": case_index,
            },
        ) as span:
            try:
                response = self.transport.request_prepared(
                    target_request,
                    timeout_seconds=30,
                    request_kwargs=(
                        {"content": request.content}
                        if request.content is not None
                        else {}
                    ),
                    response_body_limit=(
                        BEHAVIOR_MONITOR_RESPONSE_BYTES
                        if self.transport.has_response_processor
                        or getattr(self.transport, "run_observer", None) is not None
                        else None
                    ),
                    failure_response_body_limit=SMOKE_FAILURE_RESPONSE_BYTES,
                    truncate_response_body=True,
                    buffer_success_body_only=True,
                    processor_context=TargetResponseOperationContext(
                        ir=context.ir,
                        operation_key=generated.operation_key,
                        operation_method=request.method,
                        operation_path=self.config_catalog.require_operation(
                            generated.operation_key
                        ).snapshot.path,
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
                result = CatalogTestCase(
                    case_id=case_id,
                    request=catalog_request,
                    response_body=response_body,
                    failure=failure,
                )
                span.set_output(
                    {
                        "case_id": case_id,
                        "status_code": response.status_code,
                        "failed": failure is not None,
                        "body_retained": response_body is not None,
                        "body_truncated": response.body_truncated,
                    }
                )
                return result
            except TargetHTTPTimeout:
                failure = parse_transport_failure(
                    code="request_timeout",
                    message="HTTP request timed out",
                )
                span.mark_error(failure.messages[0])
            except TargetHTTPTransportError as exc:
                failure = parse_transport_failure(
                    code=exc.code,
                    message=str(exc),
                )
                span.mark_error(failure.messages[0])

        return CatalogTestCase(
            case_id=case_id,
            request=catalog_request,
            response_body=None,
            failure=failure,
        )


def _catalog_request(generated: GeneratedTestCase) -> dict[str, Any]:
    """Copy one generated case into canonical direct-name request JSON.

    Generator control state and internal node identities stay outside the Test
    Case. Header names are lowercased because HTTP makes them case-insensitive
    and semantic handles use that canonical spelling. The optional Body key
    preserves the difference between omission and an explicitly sent null.
    """
    request: dict[str, Any] = {
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


def _decode_failure_body(
    body: bytes | None,
    *,
    media_type: str | None,
    encoding: str | None,
) -> Any | None:
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


def _target_query_items(
    request: PreparedTestRequest,
) -> list[tuple[str, str] | tuple[str, str, bool]]:
    allow_reserved = set(request.query_allow_reserved_indices)
    return [
        (name, value, True) if index in allow_reserved else (name, value)
        for index, (name, value) in enumerate(request.query_items)
    ]
