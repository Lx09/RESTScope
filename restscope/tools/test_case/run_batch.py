"""Expose generic bounded Batch generation and target execution as one Tool.

The Backend serializes calls so one Agent cannot interleave two mutating
Batches. ``OperationTestingService`` freezes the Generation Store revision and
prepares every case before the first request. This Tool then projects inline
requests and bounded outcomes while exposing the durable Batch identity and
any advisory persistence warnings.
"""

from __future__ import annotations

import json
from threading import RLock
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from restscope.llm import ToolSpec
from restscope.request_generation.selection import TestMode
from restscope.request_generation.store import GeneratorConfigError
from restscope.tools.context import ToolContext
from restscope.tools.runtime import ToolBinding, ToolFailure

TEST_CASE_RUN_BATCH_TOOL_NAME = "test_case.run_batch"
_MAX_FAILURE_MESSAGE_CHARACTERS = 1_200
_MAX_OUTPUT_CHARACTERS = 24_000


class RunBatchInput(BaseModel):
    """Select one operation, semantic test mode, and bounded case count."""

    model_config = ConfigDict(extra="forbid")

    operation_key: str = Field(min_length=1, max_length=1_000)
    test_mode: Literal["happy_path", "exceptional"]
    case_count: int = Field(default=1, ge=1, le=5)
    seed: int | None = Field(default=None, ge=0, le=2**63 - 1)


class BatchCaseView(BaseModel):
    """Return one canonical request and either HTTP or transport facts."""

    model_config = ConfigDict(extra="forbid")

    case_number: int = Field(ge=1, le=5)
    test_action: Literal[
        "happy_path",
        "negative_generator",
        "ignored_constraint",
    ]
    negative_rule: str | None = None
    ignored_constraint_count: int = Field(ge=0, le=20)
    bug_found: bool
    bug_categories: list[str] = Field(max_length=3)
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
    abstract_test_case_id: str
    batch_id: str
    seed: int = Field(ge=0)
    test_mode: Literal["happy_path", "exceptional"]
    requested_case_count: int = Field(ge=1, le=5)
    executed_case_count: int = Field(ge=1, le=5)
    skipped_case_count: int = Field(ge=0, le=5)
    success_count: int = Field(ge=0, le=5)
    failure_count: int = Field(ge=0, le=5)
    bug_count: int = Field(ge=0, le=5)
    batch_persistence_warnings: list[str] = Field(max_length=20)
    cases: list[BatchCaseView] = Field(min_length=1, max_length=5)


class BatchExecutionBackend(Protocol):
    """Describe the narrow Harness service consumed by the Batch Tool."""

    config_store: object

    def run_batch(
        self,
        context: ToolContext,
        *,
        operation_key: str,
        test_mode: TestMode,
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
                    test_mode=TestMode(request.test_mode),
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
                abstract_test_case_id=result.abstract_test_case_id,
                batch_id=result.batch_id,
                seed=result.seed,
                test_mode=result.test_mode.value,
                requested_case_count=result.requested_case_count,
                executed_case_count=len(cases),
                skipped_case_count=result.skipped_case_count,
                success_count=result.success_count,
                failure_count=len(cases) - result.success_count,
                bug_count=sum(case.bug_found for case in result.cases),
                batch_persistence_warnings=list(
                    result.batch_persistence_warnings
                ),
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
        test_action=case.test_action,
        negative_rule=case.negative_rule,
        ignored_constraint_count=case.ignored_constraint_count,
        bug_found=case.bug_found,
        bug_categories=case.bug_categories,
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
            "Generate and execute 1 to 5 happy-path or exceptional requests for "
            "one exact operation from a frozen request-generation revision. "
            "The caller must choose test_mode. Write methods may permanently "
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
