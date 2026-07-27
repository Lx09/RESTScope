"""Typed, bounded evidence views for Operation Smoke diagnosis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import json
from typing import Any, Mapping

from restscope.redaction import Redactor
from restscope.testing import (
    FailureCaseEvidence,
    OperationGeneratorConfig,
    SemanticInputMap,
    build_semantic_input_map,
    failure_messages_for_evidence,
)
from restscope.testing.models import OperationExecutionReport


@dataclass(slots=True, frozen=True)
class EvidenceEntry:
    """One model-visible record with deterministic size metadata."""

    alias: str
    kind: str
    value: Any
    original_size_bytes: int
    truncated: bool


class EvidenceJournal:
    """Canonical evidence aliases rebuilt into each task-focused prompt."""

    MAX_PROMPT_BODY_BYTES = 4 * 1024
    MAX_PROMPT_BYTES = 64 * 1024

    def __init__(
        self,
        *,
        semantic_inputs: SemanticInputMap,
        redactor: Redactor | None = None,
    ) -> None:
        self.semantic_inputs = semantic_inputs
        self.redactor = redactor or Redactor()
        self.entries: list[EvidenceEntry] = []
        self.failure_aliases: dict[str, str] = {}
        self._failure_alias_by_message: dict[str, str] = {}
        self.case_aliases: dict[str, str] = {}
        self.observation_aliases: dict[str, str] = {}
        self.observation_failure_refs: dict[str, list[str]] = {}
        self.batch_summary: dict[str, Any] = {}

    @classmethod
    def from_batch(
        cls,
        *,
        report: OperationExecutionReport,
        config: OperationGeneratorConfig,
        private_case_evidence: Mapping[str, Any] | None = None,
        redactor: Redactor | None = None,
    ) -> "EvidenceJournal":
        """
        Handle from batch as part of the run-local Operation Smoke diagnosis and
        candidate workflow.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        journal = cls(
            semantic_inputs=build_semantic_input_map(config),
            redactor=redactor,
        )
        journal.batch_summary = {
            "status": report.status,
            "status_code_counts": report.status_code_counts,
            "transport_error_count": report.error_count,
            "observed_2xx": report.observed_2xx,
            "response_validation": report.response_validation,
            "behavior_monitor_warning_count": (
                report.behavior_monitor_warning_count
            ),
        }
        private = dict(private_case_evidence or {})
        failure_alias_by_id: dict[str, str] = {}
        for index, failure in enumerate(
            report.failure_report.unique_failure_messages,
            start=1,
        ):
            alias = f"F{index}"
            failure_alias_by_id[failure.failure_id] = alias
            journal.failure_aliases[alias] = failure.failure_id
            journal._failure_alias_by_message[failure.message] = alias
            journal._append(
                alias,
                "failure",
                {
                    "message": failure.message,
                    "case_refs": [],
                },
            )

        failure_refs_by_case: dict[str, list[str]] = {}
        for failure in report.failure_report.unique_failure_messages:
            alias = failure_alias_by_id[failure.failure_id]
            for case_id in failure.case_ids:
                failure_refs_by_case.setdefault(case_id, []).append(alias)

        for index, case in enumerate(report.cases, start=1):
            case_id = case.case_id
            alias = f"C{index}"
            journal.case_aliases[alias] = case_id
            value = _case_evidence(
                case,
                semantic=journal.semantic_inputs,
                failure_refs=failure_refs_by_case.get(case_id, []),
                private=private.get(case_id),
            )
            journal._append(alias, "case", value)
            for failure_alias in failure_refs_by_case.get(case_id, []):
                entry = journal.entry(failure_alias)
                if entry is not None and isinstance(entry.value, dict):
                    entry.value["case_refs"].append(alias)
        return journal

    @property
    def known_failure_refs(self) -> set[str]:
        """
        Handle known failure refs as part of the run-local Operation Smoke diagnosis and
        candidate workflow.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return set(self.failure_aliases)

    @property
    def known_evidence_refs(self) -> set[str]:
        """
        Handle known evidence refs as part of the run-local Operation Smoke diagnosis
        and candidate workflow.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return {
            *self.failure_aliases,
            *self.case_aliases,
            *self.observation_aliases,
        }

    def entry(self, alias: str) -> EvidenceEntry | None:
        """
        Handle entry as part of the run-local Operation Smoke diagnosis and candidate
        workflow.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        return next(
            (entry for entry in self.entries if entry.alias == alias),
            None,
        )

    def prompt_records(self) -> list[dict[str, Any]]:
        """
        Handle prompt records as part of the run-local Operation Smoke diagnosis and
        candidate workflow.

        The class owns any required collaborators or state; arguments supply only the
        data needed for this call.
        """
        records = [
            {
                "ref": entry.alias,
                "kind": entry.kind,
                "value": _compact_prompt_value(
                    entry.kind,
                    entry.value,
                ),
                "truncated": entry.truncated,
            }
            for entry in self.entries
        ]
        skeletons = [_prompt_record_skeleton(record) for record in records]
        if _encoded_size(skeletons) > self.MAX_PROMPT_BYTES:
            skeletons = [
                {
                    "ref": record["ref"],
                    "kind": record["kind"],
                    "value": {"truncated": True},
                    "truncated": True,
                }
                for record in records
            ]
        output = list(skeletons)
        for index, record in enumerate(records):
            candidate = [*output]
            candidate[index] = record
            if _encoded_size(candidate) <= self.MAX_PROMPT_BYTES:
                output[index] = record
        return output

    def record_tool_result(self, tool_call, result) -> tuple[str, str | None]:
        """Record one HTTP observation and optionally classify a new failure."""

        observation_alias = f"O{len(self.observation_aliases) + 1}"
        self.observation_aliases[observation_alias] = tool_call.id
        self.observation_failure_refs[observation_alias] = []
        self._append(
            observation_alias,
            "http_observation",
            {
                "request": tool_call.arguments,
                "status": result.status,
                "response": result.structured,
                "error": result.error,
                "behavior_monitor": result.metadata.get(
                    "response_processor_details"
                ),
            },
        )
        failure_messages = _tool_failure_messages(result)
        if not failure_messages:
            return observation_alias, None
        first_alias: str | None = None
        for failure_message in failure_messages:
            failure_alias = self._failure_alias_by_message.get(failure_message)
            if failure_alias is None:
                failure_alias = f"F{len(self.failure_aliases) + 1}"
                self.failure_aliases[failure_alias] = tool_call.id
                self._failure_alias_by_message[failure_message] = failure_alias
                self._append(
                    failure_alias,
                    "failure",
                    {
                        "message": failure_message,
                        "observation_refs": [observation_alias],
                    },
                )
            self.observation_failure_refs[observation_alias].append(
                failure_alias
            )
            if first_alias is None:
                first_alias = failure_alias
        return observation_alias, first_alias

    def observation_reproduces(
        self,
        observation_ref: str,
        failure_ref: str,
    ) -> bool:
        """Return whether one observation contains the same failure signature."""

        target = self.entry(failure_ref)
        if target is None or not isinstance(target.value, dict):
            return False
        target_message = target.value.get("message")
        return any(
            (
                candidate is not None
                and isinstance(candidate.value, dict)
                and candidate.value.get("message") == target_message
            )
            for candidate in (
                self.entry(ref)
                for ref in self.observation_failure_refs.get(
                    observation_ref,
                    [],
                )
            )
        )

    def _append(self, alias: str, kind: str, value: Any) -> None:
        normalized = self.redactor.redact(value)
        self.entries.append(
            EvidenceEntry(
                alias=alias,
                kind=kind,
                value=normalized,
                original_size_bytes=_encoded_size(normalized),
                truncated=False,
            )
        )


def build_effect_validation_payload(
    *,
    baseline_report: OperationExecutionReport,
    candidate_report: OperationExecutionReport,
    baseline_private_case_evidence: Mapping[str, Any] | None,
    candidate_private_case_evidence: Mapping[str, Any] | None,
    baseline_failures: list[Mapping[str, Any]],
    candidate_failures: list[Mapping[str, Any]],
    confirmed_diagnoses: list[Mapping[str, Any]],
    group_failure_mapping: list[Mapping[str, Any]],
    redactor: Redactor,
) -> dict[str, Any]:
    """Build one redacted, size-bounded view for real-effect validation.

    Public execution reports deliberately omit response bodies. Smoke execution
    retains non-2xx bodies separately for the lifetime of one run, so this
    projection must join each report case to its private evidence before the
    model compares baseline and candidate failures.

    One uniform value budget is chosen for the whole payload. Reducing every
    preview together keeps all cases represented fairly instead of spending
    the entire prompt budget on the first large response.
    """

    baseline_private = dict(baseline_private_case_evidence or {})
    candidate_private = dict(candidate_private_case_evidence or {})

    def render(max_value_bytes: int) -> dict[str, Any]:
        payload = {
            "baseline": _effect_batch_evidence(
                report=baseline_report,
                private_case_evidence=baseline_private,
                failures=baseline_failures,
                max_value_bytes=max_value_bytes,
            ),
            "candidate": _effect_batch_evidence(
                report=candidate_report,
                private_case_evidence=candidate_private,
                failures=candidate_failures,
                max_value_bytes=max_value_bytes,
            ),
            "confirmed_diagnoses": _effect_diagnosis_evidence(
                confirmed_diagnoses,
                max_value_bytes=max_value_bytes,
            ),
            "group_failure_mapping": [
                {
                    "group_id": item.get("group_id"),
                    "root_failure_refs": list(
                        item.get("root_failure_refs", [])
                    ),
                }
                for item in group_failure_mapping
            ],
        }
        return redactor.redact(payload)

    # Most runs fit with the normal per-value 4 KiB ceiling. If the combined
    # baseline/candidate view is larger, binary search finds the largest common
    # preview size that stays within the 64 KiB model-input boundary.
    maximum = EvidenceJournal.MAX_PROMPT_BODY_BYTES
    candidate = render(maximum)
    if _encoded_size(candidate) <= EvidenceJournal.MAX_PROMPT_BYTES:
        return candidate

    smallest = render(0)
    if _encoded_size(smallest) > EvidenceJournal.MAX_PROMPT_BYTES:
        raise RuntimeError(
            "Effect validation evidence metadata exceeds the 64 KiB limit"
        )

    best = smallest
    low = 1
    high = maximum - 1
    while low <= high:
        middle = (low + high) // 2
        candidate = render(middle)
        if _encoded_size(candidate) <= EvidenceJournal.MAX_PROMPT_BYTES:
            best = candidate
            low = middle + 1
        else:
            high = middle - 1
    return best


def _effect_batch_evidence(
    *,
    report: OperationExecutionReport,
    private_case_evidence: Mapping[str, Any],
    failures: list[Mapping[str, Any]],
    max_value_bytes: int,
) -> dict[str, Any]:
    """Project one public batch plus its run-local private response evidence."""

    return {
        "status_code_counts": report.status_code_counts,
        "transport_error_count": report.error_count,
        "failures": [
            {
                "ref": failure.get("ref"),
                "message": _bounded_value(
                    failure.get("message"),
                    max_bytes=max_value_bytes,
                ),
                "case_refs": list(failure.get("case_refs", [])),
            }
            for failure in failures
        ],
        "cases": [
            _effect_case_evidence(
                case,
                private=private_case_evidence.get(case.case_id),
                max_value_bytes=max_value_bytes,
            )
            for case in report.cases
        ],
    }


def _effect_diagnosis_evidence(
    diagnoses: list[Mapping[str, Any]],
    *,
    max_value_bytes: int,
) -> list[dict[str, Any]]:
    """Keep diagnosis identity while bounding its natural-language fields."""

    return [
        {
            "item_id": item.get("item_id"),
            "failure_ref": item.get("failure_ref"),
            "root_failure_refs": list(item.get("root_failure_refs", [])),
            "cause": _bounded_value(
                item.get("cause"),
                max_bytes=max_value_bytes,
            ),
            "desired_behaviors": [
                {
                    "input": _bounded_value(
                        behavior.get("input"),
                        max_bytes=max_value_bytes,
                    ),
                    "desired_behavior": _bounded_value(
                        behavior.get("desired_behavior"),
                        max_bytes=max_value_bytes,
                    ),
                }
                for behavior in item.get("desired_behaviors", [])
            ],
        }
        for item in diagnoses
    ]


def _effect_case_evidence(
    case: Any,
    *,
    private: Any,
    max_value_bytes: int,
) -> dict[str, Any]:
    """Create one compact effect case, including only non-2xx response bodies."""

    generated = case.generated_test_case
    request = {
        "method": case.request.method,
        "path": _bounded_value(
            case.request.path,
            max_bytes=max_value_bytes,
        ),
        "query": _bounded_value(
            list(case.request.query_items),
            max_bytes=max_value_bytes,
        ),
        "generated_parameters": {
            "path": _bounded_value(
                generated.path_parameters,
                max_bytes=max_value_bytes,
            ),
            "query": _bounded_value(
                generated.query_parameters,
                max_bytes=max_value_bytes,
            ),
            "headers": _bounded_value(
                generated.header_parameters,
                max_bytes=max_value_bytes,
            ),
            "cookies": _bounded_value(
                generated.cookie_parameters,
                max_bytes=max_value_bytes,
            ),
        },
        "body_present": generated.body_present,
        "body": (
            _bounded_value(
                generated.body,
                max_bytes=max_value_bytes,
            )
            if generated.body_present
            else None
        ),
    }
    response = (
        case.response.model_dump(mode="json")
        if case.response is not None
        else None
    )
    private_mapping = _private_evidence_mapping(private)
    if (
        response is not None
        and not 200 <= int(response["status_code"]) < 300
    ):
        raw_body = private_mapping.get("response_body")
        decoded_body = _decoded_response_body(
            raw_body,
            encoding=private_mapping.get("response_encoding"),
            media_type=(
                str(response["media_type"])
                if response.get("media_type") is not None
                else None
            ),
        )
        prompt_body_size = (
            _encoded_size(decoded_body)
            if decoded_body is not None
            else 0
        )
        response.update(
            {
                "body_available": raw_body is not None,
                "body": (
                    _bounded_value(
                        decoded_body,
                        max_bytes=max_value_bytes,
                    )
                    if decoded_body is not None
                    else None
                ),
                "body_original_size_bytes": _response_body_size(
                    raw_body,
                    declared_size=response.get("content_length"),
                ),
                "body_truncated": bool(
                    private_mapping.get("response_body_truncated")
                )
                or (
                    decoded_body is not None
                    and prompt_body_size > max_value_bytes
                ),
            }
        )
    return {
        "case_ref": case.case_id,
        "request": request,
        "response": response,
        "transport_error": (
            _bounded_value(
                case.transport_error.model_dump(mode="json"),
                max_bytes=max_value_bytes,
            )
            if case.transport_error is not None
            else None
        ),
        "response_validation": _bounded_value(
            case.response_validation,
            max_bytes=max_value_bytes,
        ),
        "behavior_monitor_warning_count": len(
            case.behavior_monitor_warnings
        ),
    }


def _private_evidence_mapping(value: Any) -> Mapping[str, Any]:
    """Normalize the dataclass/model/mapping shapes used by batch runners."""

    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, Mapping) else {}
    if is_dataclass(value) and not isinstance(value, type):
        dumped = asdict(value)
        return dumped if isinstance(dumped, Mapping) else {}
    return value if isinstance(value, Mapping) else {}


def _response_body_size(
    value: Any,
    *,
    declared_size: Any,
) -> int | None:
    """Return the best available pre-prompt body size."""

    if isinstance(declared_size, int) and declared_size >= 0:
        return declared_size
    if value is None:
        return None
    if isinstance(value, bytes | bytearray):
        return len(value)
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    return _encoded_size(value)


def _case_evidence(
    case,
    *,
    semantic: SemanticInputMap,
    failure_refs: list[str],
    private: Any,
) -> dict[str, Any]:
    """
    Handle case evidence as part of the run-local Operation Smoke diagnosis and
    candidate workflow.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    values: dict[str, list[Any]] = {}
    for generated in case.generated_test_case.generated_values:
        handle = semantic.handle_by_node.get(generated.input_node_id)
        if handle is not None:
            values.setdefault(handle, []).append(generated.value)
    normalized_values = {
        handle: items[0] if len(items) == 1 else items
        for handle, items in values.items()
    }
    omitted = [
        semantic.handle_by_node[node_id]
        for node_id in case.generated_test_case.omitted_input_node_ids
        if node_id in semantic.handle_by_node
    ]
    payload: dict[str, Any] = {
        "failure_refs": failure_refs,
        "generated_inputs": normalized_values,
        "omitted_inputs": omitted,
        "request": {
            "method": case.request.method,
            "path": case.request.path,
            "query": list(case.request.query_items),
            "headers": dict(case.generated_test_case.header_parameters),
            "cookies": dict(case.generated_test_case.cookie_parameters),
            "body": (
                case.generated_test_case.body
                if case.generated_test_case.body_present
                else None
            ),
        },
        "response": (
            case.response.model_dump(mode="json")
            if case.response is not None
            else None
        ),
        "transport_error": (
            case.transport_error.model_dump(mode="json")
            if case.transport_error is not None
            else None
        ),
        "behavior_monitor": {
            "response_validation": case.response_validation,
            "warnings": [
                warning.model_dump(mode="json")
                for warning in case.behavior_monitor_warnings
            ],
        },
    }
    if private is not None:
        if hasattr(private, "model_dump"):
            private = private.model_dump(mode="json")
        elif is_dataclass(private) and not isinstance(private, type):
            private = asdict(private)
        if isinstance(private, Mapping):
            payload["private_response"] = dict(private)
    return payload


