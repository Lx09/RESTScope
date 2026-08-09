"""Regression scenarios for observability. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest


def test_tracing_config_reads_every_explicit_environment_field(tmp_path: Path) -> None:
    """Scenario: verify that tracing config reads every explicit environment field."""
    from restscope.restscope_config import RESTScopeConfig

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "TRACING_ENABLED=true",
                "PHOENIX_COLLECTOR_ENDPOINT=http://phoenix.test:6006",
                "PHOENIX_PROJECT_NAME=restscope-test",
                "PHOENIX_API_KEY=phoenix-secret",
                "PHOENIX_PROTOCOL=http/protobuf",
                "TRACING_BATCH=false",
                "TRACING_MAX_CONTENT_BYTES=2048",
                "TRACING_FLUSH_TIMEOUT_SECONDS=2.5",
            ]
        ),
        encoding="utf-8",
    )

    tracing = RESTScopeConfig.from_environment(env_file).tracing

    assert tracing.enabled is True
    assert tracing.collector_endpoint == "http://phoenix.test:6006"
    assert tracing.project_name == "restscope-test"
    assert tracing.api_key == "phoenix-secret"
    assert tracing.protocol == "http/protobuf"
    assert tracing.batch is False
    assert tracing.max_content_bytes == 2048
    assert tracing.flush_timeout_seconds == 2.5
    assert "phoenix-secret" not in repr(tracing)


def test_ui_config_is_disabled_by_default_and_reads_flat_environment_fields(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Scenario: UI hosting is opt-in and accepts one valid loopback port."""
    from restscope.restscope_config import RESTScopeConfig

    monkeypatch.delenv("UI_ENABLED", raising=False)
    monkeypatch.delenv("UI_PORT", raising=False)
    default_config = RESTScopeConfig.from_environment(tmp_path / "missing.env")
    env_file = tmp_path / ".env"
    env_file.write_text("UI_ENABLED=true\nUI_PORT=9876\n", encoding="utf-8")
    enabled_config = RESTScopeConfig.from_environment(env_file)

    assert default_config.ui.enabled is False
    assert default_config.ui.port == 8765
    assert enabled_config.ui.enabled is True
    assert enabled_config.ui.port == 9876


@pytest.mark.parametrize("port", [0, 65536])
def test_ui_config_rejects_values_outside_the_tcp_port_range(port: int) -> None:
    """Scenario: an invalid UI port fails configuration before App startup."""
    from restscope.restscope_config import UIConfig

    with pytest.raises(ValueError, match="UI_PORT"):
        UIConfig(port=port)


def test_trace_content_encoder_only_redacts_registered_values() -> None:
    """Scenario: verify that trace content encoder only redacts registered values."""
    from restscope.observability.content import TraceContentEncoder
    from restscope.redaction import Redactor

    encoder = TraceContentEncoder(
        redactor=Redactor(["literal-secret"]),
        max_content_bytes=4096,
    )

    prepared = encoder.prepare(
        {
            "authorization": "Bearer literal-secret",
            "set-cookie": "sensitive-cookie-value",
            "items": [
                {"api-key": "literal-secret"},
                "prefix literal-secret suffix",
            ],
            "provider_context": {
                "reasoning_content": "private chain of thought",
            },
        }
    )
    payload = json.loads(prepared.value)

    assert "literal-secret" not in prepared.value
    assert "private chain of thought" in prepared.value
    assert payload["authorization"] == "Bearer ***REDACTED***"
    assert payload["set-cookie"] == "sensitive-cookie-value"
    assert payload["items"][0]["api-key"] == "***REDACTED***"
    assert payload["provider_context"]["reasoning_content"] == "private chain of thought"
    assert prepared.truncated is False


