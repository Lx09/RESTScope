"""Send requests to the tested API and create independent response views.

The Client accepts requests already normalized by :mod:`request`, opens an
isolated synchronous HTTP connection, and independently supplies complete
Monitor facts, a bounded Live Observer view, and the body requested by its
caller. Neither Tool nor Batch code needs to inspect Client configuration.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime

import httpx

from .errors import TargetAPIError, TargetAPITimeout
from .observation import (
    BufferedTargetResponse,
    TargetResponseObservation,
    TargetResponseOperationContext,
    TargetResponseProcessor,
    TargetResponseProcessorResult,
    TargetResponseProcessorWarning,
    TargetTransportObservation,
    _observation_request_json,
)
from .request import PreparedTargetRequest

_ClientFactory = Callable[..., httpx.Client]
_OBSERVER_BODY_LIMIT = 1024 * 1024


class TargetAPIClient:
    """Send prepared requests through the App-bound target connection.

    This is the shared network trust seam for generated batches and HTTP
    tools. It refuses absolute/cross-host paths, unsafe per-call headers,
    redirects and raw provider exceptions. An optional synchronous processor
    receives the complete response independently from the bounded body returned
    to a Tool, Batch, or UI observer.
    """

    def __init__(
        self,
        *,
        client_factory: _ClientFactory = httpx.Client,
        response_processor: TargetResponseProcessor | None = None,
        run_observer: object | None = None,
    ) -> None:
        """Keep target I/O collaborators and an optional run observer.

        Args:
            client_factory: Builds the bounded synchronous HTTP client.
            response_processor: Optional API Behavior Monitor callback.
            run_observer: Optional current-run observer. It receives copies of
                prepared requests and already-bounded responses, and its
                failures never replace the target result.
        """
        self._client_factory = client_factory
        self._response_processor = response_processor
        self._run_observer = run_observer

    @contextmanager
    def _stream(
        self,
        prepared: PreparedTargetRequest,
        *,
        timeout_seconds: float = 30,
        request_kwargs: Mapping[str, object] | None = None,
    ) -> Iterator[httpx.Response]:
        """Open one response stream and translate provider-specific failures."""

        try:
            with self._client_factory(
                timeout=timeout_seconds,
                follow_redirects=False,
            ) as client, client.stream(
                prepared.method,
                prepared.url,
                headers=prepared.headers,
                **dict(request_kwargs or {}),
            ) as response:
                yield response
        except httpx.TimeoutException as exc:
            raise TargetAPITimeout("HTTP request timed out") from exc
        except httpx.HTTPError as exc:
            raise TargetAPIError(
                "request_failed",
                f"HTTP request failed ({type(exc).__name__})",
            ) from exc

    def send(
        self,
        prepared: PreparedTargetRequest,
        *,
        timeout_seconds: float = 30,
        request_kwargs: Mapping[str, object] | None = None,
        success_body_limit: int | None = None,
        failure_body_limit: int | None = None,
        truncate_body: bool = False,
        response_context: TargetResponseOperationContext | None = None,
    ) -> BufferedTargetResponse:
        """Execute one prepared request and publish bounded live evidence.

        Observation wraps the target operation so request
        validation, target effects, response processing, and exception types
        remain unchanged. An observer defect is swallowed independently.
        """
        frozen_prepared, frozen_request_kwargs = _freeze_application_request(
            prepared,
            request_kwargs=request_kwargs,
        )
        exchange = self._start_observed_exchange(
            prepared=frozen_prepared,
            request_kwargs=frozen_request_kwargs,
            response_context=response_context,
        )
        try:
            result, observer_result = self._send(
                frozen_prepared,
                timeout_seconds=timeout_seconds,
                request_kwargs=frozen_request_kwargs,
                success_body_limit=success_body_limit,
                failure_body_limit=failure_body_limit,
                truncate_body=truncate_body,
                response_context=response_context,
                observer_enabled=exchange is not None,
            )
        except BaseException as exc:
            if isinstance(exc, TargetAPITimeout) or (
                isinstance(exc, TargetAPIError)
                and exc.code == "request_failed"
            ):
                self._process_transport_failure(
                    prepared=frozen_prepared,
                    request_kwargs=frozen_request_kwargs,
                    response_context=response_context,
                    error=exc,
                )
            if exchange is not None:
                try:
                    exchange.fail(exc)
                except Exception:  # noqa: BLE001, S110
                    pass
            raise
        if exchange is not None:
            try:
                exchange.finish(observer_result)
            except Exception:  # noqa: BLE001, S110
                pass
        directive = (
            result.processor_result.replay_directive
            if result.processor_result is not None
            else None
        )
        if directive is not None and response_context is not None:
            # Replay is advisory confirmation. It reuses the frozen method,
            # URL, final application headers, and body arguments, but its
            # target or Monitor failure never replaces the Primary response.
            replay_context = replace(
                response_context,
                abstract_test_case_id=None,
                batch_id=None,
                batch_case_index=None,
                replay_directive=directive,
            )
            try:
                replay_result = self.send(
                    frozen_prepared,
                    timeout_seconds=timeout_seconds,
                    request_kwargs=frozen_request_kwargs,
                    success_body_limit=success_body_limit,
                    failure_body_limit=failure_body_limit,
                    truncate_body=truncate_body,
                    response_context=replay_context,
                )
                replay_processor = replay_result.processor_result
                if replay_processor is not None and result.processor_result is not None:
                    replay_details = replay_processor.details or {}
                    primary_details = result.processor_result.details or {}
                    merged_processor = replace(
                        result.processor_result,
                        warnings=(
                            *result.processor_result.warnings,
                            *replay_processor.warnings,
                        ),
                        details={
                            **primary_details,
                            "bug_found": replay_details.get("bug_found"),
                            "bug_categories": replay_details.get(
                                "bug_categories",
                                [],
                            ),
                        },
                    )
                    result = replace(result, processor_result=merged_processor)
            except (TargetAPIError, TargetAPITimeout):
                pass
        return result
    def _process_transport_failure(
        self,
        *,
        prepared: PreparedTargetRequest,
        request_kwargs: Mapping[str, object] | None,
        response_context: TargetResponseOperationContext | None,
        error: TargetAPITimeout | TargetAPIError,
    ) -> None:
        """Offer one no-response attempt to the advisory persistence processor."""

        if self._response_processor is None or response_context is None:
            return
        callback = getattr(self._response_processor, "process_transport", None)
        if not callable(callback):
            return
        code = "request_timeout" if isinstance(error, TargetAPITimeout) else error.code
        try:
            callback(
                TargetTransportObservation(
                    method=prepared.method,
                    path=prepared.path,
                    url=str(prepared.url),
                    code=code,
                    message=str(error),
                    request_json=_observation_request_json(
                        prepared,
                        request_kwargs=request_kwargs,
                    ),
                ),
                response_context,
            )
        except Exception:  # noqa: BLE001
            # Monitoring cannot replace the original target transport exception.
            return

    def _send(
        self,
        prepared: PreparedTargetRequest,
        *,
        timeout_seconds: float = 30,
        request_kwargs: Mapping[str, object] | None = None,
        success_body_limit: int | None,
        failure_body_limit: int | None,
        truncate_body: bool,
        response_context: TargetResponseOperationContext | None,
        observer_enabled: bool,
    ) -> tuple[BufferedTargetResponse, BufferedTargetResponse]:
        """Execute, project a bounded caller body, and process the complete body.

        Body limits apply only to the returned ``BufferedTargetResponse``. They
        do not limit the Response Monitor observation. Processor failures become
        warnings and never replace the original HTTP result.
        """

        with self._stream(
            prepared,
            timeout_seconds=timeout_seconds,
            request_kwargs=request_kwargs,
        ) as response:
            body: bytes | None = None
            body_truncated = False
            successful = 200 <= response.status_code < 300
            selected_body_limit = (
                success_body_limit if successful else failure_body_limit
            )
            processor_enabled = (
                self._response_processor is not None
                and response_context is not None
            )
            complete_body: bytes | None = None
            if (
                selected_body_limit is not None
                or processor_enabled
                or observer_enabled
            ):
                complete_body = b"".join(response.iter_bytes())
            if selected_body_limit is not None and complete_body is not None:
                body_truncated = len(complete_body) > selected_body_limit
                body = complete_body[:selected_body_limit]
            processor_result: TargetResponseProcessorResult | None = None
            if processor_enabled and complete_body is not None:
                received_at = datetime.now(UTC)
                observation = TargetResponseObservation(
                    method=prepared.method,
                    path=prepared.path,
                    url=str(response.url),
                    status_code=response.status_code,
                    reason_phrase=response.reason_phrase,
                    headers={
                        name.lower(): value
                        for name, value in response.headers.items()
                    },
                    body=complete_body,
                    body_truncated=False,
                    received_at=received_at,
                    request_json=_observation_request_json(
                        prepared,
                        request_kwargs=request_kwargs,
                    ),
                )
                try:
                    # Monitoring is advisory to target execution. A defect is
                    # returned as a structured warning so testing still receives
                    # the target's real status and body.
                    raw_processor_result = self._response_processor.process(
                        observation,
                        response_context,
                    )
                    if isinstance(
                        raw_processor_result,
                        TargetResponseProcessorResult,
                    ):
                        processor_result = raw_processor_result
                    elif isinstance(
                        raw_processor_result,
                        TargetResponseProcessorWarning,
                    ):
                        processor_result = TargetResponseProcessorResult(
                            response_validation="partial",
                            warnings=(raw_processor_result,),
                        )
                    else:
                        processor_result = TargetResponseProcessorResult(
                            response_validation="not_evaluated",
                        )
                except Exception as exc:  # noqa: BLE001
                    processor_result = TargetResponseProcessorResult(
                        response_validation="partial",
                        warnings=(
                            TargetResponseProcessorWarning(
                                code="api_behavior_monitor_failed",
                                message="API behavior monitoring failed",
                                issues=(type(exc).__name__,),
                            ),
                        ),
                    )
            if body_truncated and not truncate_body:
                # The complete body has already reached the advisory Monitor.
                # A Tool that requires a complete bounded projection still
                # fails closed instead of mistaking a prefix for the response.
                raise TargetAPIError(
                    "response_too_large",
                    "HTTP response body exceeds the configured limit",
                )
            headers = {
                name.lower(): value
                for name, value in response.headers.items()
            }
            caller_result = BufferedTargetResponse(
                status_code=response.status_code,
                reason_phrase=response.reason_phrase,
                url=str(response.url),
                headers=headers,
                encoding=response.encoding,
                body=body,
                body_truncated=body_truncated,
                processor_result=processor_result,
            )
            observer_body = (
                complete_body[:_OBSERVER_BODY_LIMIT]
                if observer_enabled and complete_body is not None
                else None
            )
            observer_result = BufferedTargetResponse(
                status_code=response.status_code,
                reason_phrase=response.reason_phrase,
                url=str(response.url),
                headers=headers,
                encoding=response.encoding,
                body=observer_body,
                body_truncated=bool(
                    complete_body is not None
                    and len(complete_body) > _OBSERVER_BODY_LIMIT
                ),
                processor_result=processor_result,
            )
            return caller_result, observer_result

    def _start_observed_exchange(
        self,
        *,
        prepared: PreparedTargetRequest,
        request_kwargs: Mapping[str, object] | None,
        response_context: TargetResponseOperationContext | None,
    ) -> object | None:
        """Open one observer handle without making it part of HTTP success."""
        if self._run_observer is None:
            return None
        try:
            return self._run_observer.start_http_exchange(
                method=prepared.method,
                path=prepared.path,
                url=str(prepared.url),
                headers=prepared.headers,
                request_kwargs=request_kwargs,
                operation_key=(
                    response_context.operation_key
                    if response_context is not None
                    else None
                ),
                path_template=(
                    response_context.operation_path
                    if response_context is not None
                    else None
                ),
            )
        except Exception:  # noqa: BLE001
            return None


def _freeze_application_request(
    prepared: PreparedTargetRequest,
    *,
    request_kwargs: Mapping[str, object] | None,
) -> tuple[PreparedTargetRequest, dict[str, object]]:
    """Materialize final application headers and body bytes before Primary send.

    JSON and text caller inputs otherwise remain recipes that a later Replay
    would serialize again. Building one in-memory HTTP request captures the
    exact bytes and generated content headers once without opening a connection.
    """

    request = httpx.Request(
        prepared.method,
        prepared.url,
        headers=prepared.headers,
        **deepcopy(dict(request_kwargs or {})),
    )
    content = request.read()
    headers = dict(prepared.headers)
    if "json" in (request_kwargs or {}) and not any(
        name.lower() == "content-type" for name in headers
    ):
        # JSON serialization owns its application media type. Host and
        # Content-Length remain connection/protocol details and may be rebuilt
        # independently for Primary and Replay.
        headers["Content-Type"] = request.headers["content-type"]
    frozen = PreparedTargetRequest(
        method=prepared.method,
        path=prepared.path,
        url=request.url,
        headers=headers,
    )
    return frozen, ({"content": content} if content else {})
