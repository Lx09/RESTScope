from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from schemathesis_mcp.models import FileSchema, RunRequest, SchemaInput, UrlSchema
from schemathesis_mcp.projector import NdjsonProjector
from schemathesis_mcp.security import PathPolicy, Sanitizer, TargetPolicy


class CliCompatibilityError(RuntimeError):
    pass


@dataclass(frozen=True)
class SchemaSnapshot:
    cli_location: str
    metadata: dict[str, Any]
    sha256: str | None


class SchemaSnapshotter:
    def __init__(self, path_policy: PathPolicy, target_policy: TargetPolicy) -> None:
        self.path_policy = path_policy
        self.target_policy = target_policy

    def snapshot(self, schema: SchemaInput, run_dir: Path) -> SchemaSnapshot:
        if isinstance(schema, UrlSchema):
            self.target_policy.validate(schema.url)
            return SchemaSnapshot(schema.url, {"kind": "url", "url": schema.url}, None)
        if isinstance(schema, FileSchema):
            source = self.path_policy.validate(schema.path)
            suffix = source.suffix.lower()
            if suffix not in {".yaml", ".yml", ".json", ".graphql", ".gql"}:
                suffix = ".yaml"
            content = source.read_bytes()
            target = run_dir / f"schema{suffix}"
            self._write_private(target, content)
            digest = hashlib.sha256(content).hexdigest()
            return SchemaSnapshot(
                str(target),
                {"kind": "file", "source": str(source), "snapshot": target.name, "sha256": digest},
                digest,
            )
        suffix = ".json" if schema.format == "json" else ".yaml"
        content = schema.content.encode()
        target = run_dir / f"schema{suffix}"
        self._write_private(target, content)
        digest = hashlib.sha256(content).hexdigest()
        return SchemaSnapshot(
            str(target),
            {"kind": "inline", "format": schema.format, "snapshot": target.name, "sha256": digest},
            digest,
        )

    @staticmethod
    def _write_private(path: Path, content: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)


@dataclass(frozen=True)
class PreparedCommand:
    argv: list[str]
    display_command: str
    config_path: Path
    raw_ndjson_path: Path
    artifact_paths: dict[str, Path]