def _compact_prompt_value(kind: str, value: Any) -> Any:
    """
    Handle compact prompt value as part of the run-local Operation Smoke diagnosis and
    candidate workflow.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    if kind != "case" or not isinstance(value, Mapping):
        return value
    request = value.get("request")
    request = request if isinstance(request, Mapping) else {}
    response = value.get("response")
    response = response if isinstance(response, Mapping) else None
    private = value.get("private_response")
    private = private if isinstance(private, Mapping) else {}
    response_body = _decoded_response_body(
        private.get("response_body"),
        encoding=private.get("response_encoding"),
        media_type=(
            str(response.get("media_type"))
            if response is not None
            and response.get("media_type") is not None
            else None
        ),
    )
    return {
        "failure_refs": value.get("failure_refs", []),
        "generated_inputs": _bounded_value(
            value.get("generated_inputs", {}),
            max_bytes=EvidenceJournal.MAX_PROMPT_BODY_BYTES,
        ),
        "omitted_inputs": value.get("omitted_inputs", []),
        "request": {
            "method": request.get("method"),
            "path": request.get("path"),
            "query": request.get("query", []),
            "body": _bounded_value(
                request.get("body"),
                max_bytes=EvidenceJournal.MAX_PROMPT_BODY_BYTES,
            ),
        },
        "response": (
            {
                "status_code": response.get("status_code"),
                "reason_phrase": response.get("reason_phrase"),
                "media_type": response.get("media_type"),
                "body": _bounded_value(
                    response_body,
                    max_bytes=EvidenceJournal.MAX_PROMPT_BODY_BYTES,
                ),
                "body_truncated": bool(
                    private.get("response_body_truncated")
                ),
            }
            if response is not None
            else None
        ),
        "transport_error": value.get("transport_error"),
        "response_validation": (
            value.get("behavior_monitor", {}).get("response_validation")
            if isinstance(value.get("behavior_monitor"), Mapping)
            else None
        ),
    }


def _decoded_response_body(
    value: Any,
    *,
    encoding: Any,
    media_type: str | None,
) -> Any:
    """
    Handle decoded response body as part of the run-local Operation Smoke diagnosis and
    candidate workflow.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    if value is None:
        return None
    if isinstance(value, bytes):
        text = value.decode(
            str(encoding)
            if isinstance(encoding, str) and encoding
            else "utf-8",
            errors="replace",
        )
    elif isinstance(value, str):
        text = value
    else:
        return value
    if media_type is not None and "json" in media_type.lower():
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