def test_trace_content_encoder_formats_normalized_json_for_people() -> None:
    """Scenario: verify that trace content encoder formats normalized json for people."""
    from pydantic import BaseModel

    from restscope.observability.content import TraceContentEncoder
    from restscope.redaction import Redactor

    @dataclass(frozen=True)
    class DataclassValue:
        label: str

    class ModelValue(BaseModel):
        enabled: bool

    encoder = TraceContentEncoder(redactor=Redactor(), max_content_bytes=4096)
    prepared = encoder.prepare(
        {
            "unicode": "中文",
            "dataclass": DataclassValue(label="visible"),
            "model": ModelValue(enabled=True),
            "bytes": b"text",
        }
    )

    assert prepared.value.startswith("{\n  ")
    assert '"unicode": "中文"' in prepared.value
    assert json.loads(prepared.value) == {
        "unicode": "中文",
        "dataclass": {"label": "visible"},
        "model": {"enabled": True},
        "bytes": "text",
    }
    assert prepared.truncated is False


def test_trace_content_encoder_bounds_serialized_content_and_records_original_size() -> None:
    """Scenario: verify that trace content encoder bounds serialized content and records original size."""
    from restscope.observability.content import TraceContentEncoder
    from restscope.redaction import Redactor

    encoder = TraceContentEncoder(redactor=Redactor(), max_content_bytes=256)
    prepared = encoder.prepare({"content": "x" * 4096})

    assert len(prepared.value.encode("utf-8")) <= 256
    assert prepared.original_size_bytes > 4096
    assert prepared.truncated is True
    payload = json.loads(prepared.value)
    assert payload["truncated"] is True
    assert isinstance(payload["preview"], dict)
    assert payload["preview"]["content"].startswith("x")


def test_disabled_tracing_runtime_is_a_safe_noop() -> None:
    """Scenario: verify that disabled tracing runtime is a safe noop."""
    from restscope.observability import build_tracing_runtime
    from restscope.redaction import Redactor
    from restscope.restscope_config import TracingConfig

    redactor = Redactor(["not-visible"])
    runtime = build_tracing_runtime(
        TracingConfig(enabled=False),
        redactor=redactor,
    )

    with runtime.span(
        "disabled",
        kind="CHAIN",
        input_value={"token": "not-visible"},
    ) as span:
        span.set_output({"ok": True})
        span.set_attribute("restscope.status", "passed")

    runtime.redactor.register_secrets(["another-secret"])
    runtime.close()
    runtime.close()

    assert runtime.enabled is False
    assert "not-visible" not in repr(runtime)
    assert "another-secret" not in repr(runtime)


def test_enabled_runtime_without_tracing_extra_falls_back_to_noop(
    monkeypatch,
    caplog,
) -> None:
    """Scenario: verify that enabled runtime without tracing extra falls back to noop."""
    from restscope.observability import build_tracing_runtime
    from restscope.restscope_config import TracingConfig

    monkeypatch.setitem(sys.modules, "restscope.observability.phoenix", None)

    runtime = build_tracing_runtime(TracingConfig(enabled=True))

    assert runtime.enabled is False
    assert "continuing without tracing" in caplog.text


def test_span_backend_failure_is_sanitized_and_does_not_change_business_result(
    caplog,
) -> None:
    """Scenario: verify that span backend failure is sanitized and does not change business result."""
    from restscope.observability.runtime import TracingRuntime
    from restscope.redaction import Redactor

    class FailingBackend:
        def start_as_current_span(self, name):
            del name
            raise RuntimeError("backend-secret")

        def close(self):
            return None

    runtime = TracingRuntime(
        redactor=Redactor(["backend-secret"]),
        backend=FailingBackend(),
    )

    with runtime.span("business", kind="CHAIN"):
        result = "unchanged"

    assert result == "unchanged"
    assert "backend-secret" not in caplog.text
    assert "***REDACTED***" in caplog.text


