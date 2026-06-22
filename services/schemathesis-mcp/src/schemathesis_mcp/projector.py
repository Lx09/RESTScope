from __future__ import annotations

import re
from typing import Any

from schemathesis_mcp.security import Sanitizer


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


class EventProjector:
    def __init__(self, sanitizer: Sanitizer | None = None) -> None:
        self.sanitizer = sanitizer or Sanitizer()

    def project(self, event: Any) -> dict[str, Any]:
        event_type = _snake_case(type(event).__name__)
        payload: dict[str, Any] = {
            "type": event_type,
            "timestamp": getattr(event, "timestamp", None),
        }
        phase = getattr(event, "phase", None)
        if phase is not None:
            phase = getattr(phase, "name", phase)
            payload["phase"] = _enum_value(phase)
        if event_type == "phase_finished":
            payload["status"] = _enum_value(getattr(event, "status", None))
        elif event_type == "scenario_started":
            payload["operation"] = getattr(event, "label", None)
        elif event_type == "scenario_finished":
            payload.update(self._project_scenario(event))
        elif event_type == "non_fatal_error":
            payload.update(
                {
                    "label": getattr(event, "label", None),
                    "message": str(getattr(event, "value", "")),
                    "related_to_operation": getattr(event, "related_to_operation", False),
                }
            )
        elif event_type == "fatal_error":
            payload["message"] = str(getattr(event, "exception", ""))
        elif event_type == "engine_finished":
            payload.update(
                {
                    "stop_reason": _enum_value(getattr(event, "stop_reason", None)),
                    "running_time": getattr(event, "running_time", None),
                    "after_run_failures": len(getattr(event, "failures", [])),
                }
            )
        return self.sanitizer.sanitize(payload)

    def _project_scenario(self, event: Any) -> dict[str, Any]:
        recorder = getattr(event, "recorder", None)
        checks = getattr(recorder, "checks", {}) if recorder is not None else {}
        failures = sum(
            1 for nodes in checks.values() for node in nodes if _enum_value(getattr(node, "status", None)) == "failure"
        )
        return {
            "operation": getattr(event, "label", None) or getattr(recorder, "label", None),
            "status": _enum_value(getattr(event, "status", None)),
            "elapsed_time": getattr(event, "elapsed_time", None),
            "failures": failures,
            "is_final": getattr(event, "is_final", False),
        }