class CommandBuilder:
    def __init__(self, command: list[str], sanitizer: Sanitizer | None = None) -> None:
        self.command = command
        self.sanitizer = sanitizer or Sanitizer()

    def prepare(self, request: RunRequest, schema_location: str, run_dir: Path) -> PreparedCommand:
        config_path = run_dir / "schemathesis.toml"
        config_path.write_text(self._config_text(request), encoding="utf-8")
        config_path.chmod(0o600)
        raw_ndjson = run_dir / "schemathesis.ndjson"
        argv = [*self.command, "run"]
        if request.base_url is not None:
            argv.append(f"--url={request.base_url}")
        if request.phases:
            argv.append(f"--phases={','.join(request.phases)}")
        if request.checks:
            argv.append(f"--checks={','.join(request.checks)}")
        if request.generation_modes:
            mode = request.generation_modes[0] if len(request.generation_modes) == 1 else "all"
            argv.append(f"--mode={mode}")
        if request.workers is not None:
            argv.append(f"--workers={request.workers}")
        if request.max_examples is not None:
            argv.append(f"--max-examples={request.max_examples}")
        if request.max_failures is not None:
            argv.append(f"--max-failures={request.max_failures}")
        if request.seed is not None:
            argv.append(f"--seed={request.seed}")
        argv.append(f"--tls-verify={'true' if request.tls_verify else 'false'}")
        argv.extend(self._filter_args("include", request.include))
        argv.extend(self._filter_args("exclude", request.exclude))
        formats = ["ndjson", *request.reports]
        argv.extend(
            [
                f"--report={','.join(formats)}",
                f"--report-ndjson-path={raw_ndjson}",
                "--output-sanitize=true",
                "--no-color",
            ]
        )
        artifacts: dict[str, Path] = {"schemathesis_ndjson": raw_ndjson}
        report_flags = {
            "junit": ("--report-junit-path", "junit.xml"),
            "har": ("--report-har-path", "har.json"),
            "vcr": ("--report-vcr-path", "vcr.yaml"),
            "allure": ("--report-allure-path", "allure"),
        }
        for report in request.reports:
            flag, filename = report_flags[report]
            path = run_dir / filename
            argv.append(f"{flag}={path}")
            artifacts[report] = path
        argv.append(schema_location)
        return PreparedCommand(
            argv=argv,
            display_command=shlex.join(self._display_argv(argv)),
            config_path=config_path,
            raw_ndjson_path=raw_ndjson,
            artifact_paths=artifacts,
        )

    @staticmethod
    def _config_text(request: RunRequest) -> str:
        if not request.headers:
            return ""
        entries = ", ".join(f"{json.dumps(key)} = {json.dumps(value)}" for key, value in request.headers.items())
        return f"headers = {{ {entries} }}\n"

    @staticmethod
    def _filter_args(prefix: str, filters: dict[str, Any]) -> list[str]:
        output = []
        for name, value in filters.items():
            values = value if isinstance(value, list) else [value]
            output.extend(f"--{prefix}-{name.replace('_', '-')}={item}" for item in values)
        return output

    def _display_argv(self, argv: list[str]) -> list[str]:
        output = []
        for item in argv:
            if item.startswith("--url="):
                output.append(f"--url={self.sanitizer.sanitize_url(item.split('=', 1)[1])}")
            elif item.startswith(("http://", "https://")):
                output.append(self.sanitizer.sanitize_url(item))
            else:
                output.append(item)
        return output