def test_enabled_runtime_emits_nested_sanitized_openinference_spans() -> None:
    """Scenario: verify that enabled runtime emits nested sanitized openinference spans."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from restscope.observability.otel_backend import OpenTelemetryBackend
    from restscope.observability.runtime import TracingRuntime
    from restscope.redaction import Redactor

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = TracingRuntime(
        redactor=Redactor(["span-secret"]),
        max_content_bytes=4096,
        backend=OpenTelemetryBackend(
            tracer_provider=provider,
            flush_timeout_seconds=1,
        ),
    )

    with runtime.span(
        "RESTScopeApp.start",
        kind="CHAIN",
        input_value={"authorization": "span-secret"},
    ) as root:
        root.set_attribute("restscope.task_id", "task-1")
        with runtime.span(
            "LLMClient.invoke",
            kind="LLM",
            input_value={"messages": ["span-secret"]},
        ) as child:
            child.set_output({"content": "safe"})
    runtime.close()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    root_span = spans["RESTScopeApp.start"]
    child_span = spans["LLMClient.invoke"]

    assert child_span.parent.span_id == root_span.context.span_id
    assert root_span.attributes["openinference.span.kind"] == "CHAIN"
    assert child_span.attributes["openinference.span.kind"] == "LLM"
    assert "span-secret" not in root_span.attributes["input.value"]
    assert "span-secret" not in child_span.attributes["input.value"]
    assert json.loads(child_span.attributes["output.value"])["content"] == "safe"
    assert root_span.attributes["restscope.task_id"] == "task-1"


def test_runtime_adds_openinference_names_for_agent_and_tool_spans() -> None:
    """Scenario: verify that runtime adds openinference names for agent and tool spans."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from restscope.observability.otel_backend import OpenTelemetryBackend
    from restscope.observability.runtime import TracingRuntime

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = TracingRuntime(
        backend=OpenTelemetryBackend(
            tracer_provider=provider,
            flush_timeout_seconds=1,
        ),
    )

    with runtime.span("ExampleAgent.run", kind="AGENT"):
        pass
    with runtime.span("restscope.example.tool", kind="TOOL"):
        pass
    runtime.close()

    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert spans["ExampleAgent.run"].attributes["agent.name"] == "ExampleAgent.run"
    assert (
        spans["restscope.example.tool"].attributes["tool.name"]
        == "restscope.example.tool"
    )


def test_llm_message_projection_preserves_roles_when_content_is_truncated() -> None:
    """Scenario: verify that llm message projection preserves roles when content is truncated."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from restscope.observability.otel_backend import OpenTelemetryBackend
    from restscope.observability.runtime import TracingRuntime
    from restscope.redaction import Redactor

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = TracingRuntime(
        redactor=Redactor(["message-secret"]),
        max_content_bytes=256,
        backend=OpenTelemetryBackend(
            tracer_provider=provider,
            flush_timeout_seconds=1,
        ),
    )

    with runtime.span("LLMClient.invoke", kind="LLM") as span:
        span.set_llm_input_messages(
            [
                {"role": "system", "content": "s" * 1024},
                {
                    "role": "user",
                    "content": f"message-secret{'u' * 1024}",
                },
            ]
        )
    runtime.close()

    recorded = exporter.get_finished_spans()[0]
    attributes = recorded.attributes
    assert json.loads(attributes["input.value"]) == {
        "message_count": 2,
        "roles": ["system", "user"],
    }
    assert attributes["llm.input_messages.0.message.role"] == "system"
    assert attributes["llm.input_messages.1.message.role"] == "user"
    assert attributes["restscope.input.truncated"] is True
    assert attributes["restscope.input.original_size_bytes"] > 2048
    rendered = json.dumps(dict(attributes), ensure_ascii=False)
    assert "message-secret" not in rendered
    assert len(
        attributes["llm.input_messages.0.message.content"].encode("utf-8")
    ) < 1024
    assert len(
        attributes["llm.input_messages.1.message.content"].encode("utf-8")
    ) < 1024


def test_llm_message_projection_failure_does_not_change_business_result() -> None:
    """Scenario: verify that llm message projection failure does not change business result."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from restscope.observability.otel_backend import OpenTelemetryBackend
    from restscope.observability.runtime import TracingRuntime

    class BrokenMessages:
        def model_dump(self, *, mode):
            del mode
            raise ValueError("cannot encode messages")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = TracingRuntime(
        backend=OpenTelemetryBackend(
            tracer_provider=provider,
            flush_timeout_seconds=1,
        ),
    )

    with runtime.span("LLMClient.invoke", kind="LLM") as span:
        span.set_llm_input_messages(BrokenMessages())
        result = "unchanged"
    runtime.close()

    assert result == "unchanged"
    assert exporter.get_finished_spans()[0].status.status_code.name == "OK"


