"""Protect the installed RESTScope command and its secret-safe exit behavior."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner


def _openapi_file(tmp_path: Path) -> Path:
    """Write one minimal testable OpenAPI document for command scenarios."""
    path = tmp_path / "openapi.json"
    path.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "CLI", "version": "1"},
                "paths": {
                    "/health": {
                        "get": {"responses": {"200": {"description": "ok"}}}
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_command_help_documents_the_complete_target_interface() -> None:
    """Users can discover target, config, and secret-history behavior."""
    from restscope.main import main

    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "OPENAPI_FILE" in result.output
    assert "--base-url" in result.output
    assert "--env-file" in result.output
    assert "--header NAME VALUE" in result.output
    assert "shell history" in result.output


def test_command_passes_validated_inputs_and_closes_the_app(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """One successful command owns the App from construction through close."""
    from restscope import main as main_module

    events: list[object] = []

    class App:
        """Record only the public lifecycle used by the command."""

        ui_url = "http://127.0.0.1:8765"

        @classmethod
        def from_environment(cls, *, env_file: Path | None = None) -> "App":
            events.append(("construct", env_file))
            return cls()

        def initialize(self, **arguments: object) -> None:
            events.append(("initialize", arguments))

        def start(self) -> None:
            events.append("start")

        def close(self) -> None:
            events.append("close")

    monkeypatch.setattr(main_module, "RESTScopeApp", App)
    schema = _openapi_file(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("UI_ENABLED=false\n", encoding="utf-8")

    result = CliRunner().invoke(
        main_module.main,
        [
            str(schema),
            "--base-url",
            "https://api.example.test",
            "--env-file",
            str(env_file),
            "--header",
            "Authorization",
            "Bearer test-secret",
        ],
    )

    assert result.exit_code == 0
    assert events == [
        ("construct", env_file),
        (
            "initialize",
            {
                "schema_source": {"kind": "file", "path": str(schema)},
                "base_url": "https://api.example.test",
                "headers": {"Authorization": "Bearer test-secret"},
            },
        ),
        "start",
        "close",
    ]
    assert "http://127.0.0.1:8765" in result.output
    assert "test-secret" not in result.output


def test_command_rejects_duplicate_headers_without_constructing_app(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Case-insensitive duplicates fail as input errors before runtime mutation."""
    from restscope import main as main_module

    monkeypatch.setattr(
        main_module.RESTScopeApp,
        "from_environment",
        lambda **_arguments: (_ for _ in ()).throw(AssertionError("constructed")),
    )
    schema = _openapi_file(tmp_path)

    result = CliRunner().invoke(
        main_module.main,
        [
            str(schema),
            "--base-url",
            "https://api.example.test",
            "--header",
            "Authorization",
            "first",
            "--header",
            "authorization",
            "second",
        ],
    )

    assert result.exit_code == 2
    assert "duplicate" in result.output.lower()
    assert "first" not in result.output
    assert "second" not in result.output


def test_command_rejects_unsafe_target_inputs_before_constructing_app(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Malformed origins and headers are Click input errors without App effects."""
    from restscope import main as main_module

    monkeypatch.setattr(
        main_module.RESTScopeApp,
        "from_environment",
        lambda **_arguments: (_ for _ in ()).throw(AssertionError("constructed")),
    )
    schema = _openapi_file(tmp_path)
    scenarios = (
        ([str(schema), "--base-url", "https://user:secret@example.test"], "base-url"),
        ([str(schema), "--base-url", "https://example.test/api"], "base-url"),
        (
            [
                str(schema),
                "--base-url",
                "https://api.example.test",
                "--header",
                "Bad Name",
                "value",
            ],
            "HTTP tokens",
        ),
        (
            [
                str(schema),
                "--base-url",
                "https://api.example.test",
                "--header",
                "X-Test",
                "line\nbreak",
            ],
            "line breaks",
        ),
    )

    for arguments, message in scenarios:
        result = CliRunner().invoke(main_module.main, arguments)
        assert result.exit_code == 2
        assert message in result.output


def test_command_maps_interrupt_and_runtime_failure_to_safe_exit_codes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """Both terminal outcomes close the App without leaking exception text."""
    from restscope import main as main_module

    schema = _openapi_file(tmp_path)

    for failure, expected_code in (
        (KeyboardInterrupt(), 130),
        (RuntimeError("Bearer should-never-render"), 1),
    ):
        closed: list[bool] = []

        class App:
            """Raise the selected terminal result from the public start seam."""

            ui_url = None

            @classmethod
            def from_environment(cls, **_arguments: object) -> "App":
                return cls()

            def initialize(self, **_arguments: object) -> None:
                return None

            def start(self) -> None:
                raise failure

            def close(self) -> None:
                closed.append(True)
                if expected_code == 1:
                    # Cleanup cannot replace the command's already-selected
                    # runtime failure code, even when cleanup is interrupted.
                    raise KeyboardInterrupt

        monkeypatch.setattr(main_module, "RESTScopeApp", App)
        result = CliRunner().invoke(
            main_module.main,
            [str(schema), "--base-url", "https://api.example.test"],
        )

        assert result.exit_code == expected_code
        assert closed == [True]
        assert "should-never-render" not in result.output
        assert "Traceback" not in result.output


def test_command_reports_cleanup_failure_after_success_without_details(
    monkeypatch,
    tmp_path: Path,
) -> None:
    """A failed close changes successful completion to one safe runtime error."""
    from restscope import main as main_module

    class App:
        """Complete the run, then fail at the final owned cleanup boundary."""

        ui_url = None

        @classmethod
        def from_environment(cls, **_arguments: object) -> "App":
            return cls()

        def initialize(self, **_arguments: object) -> None:
            return None

        def start(self) -> None:
            return None

        def close(self) -> None:
            raise RuntimeError("Bearer cleanup-secret")

    monkeypatch.setattr(main_module, "RESTScopeApp", App)
    result = CliRunner().invoke(
        main_module.main,
        [str(_openapi_file(tmp_path)), "--base-url", "https://api.example.test"],
    )

    assert result.exit_code == 1
    assert "could not close" in result.output
    assert "cleanup-secret" not in result.output
    assert "Traceback" not in result.output