class CliBackend:
    REQUIRED_FLAGS = ("--report-ndjson-path", "--output-sanitize")

    def __init__(
        self,
        command: list[str] | None = None,
        *,
        sanitizer: Sanitizer | None = None,
        path_policy: PathPolicy | None = None,
        target_policy: TargetPolicy | None = None,
        terminate_grace_seconds: float = 5.0,
    ) -> None:
        self.command = command or self._command_from_env()
        self.sanitizer = sanitizer or Sanitizer()
        self.target_policy = target_policy or TargetPolicy.from_env()
        self.snapshotter = SchemaSnapshotter(
            path_policy or PathPolicy.from_env(),
            self.target_policy,
        )
        self.builder = CommandBuilder(self.command, self.sanitizer)
        self.projector = NdjsonProjector(self.sanitizer)
        self.terminate_grace_seconds = terminate_grace_seconds
        self._run_dirs: dict[str, Path] = {}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._lock = threading.RLock()
        self._probe: dict[str, Any] | None = None

    @staticmethod
    def _command_from_env() -> list[str]:
        override = os.getenv("SCHEMATHESIS_CLI")
        return shlex.split(override) if override else [sys.executable, "-m", "schemathesis.cli"]

    def configure_run(self, run_id: str, run_dir: Path) -> None:
        self._run_dirs[run_id] = run_dir

    def probe(self) -> dict[str, Any]:
        if self._probe is not None:
            return self._probe
        version = subprocess.run([*self.command, "--version"], check=False, capture_output=True, text=True, timeout=15)
        help_result = subprocess.run(
            [*self.command, "run", "--help"], check=False, capture_output=True, text=True, timeout=15
        )
        help_text = f"{help_result.stdout}\n{help_result.stderr}"
        missing = [flag for flag in self.REQUIRED_FLAGS if flag not in help_text]
        if version.returncode != 0 or help_result.returncode != 0 or missing:
            details = f"; missing flags: {', '.join(missing)}" if missing else ""
            raise CliCompatibilityError(f"Unsupported Schemathesis CLI{details}")
        self._probe = {"version": version.stdout.strip(), "help": help_text}
        return self._probe

    def execute(self, run_id: str, request: RunRequest, stop_event: threading.Event):
        probe = self.probe()
        run_dir = self._run_dirs[run_id]
        run_dir.chmod(0o700)
        if request.base_url is not None:
            self.target_policy.validate(request.base_url)
        snapshot = self.snapshotter.snapshot(request.schema_input, run_dir)
        safe_schema_metadata = self.sanitizer.sanitize(snapshot.metadata)
        metadata_path = run_dir / "schema.json"
        metadata_path.write_text(json.dumps(safe_schema_metadata, indent=2), encoding="utf-8")
        metadata_path.chmod(0o600)
        prepared = self.builder.prepare(request, snapshot.cli_location, run_dir)
        stdout_path = run_dir / "stdout.log"
        stderr_path = run_dir / "stderr.log"
        process: subprocess.Popen[bytes] | None = None
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process_kwargs: dict[str, Any] = {}
                if os.name == "nt":
                    process_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    process_kwargs["start_new_session"] = True
                process = subprocess.Popen(
                    prepared.argv,
                    cwd=run_dir,
                    env=os.environ.copy(),
                    stdout=stdout,
                    stderr=stderr,
                    **process_kwargs,
                )
                with self._lock:
                    self._processes[run_id] = process
                yield {
                    "type": "run_started",
                    "timestamp": time.time(),
                    "cli_version": probe["version"],
                    "command": prepared.display_command,
                    "schema": safe_schema_metadata,
                }
                offset = 0
                pending = ""
                while process.poll() is None:
                    if stop_event.is_set():
                        self.terminate(run_id)
                    offset, pending, records = self._read_records(prepared.raw_ndjson_path, offset, pending)
                    for record in records:
                        yield from self.projector.project(record)
                    time.sleep(0.01)
                offset, pending, records = self._read_records(prepared.raw_ndjson_path, offset, pending, final=True)
                for record in records:
                    yield from self.projector.project(record)
        finally:
            if process is not None and process.poll() is None:
                self.terminate(run_id)
            with self._lock:
                self._processes.pop(run_id, None)
            prepared.config_path.unlink(missing_ok=True)
        assert process is not None
        exit_code = process.returncode
        cancelled = stop_event.is_set()
        if cancelled:
            outcome = "interrupted"
        elif exit_code == 0:
            outcome = "passed"
        elif exit_code == 1:
            outcome = "failed"
        else:
            outcome = "errored"
        stop_reason = "interrupted" if cancelled else "completed" if exit_code in {0, 1} else "cli_error"
        yield {
            "type": "run_finished",
            "timestamp": time.time(),
            "outcome": outcome,
            "stop_reason": stop_reason,
            "exit_code": exit_code,
            "cli_version": probe["version"],
            "command": prepared.display_command,
            "schema": safe_schema_metadata,
            "artifacts": {name: path.name for name, path in prepared.artifact_paths.items() if path.exists()},
        }

    def terminate(self, run_id: str) -> None:
        with self._lock:
            process = self._processes.get(run_id)
        if process is None or process.poll() is not None:
            return
        if os.name == "nt":
            process.terminate()
        else:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        try:
            process.wait(timeout=self.terminate_grace_seconds)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)

    @staticmethod
    def _read_records(
        path: Path, offset: int, pending: str, *, final: bool = False
    ) -> tuple[int, str, list[dict[str, Any]]]:
        if not path.exists():
            return offset, pending, []
        with path.open("r", encoding="utf-8") as stream:
            stream.seek(offset)
            chunk = stream.read()
            offset = stream.tell()
        lines = (pending + chunk).splitlines(keepends=True)
        pending = ""
        if lines and not lines[-1].endswith(("\n", "\r")) and not final:
            pending = lines.pop()
        records = []
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                if not (final and index == len(lines) - 1 and not line.endswith(("\n", "\r"))):
                    raise
        pending = "" if final else pending
        return offset, pending, records
