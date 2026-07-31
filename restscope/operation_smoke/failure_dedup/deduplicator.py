"""Turn one failed Batch into unique, single-case Failure work items.

This deterministic Module owns message extraction, exact deduplication,
representative-case selection, LLM-output validation handoff, and Memory
recording. It calls :class:`FailureDedupAgent` only when several distinct
message Fingerprints require semantic Parameter grouping.
"""

from __future__ import annotations

from collections import OrderedDict
import json
from typing import Any, Protocol

from restscope.http_transport import is_sensitive_header
from restscope.observability import TracingRuntime
from restscope.operation_smoke.memory import (
    FailureBatchWrite,
    FailureObservationWrite,
    FailureWrite,
    RecordedFailures,
)

from .agent import FailureDedupAgent
from .schemas import (
    FailureDedupDecision,
    FailureDedupRequest,
    FailureDedupResult,
    FailureGroupDecision,
    FailureTodo,
)


_JSON_KEYS = ("message", "detail", "error", "title", "errors")
_FIELD_KEYS = ("field", "path", "name")
_MAX_MESSAGE_CHARS = 4_096
_MAX_FINGERPRINTS = 100
_MAX_MESSAGES_PER_CASE = 100


class FailureMemoryWriter(Protocol):
    """Describe the only Memory mutation required by Failure Dedup."""

    def record_failures(self, write: FailureBatchWrite) -> RecordedFailures:
        """Persist validated Failures and return their new stable identities."""
        ...


class FailureDeduplicator:
    """Hide exact and semantic deduplication behind one Coordinator Interface."""

    def __init__(
        self,
        *,
        agent: FailureDedupAgent,
        memory: FailureMemoryWriter,
        tracing_runtime: TracingRuntime | None = None,
    ) -> None:
        """Store the semantic Agent and deterministic Memory writer."""
        self.agent = agent
        self.memory = memory
        self.tracing_runtime = tracing_runtime or TracingRuntime.disabled()

    def deduplicate(
        self,
        request: FailureDedupRequest,
        *,
        max_outputs: int,
    ) -> FailureDedupResult:
        """Return one single-case Solve Todo for every distinct Failure.

        The first case producing each normalized message wins. After semantic
        grouping, the earliest original Batch case among a group's messages
        becomes that Failure's sole representative.
        """
        fingerprints = _fingerprints(request.cases)
        if not fingerprints:
            raise RuntimeError(
                "The unsuccessful Batch did not contain deduplicable failure evidence."
            )
        if len(fingerprints) > _MAX_FINGERPRINTS:
            raise RuntimeError(
                "The Batch exceeded the 100 unique Failure Fingerprint limit."
            )

        with self.tracing_runtime.span(
            "FailureDeduplicator.deduplicate",
            kind="CHAIN",
            input_value={
                "operation_key": request.operation_key,
                "failed_case_count": len(request.cases),
                "exact_fingerprint_count": len(fingerprints),
            },
        ) as span:
            if len(fingerprints) == 1:
                message = next(iter(fingerprints))
                decision = FailureDedupDecision(
                    failures=[
                        FailureGroupDecision(
                            summary=message,
                            suspected_parameters=[],
                            messages=[message],
                        )
                    ],
                    reason="One exact Failure Fingerprint requires no LLM grouping.",
                )
                result = self._record_and_expand(
                    request=request,
                    fingerprints=fingerprints,
                    decision=decision,
                    outputs_used=0,
                    correction_count=0,
                    bypassed=True,
                )
            else:
                if max_outputs < 1:
                    result = FailureDedupResult(
                        status="dedup_budget_exhausted",
                        reason="The Failure Dedup output budget was exhausted.",
                        outputs_used=0,
                        correction_count=0,
                        exact_fingerprint_count=len(fingerprints),
                    )
                    span.set_output(result.model_dump(mode="json"))
                    return result
                observations = [
                    {
                        "message": message,
                        "test_case": _prompt_test_case(case),
                    }
                    for message, case in fingerprints.items()
                ]
                decision, outputs, corrections, errors = self.agent.deduplicate(
                    operation_key=request.operation_key,
                    semantic_parameters=request.semantic_parameters,
                    observations=observations,
                    max_outputs=max_outputs,
                )
                if decision is None:
                    result = FailureDedupResult(
                        status="dedup_budget_exhausted",
                        reason="; ".join(
                            errors
                            or ["The Failure Dedup output budget was exhausted."]
                        ),
                        outputs_used=outputs,
                        correction_count=corrections,
                        exact_fingerprint_count=len(fingerprints),
                    )
                else:
                    result = self._record_and_expand(
                        request=request,
                        fingerprints=fingerprints,
                        decision=decision,
                        outputs_used=outputs,
                        correction_count=corrections,
                        bypassed=False,
                    )
            span.set_output(
                {
                    "status": result.status,
                    "failure_count": len(result.todos),
                    "outputs_used": result.outputs_used,
                    "correction_count": result.correction_count,
                }
            )
            return result

    def _record_and_expand(
        self,
        *,
        request: FailureDedupRequest,
        fingerprints: OrderedDict[str, dict[str, Any]],
        decision: FailureDedupDecision,
        outputs_used: int,
        correction_count: int,
        bypassed: bool,
    ) -> FailureDedupResult:
        """Persist one representative Observation, then create Solve Todos."""
        selections = [
            _select_representative(
                group=group,
                fingerprints=fingerprints,
                cases=request.cases,
            )
            for group in decision.failures
        ]
        recorded = self.memory.record_failures(
            FailureBatchWrite(
                operation_key=request.operation_key,
                round_number=request.round_number,
                batch_run_id=request.batch_run_id,
                failures=[
                    FailureWrite(
                        summary=group.summary,
                        suspected_parameters=(
                            None if bypassed else group.suspected_parameters
                        ),
                        observations=[
                            _observation_write(
                                # The model may list messages in any order. Use
                                # the message that actually produced the chosen
                                # earliest case so Memory evidence cannot pair
                                # one HTTP exchange with another error text.
                                message=message,
                                case=case,
                            )
                        ],
                    )
                    for group, (message, case) in zip(
                        decision.failures,
                        selections,
                        strict=True,
                    )
                ],
            )
        )
        todos: list[FailureTodo] = []
        for index, (group, stable, (_, case)) in enumerate(
            zip(
                decision.failures,
                recorded.failures,
                selections,
                strict=True,
            ),
            start=1,
        ):
            todos.append(
                FailureTodo(
                    todo_id=f"T{index}",
                    failure_id=stable.failure_id,
                    failure=group.summary,
                    test_case=case,
                    suspected_parameters=(
                        None if bypassed else group.suspected_parameters
                    ),
                )
            )
        return FailureDedupResult(
            status="bypassed" if bypassed else "deduplicated",
            todos=todos,
            reason=decision.reason,
            outputs_used=outputs_used,
            correction_count=correction_count,
            exact_fingerprint_count=len(fingerprints),
        )


