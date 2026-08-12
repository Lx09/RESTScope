"""Target API Client behavior that feeds complete facts into the Monitor."""

from __future__ import annotations


class _CapturingProcessor:
    """Retain the single observation offered by the Client."""

    def __init__(self) -> None:
        self.observation = None

    def process(self, observation, context):
        del context
        self.observation = observation
        return None


def test_monitor_receives_full_body_and_sanitized_actual_request_view() -> None:
    """Agent-facing truncation cannot truncate the independently persisted fact."""
    import httpx

    from restscope.target_api import (
        TargetAPIClient,
        TargetResponseOperationContext,
        prepare_target_request,
    )

    response_body = b'{"value":"abcdefghijklmnopqrstuvwxyz"}'

    def handler(request: httpx.Request) -> httpx.Response:
        """Return one response larger than the caller's display projection."""

        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            content=response_body,
            request=request,
        )

    def client_factory(**kwargs):
        """Build an isolated real HTTPX client over the fake network edge."""

        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)

    processor = _CapturingProcessor()
    client = TargetAPIClient(
        client_factory=client_factory,
        response_processor=processor,
    )
    prepared = prepare_target_request(
        method="POST",
        base_url="https://example.test",
        path="/items",
        query_items=(("tag", "a"), ("tag", "b")),
        context_headers={
            "Authorization": "Bearer hidden",
            "Cookie": "session=hidden",
            "Accept": "application/json",
        },
        request_headers={"Content-Type": "application/json"},
    )

    response = client.send(
        prepared,
        request_kwargs={"content": b'{"name":"Ada"}'},
        success_body_limit=12,
        truncate_body=True,
        response_context=TargetResponseOperationContext(ir=object()),
    )

    assert response.body == response_body[:12]
    assert response.body_truncated is True
    assert processor.observation.body == response_body
    assert processor.observation.body_truncated is False
    assert processor.observation.request_json == {
        "path": "/items",
        "query": [["tag", "a"], ["tag", "b"]],
        "headers": {
            "accept": "application/json",
            "content-type": "application/json",
        },
        "body": {
            "media_type": "application/json",
            "encoding": "json",
            "value": {"name": "Ada"},
        },
    }
