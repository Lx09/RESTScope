from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
from typing import Any

from schemathesis_mcp.security import Sanitizer


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class NdjsonProjector:
    """Convert Schemathesis NDJSON records into stable MCP events."""

    def __init__(self, sanitizer: Sanitizer | None = None) -> None:
        self.sanitizer = sanitizer or Sanitizer()

    def project(self, record: dict[str, Any]) -> list[dict[str, Any]]:
        if len(record) != 1:
            return [{"type": "unknown", "payload": self.sanitizer.sanitize(record)}]
        name, payload = next(iter(record.items()))
        if not isinstance(payload, dict):
            return [{"type": _snake_case(name), "payload": self.sanitizer.sanitize(payload)}]
        if name in {"ScenarioFinished", "FuzzScenarioFinished"}:
            return [self._scenario(payload)]
        if name == "LoadingFinished":
            return [
                self.sanitizer.sanitize(
                    {
                        "type": "loading_finished",
                        "timestamp": payload.get("timestamp"),
                        "specification": payload.get("specification"),
                        "operations": payload.get("statistic", {}).get("operations"),
                    }
                )
            ]
        if name == "Initialize":
            return [
                self.sanitizer.sanitize(
                    {
                        "type": "initialize",
                        "cli_version": payload.get("schemathesis_version"),
                        "seed": payload.get("seed"),
                    }
                )
            ]
        if name in {"PhaseStarted", "PhaseFinished"}:
            phase = payload.get("phase")
            if isinstance(phase, dict):
                phase = phase.get("name")
            projected = {
                "type": _snake_case(name),
                "timestamp": payload.get("timestamp"),
                "phase": phase,
            }
            if name == "PhaseFinished":
                projected["status"] = payload.get("status")
            return [self.sanitizer.sanitize(projected)]
        if name in {"NonFatalError", "FatalError"}:
            error = payload.get("value") or payload.get("exception") or {}
            message = error.get("message") if isinstance(error, dict) else str(error)
            return [
                self.sanitizer.sanitize(
                    {
                        "type": _snake_case(name),
                        "timestamp": payload.get("timestamp"),
                        "phase": payload.get("phase"),
                        "label": payload.get("label"),
                        "message": message,
                    }
                )
            ]
        keep = {
            "timestamp",
            "phase",
            "status",
            "running_time",
            "stop_reason",
            "label",
            "worker_id",
        }
        projected = {"type": _snake_case(name), **{key: value for key, value in payload.items() if key in keep}}
        return [self.sanitizer.sanitize(projected)]

    def _scenario(self, payload: dict[str, Any]) -> dict[str, Any]:
        recorder = payload.get("recorder", {})
        failures = self._failures(recorder)
        event = {
            "type": "scenario_finished",
            "timestamp": payload.get("timestamp"),
            "phase": payload.get("phase"),
            "status": payload.get("status"),
            "operation": recorder.get("label") or payload.get("label"),
            "elapsed_time": payload.get("elapsed_time"),
            "is_final": payload.get("is_final", False),
            "failures": len(failures),
        }
        if failures:
            event["_failures"] = failures
        return self.sanitizer.sanitize(event)

    def _failures(self, recorder: dict[str, Any]) -> list[dict[str, Any]]:
        output = []
        cases = recorder.get("cases", {})
        checks = recorder.get("checks", {})
        interactions = recorder.get("interactions", {})
        operation = recorder.get("label", "unknown operation")
        for case_id, nodes in checks.items():
            for node in nodes:
                if node.get("status") != "failure":
                    continue
                failure = node.get("failure_info", {}).get("failure", {})
                check = node.get("name", "unknown_check")
                failure_type = failure.get("type", "Failure")
                message = failure.get("message", failure_type)
                digest = hashlib.sha256(f"{operation}\0{check}\0{failure_type}\0{message}".encode()).hexdigest()[:16]
                interaction = interactions.get(case_id, {})
                request = interaction.get("request")
                response = interaction.get("response")
                sanitized_request = self.sanitizer.sanitize(request)
                sanitized_response = self.sanitizer.sanitize(response)
                output.append(
                    {
                        "failure_id": digest,
                        "operation": operation,
                        "check": check,
                        "title": failure_type,
                        "message": message,
                        "request": sanitized_request,
                        "response": sanitized_response,
                        "curl": self._curl(sanitized_request),
                        "case": cases.get(case_id, {}).get("value"),
                        "related_cases": self._case_chain(case_id, cases),
                    }
                )
        return output

    @staticmethod
    def _case_chain(case_id: str, cases: dict[str, Any]) -> list[dict[str, Any]]:
        chain = []
        current: str | None = case_id
        seen = set()
        while current is not None and current not in seen:
            seen.add(current)
            node = cases.get(current)
            if node is None:
                break
            value = node.get("value")
            if value is not None:
                chain.append(value)
            current = node.get("parent_id")
        chain.reverse()
        return chain

    @staticmethod
    def _curl(request: dict[str, Any] | None) -> str | None:
        if not request:
            return None
        command = ["curl", "-X", request.get("method", "GET")]
        for name, values in request.get("headers", {}).items():
            value = values[0] if isinstance(values, list) and values else values
            command.extend(["-H", f"{name}: {value}"])
        body = request.get("body")
        if isinstance(body, dict) and "$base64" in body:
            try:
                body = base64.b64decode(body["$base64"]).decode("utf-8", "replace")
            except (ValueError, TypeError):
                body = json.dumps(body)
        if body is not None:
            command.extend(["--data-binary", str(body)])
        command.append(request.get("uri", ""))
        return shlex.join(command)
