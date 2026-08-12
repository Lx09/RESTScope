"""Send requests and separate complete Monitor facts from bounded Tool views.

The transport accepts requests already normalized by the request boundary,
opens isolated synchronous HTTP clients, reads a complete response when either
the caller or Response Monitor needs it, and gives Agent-facing callers only
their requested bounded projection. The Monitor receives the complete body and
an actual request envelope before any slow semantic processing begins.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
import base64
import json

import httpx

from .errors import TargetHTTPTimeout, TargetHTTPTransportError
from .observation import (
    BufferedTargetResponse,
    TargetResponseObservation,
    TargetResponseOperationContext,
    TargetResponseProcessor,
    TargetResponseProcessorResult,
    TargetResponseProcessorWarning,
)
from .request import (
    PreparedTargetRequest,
    QueryItem,
    build_target_url,
    is_json_media_type,
    is_sensitive_header,
    merge_target_headers,
    normalize_media_type,
)

from restscope.data_types import JSONObject, JSONValue


ClientFactory = Callable[..., httpx.Client]


class TargetHTTPTransport:
    """Validate and send isolated requests to the App-bound target API.

    This is the shared network trust boundary for generated batches and HTTP
    tools. It refuses absolute/cross-host paths, unsafe per-call headers,
    redirects and raw provider exceptions. An optional synchronous processor
    receives the complete response independently from the bounded body returned
    to a Tool, Batch, or UI observer.
    """

    def __init__(
        self,
        *,
        client_factory: ClientFactory = httpx.Client,
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
        self.client_factory = client_factory
        self.response_processor = response_processor
        self.run_observer = run_observer

    @property
    def has_response_processor(self) -> bool:
        """Report whether responses will be offered to a Behavior Monitor."""
        return self.response_processor is not None

    def prepare(
        self,
        *,
        method: str,
        base_url: str | None,
        path: str,
        query_items: Sequence[QueryItem] = (),
        context_headers: Mapping[str, str] | None = None,
        request_headers: Mapping[str, str] | None = None,
        override_context_headers: bool = True,
        allowed_sensitive_request_headers: Collection[str] = (),
    ) -> PreparedTargetRequest:
        """Validate and resolve a request without opening a client or socket."""

        return PreparedTargetRequest(
            method=method,
            path=path,
            url=build_target_url(base_url, path, query_items),
            headers=merge_target_headers(
                context_headers or {},
                request_headers or {},
                override_context_headers=override_context_headers,
                allowed_sensitive_request_headers=allowed_sensitive_request_headers,
            ),
        )

    @contextmanager
    def stream(
        self,
        *,
        method: str,
        base_url: str | None,
        path: str,
        query_items: Sequence[QueryItem] = (),
        context_headers: Mapping[str, str] | None = None,
        request_headers: Mapping[str, str] | None = None,
        override_context_headers: bool = True,
        allowed_sensitive_request_headers: Collection[str] = (),
        timeout_seconds: float = 30,
        request_kwargs: Mapping[str, object] | None = None,
    ) -> Iterator[httpx.Response]:
        """Send one prepared target request and yield a bounded response while guaranteeing connection cleanup. Transport and observation failures are translated at this boundary."""
        prepared = self.prepare(
            method=method,
            base_url=base_url,
            path=path,
            query_items=query_items,
            context_headers=context_headers,
            request_headers=request_headers,
            override_context_headers=override_context_headers,
            allowed_sensitive_request_headers=allowed_sensitive_request_headers,
        )
        with self.stream_prepared(
            prepared,
            timeout_seconds=timeout_seconds,
            request_kwargs=request_kwargs,
        ) as response:
            yield response

    @contextmanager
    def stream_prepared(
        self,
        prepared: PreparedTargetRequest,
        *,
        timeout_seconds: float = 30,
        request_kwargs: Mapping[str, object] | None = None,
    ) -> Iterator[httpx.Response]:
        """Execute a request previously accepted by :meth:`prepare`."""

        try:
            with self.client_factory(timeout=timeout_seconds, follow_redirects=False) as client:
                with client.stream(
                    prepared.method,
                    prepared.url,
                    headers=prepared.headers,
                    **dict(request_kwargs or {}),
                ) as response:
                    yield response
        except httpx.TimeoutException as exc:
            raise TargetHTTPTimeout("HTTP request timed out") from exc
        except httpx.HTTPError as exc:
            raise TargetHTTPTransportError(
                "request_failed",
                f"HTTP request failed ({type(exc).__name__})",
            ) from exc

    def request_prepared(
        self,
        prepared: PreparedTargetRequest,
        *,
        timeout_seconds: float = 30,
        request_kwargs: Mapping[str, object] | None = None,
        response_body_limit: int | None = None,
        failure_response_body_limit: int | None = None,
        truncate_response_body: bool = False,
        buffer_success_body_only: bool = False,
        processor_context: TargetResponseOperationContext | None = None,
    ) -> BufferedTargetResponse:
        """Execute one prepared request and publish bounded live evidence.

        Observation wraps the existing transport operation so request
        validation, target effects, response processing, and exception types
        remain unchanged. An observer defect is swallowed independently.
        """
        exchange = self._start_observed_exchange(
            prepared=prepared,
            request_kwargs=request_kwargs,
            processor_context=processor_context,
        )
        try:
            result = self._request_prepared_unobserved(
                prepared,
                timeout_seconds=timeout_seconds,
                request_kwargs=request_kwargs,
                response_body_limit=response_body_limit,
                failure_response_body_limit=failure_response_body_limit,
                truncate_response_body=truncate_response_body,
                buffer_success_body_only=buffer_success_body_only,
                processor_context=processor_context,
            )
        except BaseException as exc:
            if exchange is not None:
                try:
                    exchange.fail(exc)
                except Exception:
                    pass
            raise
        if exchange is not None:
            try:
                exchange.finish(result)
            except Exception:
                pass
        return result

    def _request_prepared_unobserved(
        self,
        prepared: PreparedTargetRequest,
        *,
        timeout_seconds: float = 30,
        request_kwargs: Mapping[str, object] | None = None,
        response_body_limit: int | None = None,
        failure_response_body_limit: int | None = None,
        truncate_response_body: bool = False,
        buffer_success_body_only: bool = False,
        processor_context: TargetResponseOperationContext | None = None,
    ) -> BufferedTargetResponse:
        """Execute, project a bounded caller body, and process the complete body.

        Body limits apply only to the returned ``BufferedTargetResponse``. They
        do not limit the Response Monitor observation. Processor failures become
        warnings and never replace the original HTTP result.
        """

        with self.stream_prepared(
            prepared,
            timeout_seconds=timeout_seconds,
            request_kwargs=request_kwargs,
        ) as response:
            body: bytes | None = None
            body_truncated = False
            successful = 200 <= response.status_code < 300
            # Choose the Agent-facing projection limit independently from the
            # complete bytes required by the response processor.
            selected_body_limit = (
                failure_response_body_limit
                if not successful and failure_response_body_limit is not None
                else response_body_limit
                if response_body_limit is not None
                and (not buffer_success_body_only or successful)
                else None
            )
            processor_enabled = (
                self.response_processor is not None
                and processor_context is not None
            )
            complete_body: bytes | None = None
            if selected_body_limit is not None or processor_enabled:
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
                    # Monitoring is advisory to transport. A monitor defect is
                    # returned as a structured warning so testing still receives
                    # the target's real status and body.
                    raw_processor_result = self.response_processor.process(
                        observation,
                        processor_context,
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
                except Exception as exc:
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
            if body_truncated and not truncate_response_body:
                # The complete body has already reached the advisory Monitor.
                # A Tool that requires a complete bounded projection still
                # fails closed instead of mistaking a prefix for the response.
                raise TargetHTTPTransportError(
                    "response_too_large",
                    "HTTP response body exceeds the configured limit",
                )
            return BufferedTargetResponse(
                status_code=response.status_code,
                reason_phrase=response.reason_phrase,
                url=str(response.url),
                headers={
                    name.lower(): value
                    for name, value in response.headers.items()
                },
                encoding=response.encoding,
                body=body,
                body_truncated=body_truncated,
                processor_result=processor_result,
            )

    def _start_observed_exchange(
        self,
        *,
        prepared: PreparedTargetRequest,
        request_kwargs: Mapping[str, object] | None,
        processor_context: TargetResponseOperationContext | None,
    ) -> object | None:
        """Open one observer handle without making it part of HTTP success."""
        if self.run_observer is None:
            return None
        try:
            return self.run_observer.start_http_exchange(
                method=prepared.method,
                path=prepared.path,
                url=str(prepared.url),
                headers=prepared.headers,
                request_kwargs=request_kwargs,
                operation_key=(
                    processor_context.operation_key
                    if processor_context is not None
                    else None
                ),
                path_template=(
                    processor_context.operation_path
                    if processor_context is not None
                    else None
                ),
            )
        except Exception:
            return None


def _observation_request_json(
    prepared: PreparedTargetRequest,
    *,
    request_kwargs: Mapping[str, object] | None,
) -> JSONObject:
    """Build the actual persisted request view without secret-bearing headers."""

    headers = {
        name.lower(): value
        for name, value in prepared.headers.items()
        if not is_sensitive_header(name)
    }
    output: JSONObject = {
        "path": prepared.path,
        "query": [
            [name, value]
            for name, value in prepared.url.params.multi_items()
        ],
        "headers": headers,
    }
    body = _observation_request_body(
        request_kwargs or {},
        media_type=_request_media_type(prepared.headers),
    )
    if body is not None:
        output["body"] = body
    return output


def _observation_request_body(
    request_kwargs: Mapping[str, object],
    *,
    media_type: str | None,
) -> JSONObject | None:
    """Encode one caller-supplied body into JSON, text, or Base64 evidence."""

    if "json" in request_kwargs:
        value = _validated_json_value(request_kwargs["json"])
        return {
            "media_type": media_type or "application/json",
            "encoding": "json",
            "value": value,
        }
    if "content" not in request_kwargs:
        return None
    content = request_kwargs["content"]
    if isinstance(content, str):
        raw = content.encode("utf-8")
    elif isinstance(content, bytes):
        raw = content
    else:
        return None
    if is_json_media_type(media_type):
        try:
            value = _validated_json_value(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            pass
        else:
            return {
                "media_type": media_type or "application/json",
                "encoding": "json",
                "value": value,
            }
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "media_type": media_type or "application/octet-stream",
            "encoding": "base64",
            "value": base64.b64encode(raw).decode("ascii"),
        }
    return {
        "media_type": media_type or "text/plain",
        "encoding": "text",
        "value": text,
    }


def _validated_json_value(value: object) -> JSONValue:
    """Detach and validate one opaque caller value through standard JSON."""

    return json.loads(json.dumps(value, ensure_ascii=False))


def _request_media_type(headers: Mapping[str, str]) -> str | None:
    """Return a normalized request Content-Type without parameters."""

    value = next(
        (item for name, item in headers.items() if name.lower() == "content-type"),
        None,
    )
    if value is None:
        return None
    return normalize_media_type(value)
