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
        return None


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

    import restscope.target_api as target_api

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
