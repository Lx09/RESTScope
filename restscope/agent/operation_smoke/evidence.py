"""Typed, bounded evidence views for Operation Smoke diagnosis."""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
import json
from types import MappingProxyType
from typing import Any, Mapping

from restscope.observability.content import TraceContentEncoder
from restscope.redaction import Redactor
from restscope.testing import OperationGeneratorConfig
from restscope.testing.models import OperationExecutionReport


@dataclass(slots=True, frozen=True)
class SemanticInputMap:
    """Bidirectional semantic handles without exposing persisted node IDs."""

    handle_by_node: Mapping[str, str]
    node_by_handle: Mapping[str, str]


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
        self.case_aliases: dict[str, str] = {}
        self.observation_aliases: dict[str, str] = {}
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
        failure_message = _tool_failure_message(result)
        if failure_message is None:
            return observation_alias, None
        failure_alias = f"F{len(self.failure_aliases) + 1}"
        self.failure_aliases[failure_alias] = tool_call.id
        self._append(
            failure_alias,
            "failure",
            {
                "message": failure_message,
                "observation_refs": [observation_alias],
            },
        )
        return observation_alias, failure_alias

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


def build_semantic_input_map(
    config: OperationGeneratorConfig,
) -> SemanticInputMap:
    """Map active configurable inputs to request-shaped handles."""

    configured_ids = {item.input_node_id for item in config.configs}
    active_root_id = (
        config.snapshot.media_type_node_ids.get(config.active_media_type)
        if config.active_media_type is not None
        else None
    )
    active_root = next(
        (
            node
            for node in config.snapshot.input_nodes
            if node.input_node_id == active_root_id
        ),
        None,
    )
    handle_by_node: dict[str, str] = {}
    node_by_handle: dict[str, str] = {}
    for node in config.snapshot.input_nodes:
        if (
            node.input_node_id not in configured_ids
            or node.input_node_id == config.snapshot.request_body_node_id
        ):
            continue
        if node.canonical_path.startswith("body/"):
            if active_root is None or not (
                node.input_node_id == active_root.input_node_id
                or node.canonical_path.startswith(
                    f"{active_root.canonical_path}/"
                )
            ):
                continue
            relative = node.canonical_path.removeprefix(
                active_root.canonical_path
            ).removeprefix("/")
            handle = _body_handle(relative)
        else:
            handle = _parameter_handle(node.canonical_path)
        if handle in node_by_handle:
            raise ValueError(f"Semantic input handle is not unique: {handle}")
        handle_by_node[node.input_node_id] = handle
        node_by_handle[handle] = node.input_node_id
    return SemanticInputMap(
        handle_by_node=MappingProxyType(handle_by_node),
        node_by_handle=MappingProxyType(node_by_handle),
    )


def _parameter_handle(canonical_path: str) -> str:
    segments = [_unsegment(item) for item in canonical_path.split("/")]
    if len(segments) < 2:
        return ".".join(segments)
    output = f"{segments[0]}.{segments[1]}"
    return _append_schema_segments(output, segments[2:])


def _body_handle(relative_path: str) -> str:
    if not relative_path:
        return "body"
    segments = [_unsegment(item) for item in relative_path.split("/")]
    return _append_schema_segments("body", segments)


def _append_schema_segments(base: str, segments: list[str]) -> str:
    output = base
    index = 0
    while index < len(segments):
        segment = segments[index]
        if segment == "properties" and index + 1 < len(segments):
            output += f".{segments[index + 1]}"
            index += 2
            continue
        if segment == "items":
            output += "[]"
            index += 1
            continue
        if (
            segment in {"oneOf", "anyOf", "allOf"}
            and index + 1 < len(segments)
        ):
            output += f".{segment}[{segments[index + 1]}]"
            index += 2
            continue
        output += f".{segment}"
        index += 1
    return output


def _unsegment(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


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


def _tool_failure_message(result) -> str | None:
    if result.status != "succeeded":
        error = result.error or {}
        message = error.get("message") or error.get("code") or result.status
        return f"HTTP probe {result.status}: {message}"
    structured = result.structured
    if not isinstance(structured, Mapping):
        return None
    status_code = structured.get("status_code")
    if isinstance(status_code, int) and not 200 <= status_code < 300:
        reason = structured.get("reason_phrase")
        body = structured.get("body")
        suffix = f" {reason}" if reason else ""
        if body is not None:
            suffix += ": " + json.dumps(
                body,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            )
        return f"HTTP {status_code}{suffix}"
    warnings = structured.get("behavior_monitor_warnings")
    if isinstance(warnings, list) and warnings:
        return "Behavior monitor warning: " + json.dumps(
            warnings,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    return None