def _bounded_value(value: Any, *, max_bytes: int) -> Any:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    preview_bytes = encoded[: max(0, max_bytes - 256)]
    return {
        "preview": preview_bytes.decode("utf-8", errors="ignore"),
        "original_size_bytes": len(encoded),
        "truncated": True,
    }


def _prompt_record_skeleton(record: dict[str, Any]) -> dict[str, Any]:
    """
    Handle prompt record skeleton as part of the run-local Operation Smoke diagnosis and
    candidate workflow.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    value = record["value"] if isinstance(record["value"], dict) else {}
    kind = record["kind"]
    if kind == "failure":
        compact_value = {
            "message": _bounded_value(
                value.get("message"),
                max_bytes=512,
            ),
            "case_refs": value.get("case_refs", []),
            "observation_refs": value.get("observation_refs", []),
        }
    elif kind == "case":
        request = value.get("request") or {}
        response = value.get("response") or {}
        transport_error = value.get("transport_error") or {}
        generated = value.get("generated_inputs")
        compact_value = {
            "failure_refs": value.get("failure_refs", []),
            "generated_inputs": (
                {
                    key: {"truncated": True}
                    for key in generated
                }
                if isinstance(generated, dict)
                else {"truncated": True}
            ),
            "omitted_inputs": value.get("omitted_inputs", []),
            "request": {
                "method": request.get("method"),
                "path": request.get("path"),
                "query": _bounded_value(
                    request.get("query", []),
                    max_bytes=512,
                ),
                "body": {"truncated": True},
            },
            "response": (
                {
                    "status_code": response.get("status_code"),
                    "reason_phrase": response.get("reason_phrase"),
                    "media_type": response.get("media_type"),
                    "body": {"truncated": True},
                }
                if response
                else None
            ),
            "transport_error": (
                {
                    "code": transport_error.get("code"),
                    "message": _bounded_value(
                        transport_error.get("message"),
                        max_bytes=512,
                    ),
                }
                if transport_error
                else None
            ),
            "response_validation": value.get("response_validation"),
        }
    elif kind == "http_observation":
        request = value.get("request") or {}
        response = value.get("response") or {}
        error = value.get("error") or {}
        compact_value = {
            "request": {
                "method": request.get("method"),
                "path": request.get("path"),
            },
            "status": value.get("status"),
            "response": {
                "status_code": response.get("status_code"),
                "body": {"truncated": True},
            },
            "error": {
                "code": error.get("code"),
                "message": _bounded_value(
                    error.get("message"),
                    max_bytes=512,
                ),
            },
        }
    else:
        compact_value = {"truncated": True}
    return {
        "ref": record["ref"],
        "kind": kind,
        "value": compact_value,
        "truncated": True,
    }


def _encoded_size(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    )


def _tool_failure_messages(result) -> list[str]:
    """
    Handle tool failure messages as part of the run-local Operation Smoke diagnosis and
    candidate workflow.

    This private helper keeps one transformation or policy decision explicit so the
    surrounding orchestration remains readable.
    """
    if result.status != "succeeded":
        error = result.error or {}
        code = str(error.get("code") or result.status)
        message = error.get("message")
        return failure_messages_for_evidence(
            FailureCaseEvidence(
                case_id=result.tool_call_id,
                transport_error_code=code,
                transport_error_message=(
                    str(message) if message is not None else None
                ),
            )
        )
    structured = result.structured
    if not isinstance(structured, Mapping):
        return []
    status_code = structured.get("status_code")
    if not isinstance(status_code, int):
        return []
    headers = structured.get("headers")
    media_type = None
    if isinstance(headers, Mapping):
        media_type = next(
            (
                str(value)
                for key, value in headers.items()
                if str(key).lower() == "content-type"
            ),
            None,
        )
    body = structured.get("body")
    body_format = structured.get("body_format")
    encoded_body: bytes | None = None
    if body is not None:
        if body_format == "json":
            encoded_body = json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
            media_type = media_type or "application/json"
        else:
            encoded_body = str(body).encode("utf-8")
            media_type = media_type or "text/plain"
    return failure_messages_for_evidence(
        FailureCaseEvidence(
            case_id=result.tool_call_id,
            status_code=status_code,
            reason_phrase=(
                str(structured["reason_phrase"])
                if structured.get("reason_phrase") is not None
                else None
            ),
            media_type=media_type,
            body=encoded_body,
            encoding="utf-8",
        )
    )