def test_runtime_records_sanitized_error_without_leaking_exception_message() -> None:
    """Scenario: verify that runtime records sanitized error without leaking exception message."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace.status import StatusCode

    from restscope.observability.otel_backend import OpenTelemetryBackend
    from restscope.observability.runtime import TracingRuntime
    from restscope.redaction import Redactor

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    runtime = TracingRuntime(
        redactor=Redactor(["error-secret"]),
        backend=OpenTelemetryBackend(
            tracer_provider=provider,
            flush_timeout_seconds=1,
        ),
    )

    with pytest.raises(ValueError, match="error-secret"):
        with runtime.span("failing", kind="TOOL"):
            raise ValueError("failure contains error-secret")
    runtime.close()

    span = exporter.get_finished_spans()[0]
    rendered = json.dumps(
        {
            "status": span.status.description,
            "events": [dict(event.attributes) for event in span.events],
        },
        default=str,
    )

    assert span.status.status_code is StatusCode.ERROR
    assert "error-secret" not in rendered
    assert "***REDACTED***" in rendered


def test_phoenix_runtime_disables_all_automatic_instrumentation(monkeypatch) -> None:
    """Scenario: verify that phoenix runtime disables all automatic instrumentation."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider

    import restscope.observability.phoenix as phoenix_module
    from restscope.observability import build_tracing_runtime
    from restscope.redaction import Redactor
    from restscope.restscope_config import TracingConfig

    register_calls: list[dict] = []
    monkeypatch.setattr(
        phoenix_module,
        "register",
        lambda **kwargs: register_calls.append(kwargs) or TracerProvider(),
    )

    runtime = build_tracing_runtime(
        TracingConfig(
            enabled=True,
            collector_endpoint="http://phoenix.test:6006",
            project_name="restscope-test",
            api_key="phoenix-key",
            protocol="http/protobuf",
            batch=True,
            flush_timeout_seconds=1,
        )
    )
    runtime.close()

    assert runtime.enabled is False
    assert register_calls == [
        {
            "endpoint": "http://phoenix.test:6006/v1/traces",
            "project_name": "restscope-test",
            "api_key": "phoenix-key",
            "protocol": "http/protobuf",
            "batch": True,
            "set_global_tracer_provider": False,
            "auto_instrument": False,
            "verbose": False,
        }
    ]


