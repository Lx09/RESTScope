from __future__ import annotations

import json
import time

from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

import pytest


PHOENIX_ENDPOINT = "http://localhost:6006"


def _get_json(url: str) -> dict:
    opener = build_opener(ProxyHandler({}))
    with opener.open(url, timeout=2) as response:  # noqa: S310 - fixed localhost contract endpoint.
        return json.load(response)


def _wait_for_phoenix() -> None:
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _get_json(f"{PHOENIX_ENDPOINT}/v1/projects?limit=1")
            return
        except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(0.25)
    raise AssertionError("Local Phoenix did not become ready within 30 seconds") from last_error


def _wait_for_traces(project_name: str) -> dict:
    trace_url = (
        f"{PHOENIX_ENDPOINT}/v1/projects/{quote(project_name, safe='')}/traces"
        "?include_spans=true&limit=100"
    )
    span_url = (
        f"{PHOENIX_ENDPOINT}/v1/projects/{quote(project_name, safe='')}/spans"
        "?limit=100"
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            traces = _get_json(trace_url)
            if traces.get("data"):
                spans = _get_json(span_url)
                if spans.get("data"):
                    return {"traces": traces, "spans": spans}
        except HTTPError as exc:
            if exc.code != 404:
                raise
        time.sleep(0.25)
    raise AssertionError("Phoenix did not return the exported contract traces")


@pytest.mark.phoenix_contract
def test_local_phoenix_accepts_restscope_trace_hierarchy(request, tmp_path: Path) -> None:
    if "phoenix_contract" not in request.config.option.markexpr:
        pytest.skip("select -m phoenix_contract to run the local Phoenix contract")

    from restscope import RESTScopeApp
    from restscope.agent import (
        FakeOperationDependencyAnalyzer,
        FakeOperationTestRunner,
        RESTScopeRunRequest,
    )
    from restscope.capabilities import build_capabilities
    from restscope.llm import (
        LLMClient,
        LLMMessage,
        LLMProviderRegistry,
        LLMRequest,
        LLMResponse,
        ToolCall,
        ToolSpec,
    )
    from restscope.llm.providers.base import BaseLLMProvider
    from restscope.observability import build_tracing_runtime
    from restscope.restscope_config import RESTScopeConfig, TracingConfig

    _wait_for_phoenix()
    project_name = f"restscope-contract-{uuid4().hex}"
    tracing_config = TracingConfig(
        enabled=True,
        collector_endpoint=PHOENIX_ENDPOINT,
        project_name=project_name,
        api_key="",
        protocol="http/protobuf",
        batch=True,
        max_content_bytes=65536,
        flush_timeout_seconds=5,
    )
    runtime = build_tracing_runtime(
        tracing_config,
        secret_values=["contract-secret"],
    )
    assert runtime.enabled is True

    capabilities = build_capabilities(presets=(), tracing_runtime=runtime)
    capabilities.tool_registry.register(
        spec=ToolSpec(
            name="contract.tool",
            description="Emit a contract tool span",
            kind="local_function",
            input_schema={"type": "object"},
        ),
        handler=lambda context, **arguments: {
            "structured": {
                "operations": len(context.ir.operations),
                "argument_keys": sorted(arguments),
            }
        },
    )
    config = replace(
        RESTScopeConfig.from_environment(tmp_path / ".env"),
        tracing=tracing_config,
    )
    app = RESTScopeApp.from_config(
        config,
        operation_runner=FakeOperationTestRunner(),
        dependency_analyzer=FakeOperationDependencyAnalyzer(),
        capability_runtime=capabilities,
        tracing_runtime=runtime,
    )
    app.initialize(
        schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
        base_url="http://example.test",
        headers={"Authorization": "Bearer contract-secret"},
    )
    app.run(
        RESTScopeRunRequest(
            metadata={"task_id": "phoenix-contract"},
        )
    )
    capabilities.tool_executor.execute(
        tool_call=ToolCall(
            id="contract-tool",
            name="contract.tool",
            arguments={"token": "contract-secret"},
        ),
        role="decision_maker",
        state={},
    )

    reasoning = "contract private reasoning"

    class ContractProvider(BaseLLMProvider):
        name = "contract"

        def invoke(self, llm_request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                provider=self.name,
                model=llm_request.model,
                content="contract response",
                tool_calls=[
                    ToolCall(
                        id="reasoning",
                        name="openapi.inspect",
                        provider_context={"reasoning_content": reasoning},
                    )
                ],
                prompt_tokens=3,
                completion_tokens=5,
                total_tokens=8,
            )

    registry = LLMProviderRegistry()
    registry.register(ContractProvider())
    LLMClient(registry, tracing_runtime=runtime).invoke(
        LLMRequest(
            provider="contract",
            model="contract-model",
            messages=[LLMMessage(role="user", content="contract-secret")],
        )
    )
    with runtime.span(
        "contract.truncated",
        kind="CHAIN",
        input_value={"content": "x" * 70000},
    ):
        pass
    app.close()

    payload = _wait_for_traces(project_name)
    rendered = json.dumps(payload, ensure_ascii=False, default=str)
    if "contract-secret" in rendered:
        pytest.fail("Phoenix trace payload leaked a registered secret")
    if reasoning in rendered:
        pytest.fail("Phoenix trace payload leaked reasoning_content")

    expected_names = {
        "RESTScopeApp.run",
        "RESTScopeMainGraph.run",
        "OperationTestAgent.run",
        "contract.tool",
        "LLMClient.invoke",
        "contract.truncated",
    }
    spans = payload["spans"]["data"]
    assert expected_names.issubset({span["name"] for span in spans})
    assert {"CHAIN", "AGENT", "TOOL", "LLM"}.issubset(
        {span["span_kind"] for span in spans}
    )

    app_span = next(span for span in spans if span["name"] == "RESTScopeApp.run")
    graph_span = next(span for span in spans if span["name"] == "RESTScopeMainGraph.run")
    operation_span = next(
        span
        for span in spans
        if span["name"] == "OperationTestAgent.run"
        and span["context"]["trace_id"] == app_span["context"]["trace_id"]
    )
    truncated_span = next(span for span in spans if span["name"] == "contract.truncated")

    assert graph_span["parent_id"] == app_span["context"]["span_id"]
    assert operation_span["parent_id"] == graph_span["context"]["span_id"]
    assert truncated_span["attributes"]["restscope.input.truncated"] is True
    assert truncated_span["attributes"]["restscope.input.original_size_bytes"] > 65536
