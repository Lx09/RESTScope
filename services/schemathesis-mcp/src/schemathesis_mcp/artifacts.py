from __future__ import annotations

import json
import shutil
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from schemathesis_mcp.models import EventEntry, EventPage, FailureDetail, RunResult


class CursorExpired(ValueError):
    def __init__(self, artifact_uri: str) -> None:
        super().__init__("The requested event cursor is no longer available in memory")
        self.artifact_uri = artifact_uri


@dataclass
class _RunArtifacts:
    events: deque[EventEntry]
    next_cursor: int = 0


class ArtifactStore:
    def __init__(self, root: Path, max_events: int = 1_000) -> None:
        self.root = Path(root)
        self.max_events = max_events
        self._runs: dict[str, _RunArtifacts] = {}
        self._lock = threading.RLock()

    def create_run(self, run_id: str) -> None:
        with self._lock:
            (self.root / run_id / "failures").mkdir(parents=True, exist_ok=True)
            self._runs[run_id] = _RunArtifacts(events=deque(maxlen=self.max_events))

    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def append_event(self, run_id: str, payload: dict[str, Any]) -> EventEntry:
        with self._lock:
            run = self._runs[run_id]
            entry = EventEntry(cursor=run.next_cursor, payload=payload)
            run.next_cursor += 1
            run.events.append(entry)
            path = self.root / run_id / "events.ndjson"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry.model_dump(mode="json"), separators=(",", ":")))
                stream.write("\n")
            return entry

    def get_events(self, run_id: str, cursor: int = 0, limit: int = 100) -> EventPage:
        with self._lock:
            run = self._runs[run_id]
            oldest = run.events[0].cursor if run.events else run.next_cursor
            if cursor < oldest:
                raise CursorExpired(self.events_uri(run_id))
            events = [entry for entry in run.events if entry.cursor >= cursor][: max(1, min(limit, 500))]
            next_cursor = events[-1].cursor + 1 if events else cursor
            return EventPage(events=events, next_cursor=next_cursor, artifact_uri=self.events_uri(run_id))

    def write_failure(self, run_id: str, failure: FailureDetail) -> str:
        path = self.root / run_id / "failures" / f"{failure.failure_id}.json"
        path.write_text(failure.model_dump_json(indent=2), encoding="utf-8")
        return f"schemathesis://runs/{run_id}/failures/{failure.failure_id}.json"

    def read_failure(self, run_id: str, failure_id: str) -> FailureDetail:
        path = self.root / run_id / "failures" / f"{failure_id}.json"
        return FailureDetail.model_validate_json(path.read_text(encoding="utf-8"))

    def write_result(self, result: RunResult) -> None:
        path = self.root / result.run_id / "result.json"
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    def read_result(self, run_id: str) -> RunResult:
        return RunResult.model_validate_json((self.root / run_id / "result.json").read_text(encoding="utf-8"))

    def read_resource(self, uri: str) -> str:
        prefix = "schemathesis://runs/"
        if not uri.startswith(prefix):
            raise ValueError(f"Unsupported resource URI: {uri}")
        relative = uri.removeprefix(prefix)
        path = (self.root / relative).resolve()
        root = self.root.resolve()
        if root not in path.parents:
            raise ValueError("Resource path escapes the artifact directory")
        return path.read_text(encoding="utf-8")

    @staticmethod
    def events_uri(run_id: str) -> str:
        return f"schemathesis://runs/{run_id}/events.ndjson"

    def artifact_uris(self, run_id: str) -> dict[str, str]:
        output = {"events": self.events_uri(run_id)}
        for name, filename in (("junit", "junit.xml"), ("har", "har.json")):
            if (self.root / run_id / filename).exists():
                output[name] = f"schemathesis://runs/{run_id}/{filename}"
        return output

    def expire_run(self, run_id: str) -> None:
        with self._lock:
            self._runs.pop(run_id, None)
            shutil.rmtree(self.root / run_id, ignore_errors=True)