def _select_representative(
    *,
    group: FailureGroupDecision,
    fingerprints: OrderedDict[str, dict[str, Any]],
    cases: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Choose the earliest Batch case and retain its matching error message."""
    return min(
        ((message, fingerprints[message]) for message in group.messages),
        key=lambda item: cases.index(item[1]),
    )


def _fingerprints(
    cases: list[dict[str, Any]],
) -> OrderedDict[str, dict[str, Any]]:
    """Keep the first Batch case for every exact normalized failure message."""
    output: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for case in cases:
        for message in _case_messages(case):
            output.setdefault(message, case)
    return output


def _case_messages(case: dict[str, Any]) -> list[str]:
    """Extract bounded transport, JSON, text, or status fallback messages."""
    transport = case.get("transport_error")
    if isinstance(transport, dict):
        code = _normalize_text(str(transport.get("code") or "transport_error"))
        detail = _normalize_text(str(transport.get("message") or ""))
        return [_bound(f"TRANSPORT {code}: {detail}" if detail else f"TRANSPORT {code}")]

    response = case.get("response")
    if not isinstance(response, dict):
        return []
    status = response.get("status_code")
    if not isinstance(status, int) or 200 <= status < 300:
        return []
    fallback = _bound(
        f"HTTP {status}"
        + (
            f" {_normalize_text(str(response.get('reason_phrase')))}"
            if response.get("reason_phrase")
            else ""
        )
    )
    body = response.get("body")
    if body in (None, ""):
        return [fallback]
    media = str(response.get("media_type") or "").split(";", 1)[0].lower()
    if media == "application/json" or media.endswith("+json"):
        if response.get("body_truncated"):
            return [fallback + " [failure body truncated]"]
        try:
            payload = body if not isinstance(body, str) else json.loads(body)
        except (TypeError, json.JSONDecodeError):
            return [fallback]
        extracted = _extract_json_messages(payload)[:_MAX_MESSAGES_PER_CASE]
        return [_bound(f"HTTP {status}: {item}") for item in extracted] or [fallback]
    if media.startswith("text/"):
        detail = _normalize_text(str(body))
        return [_bound(f"HTTP {status}: {detail}")] if detail else [fallback]
    return [fallback]


def _extract_json_messages(value: Any, *, depth: int = 0) -> list[str]:
    """Find conventional API error fields without traversing arbitrary bodies."""
    if depth > 8 or not isinstance(value, dict):
        return []
    for key in _JSON_KEYS:
        if key not in value:
            continue
        result = _extract_json_field(key, value[key], depth=depth + 1)
        if result:
            return result
    return []


def _extract_json_field(key: str, value: Any, *, depth: int) -> list[str]:
    """Extract strings and field-aware entries from one conventional error key."""
    if isinstance(value, str):
        normalized = _normalize_text(value)
        return [normalized] if normalized else []
    if isinstance(value, list):
        output: list[str] = []
        for item in value:
            output.extend(_extract_error_item(item, depth=depth + 1))
        return output
    if isinstance(value, dict):
        nested = _extract_json_messages(value, depth=depth + 1)
        if nested:
            return nested

        # GitLab and many validation libraries place field names directly
        # beneath ``message`` or ``errors``, for example
        # {"message": {"name": ["has already been taken"]}}. Retaining the
        # field path is essential: otherwise unrelated Parameter failures would
        # both collapse to a generic HTTP status Fingerprint.
        output: list[str] = []
        for field, child in value.items():
            output.extend(
                _extract_field_messages(
                    field=_normalize_text(str(field)),
                    value=child,
                    depth=depth + 1,
                )
            )
        return output
    return []


def _extract_error_item(value: Any, *, depth: int) -> list[str]:
    """Retain a field name when an API returns an array of structured errors."""
    if isinstance(value, str):
        normalized = _normalize_text(value)
        return [normalized] if normalized else []
    if not isinstance(value, dict) or depth > 8:
        return []
    messages = _extract_json_messages(value, depth=depth + 1)
    if not messages:
        messages = [
            message
            for key, child in value.items()
            if key not in _FIELD_KEYS
            for message in _extract_field_messages(
                field=_normalize_text(str(key)),
                value=child,
                depth=depth + 1,
            )
        ]
    field = next(
        (
            _normalize_text(str(value[key]))
            for key in _FIELD_KEYS
            if isinstance(value.get(key), (str, int))
        ),
        "",
    )
    return [f"{field}: {message}" for message in messages] if field else messages


def _extract_field_messages(
    *,
    field: str,
    value: Any,
    depth: int,
) -> list[str]:
    """Flatten one field-keyed validation subtree into bounded semantic text."""
    if depth > 8 or not field:
        return []
    if isinstance(value, str):
        message = _normalize_text(value)
        return [f"{field}: {message}"] if message else []
    if isinstance(value, list):
        output: list[str] = []
        for item in value:
            if isinstance(item, str):
                message = _normalize_text(item)
                if message:
                    output.append(f"{field}: {message}")
            else:
                for message in _extract_error_item(item, depth=depth + 1):
                    output.append(f"{field}: {message}")
        return output
    if isinstance(value, dict):
        output: list[str] = []
        for child_field, child in value.items():
            path = f"{field}.{_normalize_text(str(child_field))}"
            output.extend(
                _extract_field_messages(
                    field=path,
                    value=child,
                    depth=depth + 1,
                )
            )
        return output
    return []


def _prompt_test_case(case: dict[str, Any]) -> dict[str, Any]:
    """Remove runtime identities and expose one redacted HTTP JSON example."""
    generated = case.get("generated_test_case")
    generated = generated if isinstance(generated, dict) else {}
    prepared = case.get("request")
    prepared = prepared if isinstance(prepared, dict) else {}
    headers = generated.get("header_parameters") or prepared.get("headers") or {}
    request = {
        "path": prepared.get("path"),
        "path_parameters": generated.get("path_parameters", {}),
        "query": generated.get("query_parameters", {}),
        "headers": {
            name: "[redacted]" if is_sensitive_header(name) else value
            for name, value in headers.items()
        },
    }
    if generated.get("body_present"):
        request["body"] = generated.get("body")
    response = case.get("response")
    return {
        "request": request,
        "response": response,
        **(
            {"transport_error": case["transport_error"]}
            if case.get("transport_error") is not None
            else {}
        ),
    }


def _observation_write(
    *,
    message: str,
    case: dict[str, Any],
) -> FailureObservationWrite:
    """Reduce the representative case to bounded persistent evidence."""
    prompt_case = _prompt_test_case(case)
    response = prompt_case.get("response")
    response = response if isinstance(response, dict) else {}
    return FailureObservationWrite(
        observation_key=str(case.get("case_id") or message),
        trigger=message,
        response_summary={
            key: response[key]
            for key in ("status_code", "media_type")
            if key in response
        },
        necessary_values=prompt_case["request"],
    )


def _normalize_text(value: str) -> str:
    """Collapse whitespace so formatting-only differences share a Fingerprint."""
    return " ".join(value.split())


def _bound(value: str) -> str:
    """Keep messages within the reviewed Prompt and Memory safety allowance."""
    if len(value) <= _MAX_MESSAGE_CHARS:
        return value
    remaining = _MAX_MESSAGE_CHARS - 80
    head = remaining // 2
    tail = remaining - head
    return (
        f"{value[:head]}…[message clipped; chars={len(value)}]…{value[-tail:]}"
    )
