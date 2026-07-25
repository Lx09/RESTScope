"""Capabilities for configuring and running lightweight OpenAPI tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from restscope.capabilities.tool_context import ToolContext
from restscope.capabilities.tool_registry import ToolRegistry
from restscope.llm.schemas import ToolSpec
from restscope.testing.models import (
    InputGeneratorConfig,
    InputGeneratorPatch,
    OperationExecutionReport,
    OperationGeneratorConfig,
)

if TYPE_CHECKING:
    from restscope.testing.catalog import GeneratorConfigCatalog
    from restscope.testing.execution import OperationTestingService


INSPECT_INPUTS_TOOL_NAME = "restscope.testing.inspect_operation_inputs"
REPLACE_GENERATORS_TOOL_NAME = "restscope.testing.replace_operation_generators"
PATCH_GENERATORS_TOOL_NAME = "restscope.testing.patch_operation_generators"
RUN_OPERATION_TOOL_NAME = "restscope.testing.run_operation"

CONFIGURATION_TOOL_NAMES = frozenset(
    {
        INSPECT_INPUTS_TOOL_NAME,
        REPLACE_GENERATORS_TOOL_NAME,
        PATCH_GENERATORS_TOOL_NAME,
    }
)


class TestingToolError(RuntimeError):
    """Stable capability error suitable for a redacted ToolResult."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InspectOperationInputsArguments(_Arguments):
    operation_key: str


class ReplaceOperationGeneratorsArguments(_Arguments):
    operation_key: str
    expected_revision: int = Field(ge=1)
    active_media_type: str | None = None
    configs: list[InputGeneratorConfig]


class PatchOperationGeneratorsArguments(_Arguments):
    operation_key: str
    expected_revision: int = Field(ge=1)
    updates: list[InputGeneratorPatch] = Field(min_length=1)


class RunOperationArguments(_Arguments):
    operation_key: str
    case_count: int = Field(default=1, ge=1, le=20)
    seed: int | None = None


def register_testing_tools(
    registry: ToolRegistry,
    *,
    generator_config_catalog: GeneratorConfigCatalog,
    operation_testing_service: OperationTestingService,
) -> tuple[ToolSpec, ...]:
    """Register configuration management and target execution capabilities."""

    handlers = _TestingToolHandlers(
        generator_config_catalog=generator_config_catalog,
        operation_testing_service=operation_testing_service,
    )
    specs = (
        _configuration_spec(
            name=INSPECT_INPUTS_TOOL_NAME,
            description=(
                "Inspect one operation's frozen request snapshot and complete persisted "
                "generator configuration."
            ),
            arguments=InspectOperationInputsArguments,
            output=OperationGeneratorConfig,
        ),
        _configuration_spec(
            name=REPLACE_GENERATORS_TOOL_NAME,
            description=(
                "Atomically replace every generator for one frozen operation snapshot."
            ),
            arguments=ReplaceOperationGeneratorsArguments,
            output=OperationGeneratorConfig,
            read_only=False,
        ),
        _configuration_spec(
            name=PATCH_GENERATORS_TOOL_NAME,
            description=(
                "Atomically patch selected generators for one frozen operation snapshot."
            ),
            arguments=PatchOperationGeneratorsArguments,
            output=OperationGeneratorConfig,
            read_only=False,
        ),
        ToolSpec(
            name=RUN_OPERATION_TOOL_NAME,
            description=(
                "Generate and serially execute up to 20 test cases for an operation from "
                "its persisted request snapshot and generator configuration."
            ),
            kind="local_function",
            input_schema=RunOperationArguments.model_json_schema(),
            output_schema=OperationExecutionReport.model_json_schema(),
            risk_level="high",
            read_only=False,
            requires_approval=False,
            timeout_seconds=30,
            metadata={"target_bound": True, "generated_requests_only": True},
        ),
    )
    methods = (
        handlers.inspect_operation_inputs,
        handlers.replace_operation_generators,
        handlers.patch_operation_generators,
        handlers.run_operation,
    )
    for spec, handler in zip(specs, methods, strict=True):
        registry.register(spec=spec, handler=handler)
    return specs


def _configuration_spec(
    *,
    name: str,
    description: str,
    arguments: type[BaseModel],
    output: type[BaseModel] | None,
    read_only: bool = True,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description,
        kind="local_function",
        input_schema=arguments.model_json_schema(),
        output_schema=output.model_json_schema() if output is not None else {"type": "object"},
        risk_level="low" if read_only else "medium",
        read_only=read_only,
        requires_approval=False,
        metadata={
            "configuration_management": True,
        },
    )


class _TestingToolHandlers:
    def __init__(
        self,
        *,
        generator_config_catalog: GeneratorConfigCatalog,
        operation_testing_service: OperationTestingService,
    ) -> None:
        self.catalog = generator_config_catalog
        self.service = operation_testing_service

    def inspect_operation_inputs(self, context: ToolContext, /, **arguments: Any) -> dict[str, Any]:
        del context
        request = _validate(InspectOperationInputsArguments, arguments)
        config = self.catalog.inspect_operation(request.operation_key)
        return _result(
            (
                f"Inspected {len(config.snapshot.input_nodes)} frozen input nodes "
                f"for {config.operation_key}"
            ),
            config,
        )

    def replace_operation_generators(
        self,
        context: ToolContext,
        /,
        **arguments: Any,
    ) -> dict[str, Any]:
        del context
        request = _validate(ReplaceOperationGeneratorsArguments, arguments)
        config = self.catalog.replace_operation(
            operation_key=request.operation_key,
            expected_revision=request.expected_revision,
            active_media_type=request.active_media_type,
            configs=request.configs,
        )
        return {
            "content": (
                f"Stored generator configuration revision {config.revision} "
                f"for {config.operation_key}"
            ),
            "structured": config.model_dump(mode="json"),
        }

    def patch_operation_generators(
        self,
        context: ToolContext,
        /,
        **arguments: Any,
    ) -> dict[str, Any]:
        del context
        request = _validate(PatchOperationGeneratorsArguments, arguments)
        config = self.catalog.patch_operation(
            operation_key=request.operation_key,
            expected_revision=request.expected_revision,
            updates=request.updates,
        )
        return _result(
            f"Stored generator configuration revision {config.revision} for {config.operation_key}",
            config,
        )

    def run_operation(self, context: ToolContext, /, **arguments: Any) -> dict[str, Any]:
        request = _validate(RunOperationArguments, arguments)
        report = self.service.run_operation(context, **request.model_dump())
        return _result(
            f"{report.status}: executed {len(report.cases)} cases for {report.operation_key}",
            report,
        )


def _validate(model: type[_Arguments], arguments: Mapping[str, Any]):
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        issue = exc.errors(include_input=False)[0]
        location = ".".join(str(item) for item in issue.get("loc", ())) or "request"
        raise TestingToolError(
            "invalid_testing_request",
            f"Invalid testing tool field {location}: {issue['msg']}",
        ) from exc


def _result(content: str, model: BaseModel) -> dict[str, Any]:
    return {
        "content": content,
        "structured": model.model_dump(mode="json"),
    }