def test_openai_sdk_call_does_not_create_automatic_child_span(monkeypatch) -> None:
    """Scenario: verify that openai sdk call does not create automatic child span."""
    pytest.importorskip("opentelemetry.sdk")
    import httpx
    from openai import OpenAI
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import restscope.observability.phoenix as phoenix_module
    from restscope.observability import build_tracing_runtime
    from restscope.redaction import Redactor
    from restscope.restscope_config import TracingConfig

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(phoenix_module, "register", lambda **kwargs: provider)

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "sdk-output-secret",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    runtime = build_tracing_runtime(
        TracingConfig(
            enabled=True,
            collector_endpoint="http://phoenix.test:6006",
            flush_timeout_seconds=1,
        ),
        redactor=Redactor(
            [
                "sdk-input-secret",
                "sdk-output-secret",
                "sdk-tool-secret",
            ]
        ),
    )
    client = OpenAI(
        api_key="test-key",
        base_url="http://openai.test",
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    try:
        with runtime.span("LLMClient.invoke", kind="LLM"):
            response = client.chat.completions.create(
                model="test-model",
                messages=[{"role": "user", "content": "sdk-input-secret"}],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "secret_tool",
                            "description": "sdk-tool-secret",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            )
            assert response.choices[0].message.content == "sdk-output-secret"
    finally:
        client.close()
        runtime.close()

    spans = list(exporter.get_finished_spans())
    rendered = json.dumps(
        [
            {
                "name": span.name,
                "attributes": dict(span.attributes),
                "events": [dict(event.attributes) for event in span.events],
            }
            for span in spans
        ],
        default=str,
    )

    assert [span.name for span in spans] == ["LLMClient.invoke"]
    assert all(
        secret not in rendered
        for secret in (
            "sdk-input-secret",
            "sdk-output-secret",
            "sdk-tool-secret",
        )
    )


def test_phoenix_runtime_reuses_matching_process_registration(monkeypatch) -> None:
    """Scenario: verify that phoenix runtime reuses matching process registration."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider

    import restscope.observability.phoenix as phoenix_module
    from restscope.observability import build_tracing_runtime
    from restscope.restscope_config import TracingConfig

    register_calls: list[dict] = []
    shutdown_calls: list[str] = []
    provider = TracerProvider()
    monkeypatch.setattr(
        provider,
        "force_flush",
        lambda **kwargs: shutdown_calls.append("flush") or True,
    )
    monkeypatch.setattr(
        provider,
        "shutdown",
        lambda: shutdown_calls.append("shutdown"),
    )

    monkeypatch.setattr(
        phoenix_module,
        "register",
        lambda **kwargs: register_calls.append(kwargs) or provider,
    )
    config = TracingConfig(
        enabled=True,
        collector_endpoint="http://phoenix.test:6006",
        project_name="shared",
        flush_timeout_seconds=1,
    )

    first = build_tracing_runtime(config)
    second = build_tracing_runtime(config)
    first.close()

    assert second.enabled is True
    assert len(register_calls) == 1
    assert shutdown_calls == []

    second.close()

    assert shutdown_calls == ["flush", "shutdown"]


def test_phoenix_runtime_rejects_conflicting_process_configuration_fail_open(
    monkeypatch,
    caplog,
) -> None:
    """Scenario: verify that phoenix runtime rejects conflicting process configuration fail open."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider

    import restscope.observability.phoenix as phoenix_module
    from restscope.observability import build_tracing_runtime
    from restscope.restscope_config import TracingConfig

    monkeypatch.setattr(phoenix_module, "register", lambda **kwargs: TracerProvider())
    active = build_tracing_runtime(
        TracingConfig(
            enabled=True,
            collector_endpoint="http://phoenix.test:6006",
            project_name="first",
            api_key="conflict-secret",
            flush_timeout_seconds=1,
        )
    )
    try:
        conflicting = build_tracing_runtime(
            TracingConfig(
                enabled=True,
                collector_endpoint="http://other-phoenix.test:6006",
                project_name="second",
                api_key="conflict-secret",
                flush_timeout_seconds=1,
            )
        )
    finally:
        active.close()

    assert conflicting.enabled is False
    assert "different configuration" in caplog.text
    assert "conflict-secret" not in caplog.text


def test_runtime_shutdown_timeout_is_fail_open(caplog) -> None:
    """Scenario: verify that runtime shutdown timeout is fail open."""
    from restscope.observability.otel_backend import OpenTelemetryBackend
    from restscope.observability.runtime import TracingRuntime
    from restscope.redaction import Redactor

    class BlockingProvider:
        def get_tracer(self, name):
            del name
            return object()

        def force_flush(self, *, timeout_millis):
            del timeout_millis

        def shutdown(self):
            time.sleep(1)

    runtime = TracingRuntime(
        redactor=Redactor(),
        backend=OpenTelemetryBackend(
            tracer_provider=BlockingProvider(),
            flush_timeout_seconds=0.01,
        ),
    )

    started = time.monotonic()
    runtime.close()
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert "Tracing shutdown failed" in caplog.text


def test_local_phoenix_runtime_temporarily_bypasses_process_proxy(monkeypatch) -> None:
    """Scenario: verify that local phoenix runtime temporarily bypasses process proxy."""
    pytest.importorskip("opentelemetry.sdk")
    from opentelemetry.sdk.trace import TracerProvider

    import restscope.observability.phoenix as phoenix_module
    from restscope.observability import build_tracing_runtime
    from restscope.restscope_config import TracingConfig

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.test:8080")
    monkeypatch.setenv("no_proxy", "existing.test")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setattr(
        phoenix_module,
        "register",
        lambda **kwargs: TracerProvider(),
    )

    runtime = build_tracing_runtime(
        TracingConfig(
            enabled=True,
            collector_endpoint="http://localhost:6006",
            flush_timeout_seconds=1,
        )
    )

    assert {"existing.test", "localhost", "127.0.0.1"}.issubset(
        set(os.environ["no_proxy"].split(","))
    )
    assert {"localhost", "127.0.0.1"}.issubset(
        set(os.environ["NO_PROXY"].split(","))
    )

    runtime.close()

    assert os.environ["no_proxy"] == "existing.test"
    assert "NO_PROXY" not in os.environ
