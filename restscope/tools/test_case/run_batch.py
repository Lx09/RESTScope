"""Expose generic bounded Batch generation and target execution as one Tool.

The Backend serializes calls so one Agent cannot interleave two mutating
Batches. ``OperationTestingService`` freezes the Generation Store revision and
prepares every case before the first request. This Tool then projects inline
requests and bounded outcomes without creating ``TC*``/``E*`` identities or
persisting Batch state.
"""

from __future__ import annotations

import json
from threading import RLock
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from restscope.llm import ToolSpec
from restscope.request_generation.store import GeneratorConfigError
from restscope.tools.context import ToolContext
from restscope.tools.runtime import ToolBinding, ToolFailure


TEST_CASE_RUN_BATCH_TOOL_NAME = "test_case.run_batch"
_MAX_FAILURE_MESSAGE_CHARACTERS = 1_200
_MAX_OUTPUT_CHARACTERS = 24_000


class RunBatchInput(BaseModel):
    """Select one operation and a small deterministic case count."""

    model_config = ConfigDict(extra="forbid")

    operation_key: str = Field(min_length=1, max_length=1_000)
    case_count: int = Field(default=1, ge=1, le=5)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)


class BatchCaseView(BaseModel):
    """Return one canonical request and either HTTP or transport facts."""

    model_config = ConfigDict(extra="forbid")

    case_number: int = Field(ge=1, le=5)
    request: dict[str, object] = Field(
        description="Complete bounded canonical method, URL, headers, and body for this case."
    )
    outcome_kind: Literal["http", "transport"]
    status_code: int | None = Field(default=None, ge=100, le=599)
    reason_phrase: str | None = None
    failure_messages: list[str]
    transport_code: str | None = None


class RunBatchOutput(BaseModel):
    """Validate the complete bounded Batch evidence returned to an Agent."""

    model_config = ConfigDict(extra="forbid")

    operation_key: str
    method: str
    path: str
    mutating_operation: bool
    generation_revision: int = Field(ge=0)
    generation_state_digest: str
    seed: int = Field(ge=0)
    case_count: int = Field(ge=1, le=5)
    success_count: int = Field(ge=0, le=5)
    failure_count: int = Field(ge=0, le=5)
    cases: list[BatchCaseView] = Field(min_length=1, max_length=5)


class BatchExecutionBackend(Protocol):
    """Describe the narrow Harness service consumed by the Batch Tool."""

    config_store: object

    def run_batch(
        self,
        context: ToolContext,
        *,
        operation_key: str,
        case_count: int,
        seed: int | None,
    ) -> object: ...


class TestCaseBatchToolBackend:
    """Bind current target context and operation execution behind one lock."""

    def __init__(
        self,
        *,
        service: BatchExecutionBackend,
        context_provider,
    ) -> None:
        self.service = service
        self.context_provider = context_provider
        self._lock = RLock()

    def run_batch(self, **arguments: object) -> dict[str, object]:
        """Execute one complete preflighted Batch and return inline evidence."""
        try:
            request = RunBatchInput.model_validate(arguments)
            context: ToolContext = self.context_provider()
            with self._lock:
                result = self.service.run_batch(
                    context,
                    operation_key=request.operation_key,
                    case_count=request.case_count,
                    seed=request.seed,
                )
            operation = self.service.config_store.require_state(
                request.operation_key
            ).config.snapshot
            cases = [_case_view(item) for item in result.cases]
            output = RunBatchOutput(
                operation_key=result.operation_key,
                method=operation.method,
                path=operation.path,
                mutating_operation=operation.method.upper() not in {"GET", "HEAD", "OPTIONS"},
                generation_revision=result.generation_revision,
                generation_state_digest=result.generation_state_digest,
                seed=result.seed,
                case_count=len(cases),
                success_count=result.success_count,
                failure_count=len(cases) - result.success_count,
                cases=cases,
            )
            payload = output.model_dump(mode="json")
            if len(json.dumps(payload, separators=(",", ":"))) > _MAX_OUTPUT_CHARACTERS:
                raise ToolFailure(
                    code="test_batch_output_too_large",
                    message="Complete Batch evidence exceeds 24000 characters",
                )
        except ValidationError as exc:
            raise ToolFailure(
                code="invalid_test_batch",
                message="Batch arguments do not match the Tool contract",
            ) from exc
        except (RuntimeError, GeneratorConfigError) as exc:
            raise ToolFailure(
                code=getattr(exc, "code", "test_batch_failed"),
                message=str(exc),
            ) from exc
        return {"structured": payload}


def _case_view(case) -> BatchCaseView:
    """Bound failure text while keeping the generated request complete."""
    failure = case.failure
    messages = list(failure.messages) if failure is not None else []
    remaining = _MAX_FAILURE_MESSAGE_CHARACTERS
    bounded: list[str] = []
    for message in messages:
        if remaining <= 0:
            break
        value = message[:remaining]
        bounded.append(value)
        remaining -= len(value)
    transport = failure if getattr(failure, "kind", None) == "transport" else None
    return BatchCaseView(
        case_number=case.case_number,
        request=case.request,
        outcome_kind="transport" if transport is not None else "http",
        status_code=case.status_code,
        reason_phrase=case.reason_phrase,
        failure_messages=bounded,
        transport_code=getattr(transport, "code", None),
    )


def test_case_run_batch_tool_spec() -> ToolSpec:
    """Return the global contract for a 1–5 case target Batch."""
    return ToolSpec(
        name=TEST_CASE_RUN_BATCH_TOOL_NAME,
        description=(
            "Generate and execute 1 to 5 requests for one exact operation from "
            "a frozen request-generation revision. Write methods may permanently "
            "change target state and are not rolled back."
        ),
        kind="local_function",
        input_schema=RunBatchInput.model_json_schema(),
        output_schema=RunBatchOutput.model_json_schema(),
        strict=True,
    )


def test_case_run_batch_tool_binding(
    backend: TestCaseBatchToolBackend,
) -> ToolBinding:
    """Bind the one generic Batch Tool to App-owned execution state."""
    return ToolBinding(name=TEST_CASE_RUN_BATCH_TOOL_NAME, execute=backend.run_batch)
