"""Own the exact tools that one Agent may offer and execute.

An Agent or workflow creates one :class:`AgentToolbox`, registers only the
tools that belong to that Agent, and later asks the same object for model
specifications and execution. Tool implementations bind their own dependencies
before registration, so this Module never injects target credentials, OpenAPI
state, or other unrelated runtime objects into a call.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from jsonschema import SchemaError, ValidationError, validate
from jsonschema.validators import validator_for

from restscope.llm import (
    ProviderUnavailableError,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from restscope.observability import TracingRuntime

if TYPE_CHECKING:
    from restscope.tools.catalog import ToolCatalog


ToolImplementation = Callable[..., object]


@dataclass(frozen=True)
class ToolBinding:
    """Bind one Catalog name to its session-specific implementation.

    A Binding carries no Schema. The global Catalog remains the only authority
    for the model contract, while the Harness injects just the stateful code
    needed by this Agent session.
    """

    name: str
    execute: ToolImplementation


class ToolFailure(RuntimeError):
    """Carry one expected, model-safe tool rejection out of an implementation.

    Args:
        code: Stable machine-readable reason used by Agent feedback and tests.
        message: Safe explanation that may be shown directly to the model.
        content: Optional bounded tool content when the workflow needs more
            structured guidance than the error envelope alone.
        status: Whether the expected failure is ordinary or a timeout.

    Unexpected programming exceptions must not use this type; the toolbox
    records those internally and returns ``internal_tool_error`` instead.
    """

    def __init__(
        self,
        *,
        code: str,
        message: str,
        content: str | None = None,
        status: Literal["failed", "timed_out"] = "failed",
    ) -> None:
        """Store an already-sanitized expected failure for ToolResult creation."""
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.content = content
        self.status = status


class AgentToolbox:
    """Keep one Agent's tool specifications and implementations together.

    A toolbox starts empty. Registration is append-only: a duplicate name is a
    construction error rather than an implicit permission or implementation
    change. The same object validates schemas, executes calls, and returns
    model-safe results, so permission and implementation cannot drift apart.
    """

    def __init__(
        self,
        *,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Create an empty toolbox using the App's tracing and redaction seam."""
        self._tools: dict[str, tuple[ToolSpec, ToolImplementation]] = {}
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    @classmethod
    def from_catalog(
        cls,
        *,
        catalog: "ToolCatalog",
        selected_names: tuple[str, ...],
        bindings: list[ToolBinding],
        tracing_runtime: TracingRuntime | None = None,
    ) -> "AgentToolbox":
        """Build one Agent's toolbox from exact Catalog grants and Bindings.

        The selected names and Binding names must be identical. This prevents a
        workflow from silently adding an implementation that the Agent Profile
        did not grant or omitting a Tool while still advertising its contract.
        """
        binding_by_name: dict[str, ToolBinding] = {}
        for binding in bindings:
            if binding.name in binding_by_name:
                raise ValueError(f"Tool Binding is duplicated: {binding.name}")
            binding_by_name[binding.name] = binding
        if set(selected_names) != set(binding_by_name):
            raise ValueError("Selected Tool names and Tool Bindings do not match")

        toolbox = cls(tracing_runtime=tracing_runtime)
        for definition in catalog.select(selected_names):
            binding = binding_by_name[definition.name]
            toolbox.register(spec=definition.spec, execute=binding.execute)
        return toolbox

    def register(
        self,
        *,
        spec: ToolSpec,
        execute: ToolImplementation,
    ) -> None:
        """Register one complete tool without allowing silent replacement.

        Args:
            spec: The name and JSON contracts shown to the model.
            execute: Code that receives validated model arguments and returns
                the tool's model-facing result.

        Raises:
            TypeError: If ``execute`` is not callable.
            ValueError: If this toolbox already contains ``spec.name``.
        """
        if not callable(execute):
            raise TypeError(f"Tool implementation must be executable: {spec.name}")
        if spec.kind == "local_function" and spec.output_schema is None:
            raise ValueError(
                f"RESTScope tool requires an output schema: {spec.name}"
            )
        self._check_schema(spec.input_schema, contract="input", tool_name=spec.name)
        if spec.output_schema is not None:
            self._check_schema(
                spec.output_schema,
                contract="output",
                tool_name=spec.name,
            )
        if spec.name in self._tools:
            raise ValueError(f"Tool is already registered: {spec.name}")
        self._tools[spec.name] = (spec, execute)

    @staticmethod
    def _check_schema(
        schema: dict[str, object],
        *,
        contract: str,
        tool_name: str,
    ) -> None:
        """Reject an invalid JSON Schema while the toolbox is constructed."""
        try:
            validator_for(schema).check_schema(schema)
        except SchemaError as exc:
            raise ValueError(
                f"Tool has an invalid {contract} schema: {tool_name}"
            ) from exc

    def specs(self) -> list[ToolSpec]:
        """Return the model descriptions in deterministic registration order."""
        return [spec for spec, _implementation in self._tools.values()]

    def execute(self, tool_call: ToolCall) -> ToolResult:
        """Validate and execute one call from this toolbox.

        Args:
            tool_call: The model-selected tool name and untrusted arguments.

        Returns:
            A result that either denies invalid input before the implementation
            runs or contains the implementation's structured output.

        Raises:
            ProviderUnavailableError: A nested LLM-backed tool exhausted its
                provider retry policy, so the owning workflow must stop.
        """
        validation_failure = self._validation_failure(tool_call)
        if validation_failure is not None:
            return validation_failure
        return self._execute_validated(tool_call)

    def execute_many(self, tool_calls: list[ToolCall]) -> list[ToolResult]:
        """Execute independent calls concurrently and retain call order.

        Args:
            tool_calls: A workflow-approved group whose calls do not depend on
                one another and whose implementations do not mutate shared
                Agent session state while they run.

        Returns:
            One result per call in the original model-provided order. An empty
            input returns immediately without creating worker threads.

        Raises:
            ProviderUnavailableError: A nested LLM-backed tool exhausted its
                provider retry policy. No result group is returned.
        """
        if not tool_calls:
            return []

        validation_failures = [
            self._validation_failure(tool_call)
            for tool_call in tool_calls
        ]
        if any(failure is not None for failure in validation_failures):
            return [
                failure
                if failure is not None
                else ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    status="denied",
                    error={
                        "code": "tool_batch_rejected",
                        "message": (
                            "No tools ran because another call in the batch was invalid."
                        ),
                    },
                )
                for tool_call, failure in zip(
                    tool_calls,
                    validation_failures,
                    strict=True,
                )
            ]

        # Worker threads do not inherit Python Context variables automatically.
        # OpenTelemetry keeps the current parent span in that Context, so every
        # task needs its own copy to remain inside the calling Agent's trace.
        contexts = [copy_context() for _tool_call in tool_calls]
        with ThreadPoolExecutor(max_workers=len(tool_calls)) as pool:
            futures = [
                pool.submit(
                    context.run,
                    self._execute_validated,
                    tool_call,
                )
                for context, tool_call in zip(
                    contexts,
                    tool_calls,
                    strict=True,
                )
            ]
            # Reading submitted Futures in call order preserves the provider's
            # tool-call protocol even when later calls finish first.
            return [future.result() for future in futures]

    def _validation_failure(self, tool_call: ToolCall) -> ToolResult | None:
        """Return a denial before execution, or ``None`` for a valid call."""
        try:
            spec, _implementation = self._tools[tool_call.name]
        except KeyError:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="denied",
                error={
                    "code": "unknown_tool",
                    "message": "The requested tool is not registered for this Agent.",
                },
            )

        # Model-generated arguments remain untrusted even when a provider says
        # it enforced the same schema before returning the tool call.
        try:
            validate(instance=tool_call.arguments, schema=spec.input_schema)
        except ValidationError:
            return ToolResult(
                tool_call_id=tool_call.id,
                name=tool_call.name,
                status="denied",
                error={
                    "code": "invalid_tool_arguments",
                    "message": "Tool arguments do not match the declared input schema.",
                },
            )
        return None

    def _execute_validated(self, tool_call: ToolCall) -> ToolResult:
        """Trace and execute one call without hiding a shared provider outage.

        Ordinary tool failures return a model-safe :class:`ToolResult`.
        ``ProviderUnavailableError`` is raised only after the Tool span closes
        normally, so tracing cannot follow its private SDK cause into a raw
        provider response.
        """
        provider_error: ProviderUnavailableError | None = None
        redacted: ToolResult | None = None
        with self.tracing_runtime.span(
            tool_call.name,
            kind="TOOL",
            input_value={"arguments": tool_call.arguments},
            attributes={"tool.name": tool_call.name},
        ) as span:
            try:
                result = self._invoke_validated(tool_call)
            except ProviderUnavailableError as exc:
                # This is run-control state, not feedback the Agent can repair.
                # Record only the same stable fields as the owning LLM span.
                span.set_attribute("restscope.llm.error_code", exc.code)
                if exc.status_code is not None:
                    span.set_attribute(
                        "http.response.status_code",
                        exc.status_code,
                    )
                span.set_attribute(
                    "restscope.llm.provider_retry_limit",
                    exc.retry_limit,
                )
                span.mark_error(str(exc))
                provider_error = exc
            except ToolFailure as exc:
                result = ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    status=exc.status,
                    content=exc.content,
                    error={"code": exc.code, "message": exc.safe_message},
                )
            except Exception as exc:
                # The trace retains a redacted diagnostic event. The exception
                # itself never crosses the model-visible ToolResult boundary.
                span.record_error(exc)
                result = ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    status="failed",
                    error={
                        "code": "internal_tool_error",
                        "message": "The tool failed because of an internal error.",
                    },
                )
            if provider_error is None:
                redacted = ToolResult.model_validate(
                    self.tracing_runtime.redactor.redact(result)
                )
                span.set_output(redacted)
                span.set_attribute("restscope.tool.status", redacted.status)
                if redacted.status in {"failed", "timed_out"}:
                    message = (redacted.error or {}).get(
                        "message",
                        redacted.status,
                    )
                    span.mark_error(str(message))

        if provider_error is not None:
            raise provider_error
        assert redacted is not None
        return redacted

    def _invoke_validated(self, tool_call: ToolCall) -> ToolResult:
        """Call one implementation and enforce its successful output schema."""
        spec, implementation = self._tools[tool_call.name]
        output = implementation(**tool_call.arguments)
        payload = output if isinstance(output, dict) else {"content": str(output)}
        structured = payload.get("structured")

        # Only successful structured output is checked. Expected tool failures
        # use the stable ToolResult error contract instead of a success schema.
        if spec.output_schema is not None:
            try:
                validate(instance=structured, schema=spec.output_schema)
            except ValidationError:
                return ToolResult(
                    tool_call_id=tool_call.id,
                    name=tool_call.name,
                    status="failed",
                    error={
                        "code": "invalid_tool_output",
                        "message": (
                            "Tool output does not match the declared output schema."
                        ),
                    },
                )
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="succeeded",
            content=payload.get("content"),
            structured=structured,
            artifact_ids=payload.get("artifact_ids", []),
        )
