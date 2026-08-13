"""Public request preparation and response projection for the target API."""

from __future__ import annotations


class _CapturingProcessor:
    """Retain the complete response offered to the Behavior Monitor seam."""

    def __init__(self) -> None:
        self.observation = None

    def process(self, observation, context):
        """Capture one observation without changing the target response."""

        del context
        self.observation = observation


class _CapturingExchange:
    """Retain the separately bounded response offered to Live Observer."""

    def __init__(self) -> None:
        self.response = None

    def finish(self, response) -> None:
        """Capture the Observer projection after a successful exchange."""

        self.response = response

    def fail(self, exc: BaseException) -> None:
        """Fail the scenario if the target request unexpectedly fails."""

        raise AssertionError("target request unexpectedly failed") from exc


class _CapturingObserver:
    """Open one fake live exchange without exposing Client implementation."""

    def __init__(self) -> None:
        self.exchange = _CapturingExchange()

    def start_http_exchange(self, **details):
        """Return the fake exchange and ignore already-tested request details."""

        del details
        return self.exchange


def test_target_api_facade_exposes_only_shared_integration_entries() -> None:
    """Readers enter through one Client, one prepare function, and shared records."""

    from restscope import target_api

    assert set(target_api.__all__) == {
        "BufferedTargetResponse",
        "PreparedTargetRequest",
        "TargetAPIClient",
        "TargetAPIError",
        "TargetAPITimeout",
        "TargetResponseObservation",
        "TargetTransportObservation",
        "TargetResponseOperationContext",
        "TargetResponseProcessor",
        "TargetResponseProcessorResult",
        "TargetResponseProcessorWarning",
        "prepare_target_request",
    }


def test_prepare_is_pure_and_client_keeps_three_response_views_independent() -> None:
    """Monitor, Observer, and caller receive the body projection each one owns."""

    import httpx

    from restscope.target_api import (
        TargetAPIClient,
        TargetResponseOperationContext,
        prepare_target_request,
    )

    response_body = b"x" * (1024 * 1024 + 37)
    opened_clients = 0

    def handler(request: httpx.Request) -> httpx.Response:
        """Return a body larger than the fixed Live Observer projection."""

        return httpx.Response(200, content=response_body, request=request)

    def client_factory(**kwargs):
        """Count actual network clients independently from pure preparation."""

        nonlocal opened_clients
        opened_clients += 1
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    prepared = prepare_target_request(
        method="GET",
        base_url="https://example.test",
        path="/items",
    )
    assert opened_clients == 0

    processor = _CapturingProcessor()
    observer = _CapturingObserver()
    client = TargetAPIClient(
        client_factory=client_factory,
        response_processor=processor,
        run_observer=observer,
    )
    response = client.send(
        prepared,
        success_body_limit=12,
        truncate_body=True,
        response_context=TargetResponseOperationContext(ir=object()),
    )

    assert opened_clients == 1
    assert response.body == response_body[:12]
    assert response.body_truncated is True
    assert processor.observation.body == response_body
    assert observer.exchange.response.body == response_body[: 1024 * 1024]
    assert observer.exchange.response.body_truncated is True


def test_client_does_not_read_an_unused_success_body() -> None:
    """A Batch-like call without Monitor or Observer leaves success bytes unread."""

    import httpx

    from restscope.target_api import TargetAPIClient, prepare_target_request

    class UnreadableSuccessBody(httpx.SyncByteStream):
        """Raise if the Client consumes a success body nobody requested."""

        def __iter__(self):
            raise AssertionError("unused success body was read")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=UnreadableSuccessBody(), request=request)

    client = TargetAPIClient(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        )
    )
    response = client.send(
        prepare_target_request(
            method="GET",
            base_url="https://example.test",
            path="/items",
        )
    )

    assert response.body is None
    assert response.body_truncated is False


