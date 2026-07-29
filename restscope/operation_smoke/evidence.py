"""Build bounded in-memory Batch evidence for Planner and Failure Solve.

Temporary case codes are created only for one Plan request. Every returned
todo immediately contains expanded case objects. Planner and Solve can inspect
current request inputs, generated values, and response evidence without placing
the raw Batch in persistent Failure Memory.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from pydantic import BaseModel

from restscope.testing import OperationExecutionReport


_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
}


def build_batch_evidence(
    report: OperationExecutionReport,
    private_case_evidence: dict[str, object],
) -> dict[str, Any]:
    """Combine the public report and App-only response bodies by case."""
    cases: list[dict[str, Any]] = []
    for case in report.cases:
        public = case.model_dump(mode="json")
        request = public.get("request")
        if isinstance(request, dict):
            headers = request.get("headers")
            if isinstance(headers, dict):
                request["headers"] = {
                    name: (
                        "[redacted]"
                        if name.lower() in _SENSITIVE_HEADER_NAMES
                        else value
                    )
                    for name, value in headers.items()
                }
        private = _json_value(private_case_evidence.get(case.case_id))
        body = _private_response_body(private)
        if body is not None:
            response = public.setdefault("response", {})
            if response is None:
                response = {}
                public["response"] = response
            response["body"] = body
            response["body_truncated"] = bool(
                private.get("response_body_truncated")
            )
        cases.append(public)
    return {
        "run": report.model_dump(
            mode="json",
            exclude={"cases"},
        ),
        "cases": cases,
    }


def build_plan_case_map(
    batch: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Create Plan-only codes and identify every currently failed case."""
    cases = list(batch.get("cases", []))
    coded = {
        f"C{index}": case
        for index, case in enumerate(cases, start=1)
    }
    failed_ids = {
        case_id
        for failure in batch["run"]["failure_report"][
            "unique_failure_messages"
        ]
        for case_id in failure["case_ids"]
    }
    failed_codes: list[str] = []
    for code, case in coded.items():
        response = case.get("response") or {}
        status = response.get("status_code")
        failed = (
            case.get("case_id") in failed_ids
            or case.get("transport_error") is not None
            or not isinstance(status, int)
            or not 200 <= status < 300
        )
        if failed:
            failed_codes.append(code)
    return coded, failed_codes


def _json_value(value: object | None) -> dict[str, Any]:
    """Convert private dataclass/Pydantic evidence without mutating it."""
    if value is None:
        return {}
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, dict):
        return dict(value)
    return {"value": value}


def _private_response_body(private: dict[str, Any]) -> str | None:
    """Decode the retained response body while preserving undecodable bytes."""
    body = private.get("response_body")
    if body is None:
        body = private.get("body")
    if body is None:
        return None
    if isinstance(body, str):
        return body
    if isinstance(body, bytes):
        encoding = private.get("response_encoding") or private.get("encoding")
        try:
            return body.decode(encoding or "utf-8")
        except (LookupError, UnicodeDecodeError):
            return body.decode("utf-8", errors="replace")
    return str(body)
