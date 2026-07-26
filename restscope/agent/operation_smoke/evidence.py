"""Typed, bounded evidence views for Operation Smoke diagnosis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import json
from typing import Any, Mapping

from restscope.observability.content import TraceContentEncoder
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

    MAX_ITEM_BYTES = 64 * 1024
    MAX_TOTAL_BYTES = 256 * 1024

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
        self._total_bytes = 0

    @classmethod
    def from_batch(
        cls,
        *,
        report: OperationExecutionReport,
        config: OperationGeneratorConfig,
        private_case_evidence: Mapping[str, Any] | None = None,
        redactor: Redactor | None = None,
    ) -> "EvidenceJournal":
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
        cases_by_id = {case.case_id: case for case in report.cases}
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

        failed_case_ids: list[str] = []
        failure_refs_by_case: dict[str, list[str]] = {}
        for failure in report.failure_report.unique_failure_messages:
            alias = failure_alias_by_id[failure.failure_id]
            for case_id in failure.case_ids:
                failure_refs_by_case.setdefault(case_id, []).append(alias)
                if case_id not in failed_case_ids:
                    failed_case_ids.append(case_id)

        for index, case_id in enumerate(failed_case_ids, start=1):
            case = cases_by_id.get(case_id)
            if case is None:
                continue
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
        return set(self.failure_aliases)

    @property
    def known_evidence_refs(self) -> set[str]:
        return {
            *self.failure_aliases,
            *self.case_aliases,
            *self.observation_aliases,
        }

    def entry(self, alias: str) -> EvidenceEntry | None:
        return next(
            (entry for entry in self.entries if entry.alias == alias),
            None,
        )

    def prompt_records(self) -> list[dict[str, Any]]:
        return [
            {
                "ref": entry.alias,
                "kind": entry.kind,
                "value": entry.value,
                "original_size_bytes": entry.original_size_bytes,
                "truncated": entry.truncated,
            }
            for entry in self.entries
        ]

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
        encoder = TraceContentEncoder(
            self.redactor,
            max_content_bytes=self.MAX_ITEM_BYTES,
        )
        prepared = encoder.prepare(value)
        decoded = json.loads(prepared.value or "{}")
        if prepared.truncated:
            decoded = {
                **decoded,
                "original_size_bytes": prepared.original_size_bytes,
            }
        encoded_size = len(
            json.dumps(
                decoded,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
        if self._total_bytes + encoded_size > self.MAX_TOTAL_BYTES:
            decoded = {
                "preview": {},
                "original_size_bytes": prepared.original_size_bytes,
                "truncated": True,
                "journal_limit_reached": True,
            }
            encoded_size = len(
                json.dumps(decoded, separators=(",", ":")).encode("utf-8")
            )
        self._total_bytes += encoded_size
        self.entries.append(
            EvidenceEntry(
                alias=alias,
                kind=kind,
                value=decoded,
                original_size_bytes=prepared.original_size_bytes,
                truncated=prepared.truncated
                or bool(
                    isinstance(decoded, dict)
                    and decoded.get("journal_limit_reached")
                ),
            )
        )


def _case_evidence(
    case,
    *,
    semantic: SemanticInputMap,
    failure_refs: list[str],
    private: Any,
) -> dict[str, Any]:
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


def _tool_failure_messages(result) -> list[str]:
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