def test_processor_requested_replay_resends_identical_request_once_without_recursion() -> None:
    """One Primary directive repeats frozen application bytes through the same processor."""

    import httpx

    from restscope.target_api import (
        TargetAPIClient,
        TargetResponseOperationContext,
        TargetResponseProcessorResult,
        prepare_target_request,
    )
    from restscope.target_api.observation import TargetReplayDirective

    requests: list[tuple[str, str, tuple[tuple[str, str], ...], bytes]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(
            (
                request.method,
                str(request.url),
                tuple(request.headers.multi_items()),
                request.content,
            )
        )
        return httpx.Response(
            500,
            headers={"content-type": "application/json"},
            content=b'{"error":"repeat"}',
            request=request,
        )

    class ReplayProcessor:
        """Request Replay only for Primary and record both Monitor passes."""

        def __init__(self):
            self.contexts = []

        def process(self, _observation, context):
            self.contexts.append(context)
            if context.replay_directive is not None:
                return TargetResponseProcessorResult(response_validation="evaluated")
            return TargetResponseProcessorResult(
                response_validation="evaluated",
                replay_directive=TargetReplayDirective(
                    primary_observation_id="primary",
                    state=object(),
                ),
            )

    processor = ReplayProcessor()
    client = TargetAPIClient(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler),
            **kwargs,
        ),
        response_processor=processor,
    )
    prepared = prepare_target_request(
        method="POST",
        base_url="https://example.test",
        path="/items",
        query_items=[("tag", "a"), ("tag", "b")],
        context_headers={"Authorization": "Bearer secret"},
        request_headers={"Content-Type": "application/octet-stream", "X-Case": "one"},
    )

    response = client.send(
        prepared,
        request_kwargs={"content": b"\x00exact-body"},
        success_body_limit=100,
        failure_body_limit=100,
        response_context=TargetResponseOperationContext(
            ir=object(),
            abstract_test_case_id="abstract",
            batch_id="batch",
            batch_case_index=0,
        ),
    )

    assert response.status_code == 500
    assert len(requests) == 2
    assert requests[0] == requests[1]
    assert len(processor.contexts) == 2
    replay_context = processor.contexts[1]
    assert replay_context.replay_directive is not None
    assert replay_context.abstract_test_case_id is None
    assert replay_context.batch_id is None
    assert replay_context.batch_case_index is None


def test_replay_processor_warnings_are_returned_with_the_primary_response() -> None:
    """Replay advisory failures remain visible without replacing Primary HTTP facts."""

    import httpx

    from restscope.target_api import (
        TargetAPIClient,
        TargetResponseOperationContext,
        TargetResponseProcessorResult,
        TargetResponseProcessorWarning,
        prepare_target_request,
    )
    from restscope.target_api.observation import TargetReplayDirective

    class WarningProcessor:
        """Request one Replay and report a persistence warning from that pass."""

        def process(self, _observation, context):
            if context.replay_directive is None:
                return TargetResponseProcessorResult(
                    response_validation="evaluated",
                    replay_directive=TargetReplayDirective(
                        primary_observation_id="primary",
                        state=object(),
                    ),
                )
            return TargetResponseProcessorResult(
                response_validation="partial",
                warnings=(TargetResponseProcessorWarning(
                    code="oracle_assessment_persistence_failed",
                    message="Bug Oracle Assessment could not be persisted",
                ),),
            )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"failure", request=request)

    response = TargetAPIClient(
        client_factory=lambda **kwargs: httpx.Client(transport=httpx.MockTransport(handler), **kwargs),
        response_processor=WarningProcessor(),
    ).send(
        prepare_target_request(method="GET", base_url="https://example.test", path="/items"),
        failure_body_limit=100,
        response_context=TargetResponseOperationContext(ir=object()),
    )

    assert response.status_code == 500
    assert response.processor_result is not None
    assert [warning.code for warning in response.processor_result.warnings] == [
        "oracle_assessment_persistence_failed"
    ]
