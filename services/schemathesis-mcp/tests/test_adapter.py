from __future__ import annotations

import stat
import sys
import threading
import time
from pathlib import Path

import pytest

from schemathesis_mcp.adapter import CliBackend, CliCompatibilityError, CommandBuilder, SchemaSnapshotter
from schemathesis_mcp.models import FileSchema, InlineSchema, RunRequest, UrlSchema
from schemathesis_mcp.security import PathNotAllowed, PathPolicy, TargetNotAllowed, TargetPolicy


def test_file_schema_is_copied_to_private_run_snapshot(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "openapi.yaml"
    source.write_text("openapi: 3.0.0\ninfo: {title: Demo, version: '1'}\npaths: {}\n")
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    snapshotter = SchemaSnapshotter(PathPolicy([workspace]), TargetPolicy())

    snapshot = snapshotter.snapshot(FileSchema(kind="file", path=str(source)), run_dir)

    assert snapshot.cli_location == str(run_dir / "schema.yaml")
    assert (run_dir / "schema.yaml").read_text() == source.read_text()
    assert snapshot.sha256
    assert stat.S_IMODE((run_dir / "schema.yaml").stat().st_mode) == 0o600


def test_inline_schema_is_written_with_declared_format(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    snapshotter = SchemaSnapshotter(PathPolicy([tmp_path]), TargetPolicy())

    snapshot = snapshotter.snapshot(
        InlineSchema(kind="inline", format="json", content='{"openapi":"3.0.0"}'),
        run_dir,
    )

    assert snapshot.cli_location.endswith("schema.json")
    assert Path(snapshot.cli_location).read_text() == '{"openapi":"3.0.0"}'


def test_schema_security_policies_reject_unapproved_paths_and_hosts(tmp_path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    snapshotter = SchemaSnapshotter(
        PathPolicy([tmp_path / "allowed"]),
        TargetPolicy({"api.example.com"}),
    )

    with pytest.raises(PathNotAllowed):
        snapshotter.snapshot(FileSchema(kind="file", path="/etc/passwd"), run_dir)
    with pytest.raises(TargetNotAllowed):
        snapshotter.snapshot(UrlSchema(kind="url", url="https://other.example/openapi.json"), run_dir)


def test_command_builder_uses_config_file_and_requested_reports(tmp_path) -> None:
    request = RunRequest(
        schema={"kind": "url", "url": "https://api.example/openapi.json"},
        base_url="https://api.example",
        headers={"Authorization": "Bearer secret"},
        phases=["coverage", "fuzzing"],
        checks=["not_a_server_error"],
        generation_modes=["positive", "negative"],
        include={"path": "/users", "method": "GET"},
        workers=2,
        max_examples=5,
        max_failures=3,
        seed=42,
        tls_verify=False,
        reports=["junit", "har"],
    )
    builder = CommandBuilder([sys.executable, "-m", "schemathesis.cli"])

    prepared = builder.prepare(
        request=request,
        schema_location="https://api.example/openapi.json",
        run_dir=tmp_path,
    )

    assert prepared.argv[:4] == [sys.executable, "-m", "schemathesis.cli", "run"]
    assert prepared.argv[-1] == "https://api.example/openapi.json"
    assert "--report=ndjson,junit,har" in prepared.argv
    assert f"--report-ndjson-path={tmp_path / 'schemathesis.ndjson'}" in prepared.argv
    assert "--phases=coverage,fuzzing" in prepared.argv
    assert "--mode=all" in prepared.argv
    assert "--include-path=/users" in prepared.argv
    assert "--include-method=GET" in prepared.argv
    assert "secret" not in " ".join(prepared.argv)
    assert "Authorization" in prepared.config_path.read_text()
    assert stat.S_IMODE(prepared.config_path.stat().st_mode) == 0o600


def _write_fake_cli(path: Path) -> None:
    path.write_text(
        """
import json
import os
import pathlib
import signal
import sys
import time

if "--version" in sys.argv:
    print("schemathesis 4.99.0")
    raise SystemExit(0)
if "run" in sys.argv and "--help" in sys.argv:
    print("--report --report-ndjson-path --output-sanitize --phases --max-examples")
    raise SystemExit(0)

ndjson_arg = next(item for item in sys.argv if item.startswith("--report-ndjson-path="))
path = pathlib.Path(ndjson_arg.split("=", 1)[1])
records = [
    {"Initialize": {"command": "schemathesis run [REDACTED]", "schemathesis_version": "4.99.0", "seed": 1}},
    {"EngineStarted": {"id": "1", "timestamp": 1.0}},
    {"EngineFinished": {"id": "2", "timestamp": 2.0, "running_time": 1.0, "stop_reason": "completed"}},
]
with path.open("w") as stream:
    for record in records:
        stream.write(json.dumps(record) + "\\n")
        stream.flush()
        time.sleep(0.01)
print("fake stdout")
print("fake stderr", file=sys.stderr)
raise SystemExit(int(os.environ.get("FAKE_EXIT_CODE", "0")))
""".strip()
    )


@pytest.mark.parametrize(
    ("exit_code", "expected_outcome"),
    [(0, "passed"), (1, "failed"), (2, "errored")],
)
def test_cli_backend_streams_ndjson_and_maps_exit_codes(tmp_path, monkeypatch, exit_code, expected_outcome) -> None:
    fake_cli = tmp_path / "fake_cli.py"
    _write_fake_cli(fake_cli)
    monkeypatch.setenv("FAKE_EXIT_CODE", str(exit_code))
    backend = CliBackend(command=[sys.executable, str(fake_cli)])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    backend.configure_run("run-1", run_dir)
    request = RunRequest(schema={"kind": "inline", "format": "yaml", "content": "openapi: 3.0.0"})

    events = list(backend.execute("run-1", request, threading.Event()))

    assert events[-1]["type"] == "run_finished"
    assert events[-1]["outcome"] == expected_outcome
    assert events[-1]["exit_code"] == exit_code
    assert (run_dir / "stdout.log").read_text().strip() == "fake stdout"
    assert (run_dir / "stderr.log").read_text().strip() == "fake stderr"


def test_cli_backend_rejects_cli_without_required_ndjson_flags(tmp_path) -> None:
    fake_cli = tmp_path / "bad_cli.py"
    fake_cli.write_text("import sys\nprint('schemathesis 4.0.0' if '--version' in sys.argv else '--report')\n")

    with pytest.raises(CliCompatibilityError, match="report-ndjson-path"):
        CliBackend(command=[sys.executable, str(fake_cli)]).probe()


def test_ndjson_reader_keeps_partial_lines_and_ignores_truncated_final_record(tmp_path) -> None:
    path = tmp_path / "events.ndjson"
    path.write_text('{"EngineStarted":{"timestamp":1')

    offset, pending, records = CliBackend._read_records(path, 0, "")
    assert records == []
    assert pending

    with path.open("a") as stream:
        stream.write('.0}}\n{"truncated":')
    offset, pending, records = CliBackend._read_records(path, offset, pending)
    assert records == [{"EngineStarted": {"timestamp": 1.0}}]

    _, _, records = CliBackend._read_records(path, offset, pending, final=True)
    assert records == []


def test_cli_backend_cancels_process_group_and_removes_private_config(tmp_path) -> None:
    fake_cli = tmp_path / "hanging_cli.py"
    fake_cli.write_text(
        """
import pathlib
import sys
import time

if "--version" in sys.argv:
    print("schemathesis 4.99.0")
    raise SystemExit(0)
if "--help" in sys.argv:
    print("--report-ndjson-path --output-sanitize")
    raise SystemExit(0)
path = pathlib.Path(next(item for item in sys.argv if item.startswith("--report-ndjson-path=")).split("=", 1)[1])
path.write_text('{"EngineStarted":{"timestamp":1.0}}\\n')
while True:
    time.sleep(1)
""".strip()
    )
    backend = CliBackend(command=[sys.executable, str(fake_cli)], terminate_grace_seconds=0.1)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    backend.configure_run("run-1", run_dir)
    request = RunRequest(
        schema={"kind": "inline", "format": "yaml", "content": "openapi: 3.0.0"},
        headers={"Authorization": "Bearer secret"},
    )
    stop_event = threading.Event()
    events = []

    def consume() -> None:
        events.extend(backend.execute("run-1", request, stop_event))

    thread = threading.Thread(target=consume)
    thread.start()
    deadline = time.monotonic() + 2
    while not (run_dir / "schemathesis.toml").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    stop_event.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert events[-1]["outcome"] == "interrupted"
    assert not (run_dir / "schemathesis.toml").exists()
