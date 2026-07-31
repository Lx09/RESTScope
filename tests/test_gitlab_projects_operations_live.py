"""Opt-in live acceptance for the five GitLab Projects Smoke operations.

The test loads the real GitLab OpenAPI document, narrows the App-lifetime IR to
the collection and item operations for ``/api/v4/projects``, and sends generated
requests to the disposable local ``gitlab-test`` container. DeepSeek and
Phoenix remain real boundaries. Passing means every operation completed at
least one ten-case Batch without an unsupported or technical terminal result;
individual operations do not need to reach the normal 80% Smoke threshold.

This test can create, update, and delete projects. It stays skipped unless
``RUN_GITLAB_PROJECTS_FIVE_E2E=1`` is set. Authentication is acquired from the
disposable container at runtime and never written to report or trace artifacts.
The caller must enforce the approved ten-minute process deadline when invoking
pytest, so a wedged provider or target cannot leave the acceptance run alive.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace
from datetime import UTC, datetime
from html import unescape
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GITLAB_CONTAINER = "gitlab-test"
GITLAB_WEB_URL = "http://127.0.0.1:7077"
PHOENIX_URL = "http://127.0.0.1:6006"
EXPECTED_CASES_PER_BATCH = 10
LIVE_OPERATION_KEYS = (
    "GET /api/v4/projects",
    "POST /api/v4/projects",
    "GET /api/v4/projects/{id}",
    "PUT /api/v4/projects/{id}",
    "DELETE /api/v4/projects/{id}",
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.live_e2e,
    pytest.mark.skipif(
        os.environ.get("RUN_GITLAB_PROJECTS_FIVE_E2E") != "1",
        reason=(
            "Set RUN_GITLAB_PROJECTS_FIVE_E2E=1 for the destructive, "
            "DeepSeek-backed GitLab Projects acceptance run."
        ),
    ),
]


def _initial_root_password() -> str:
    """Read the disposable container password without logging or persisting it."""

    result = subprocess.run(
        [
            "docker",
            "exec",
            GITLAB_CONTAINER,
            "sh",
            "-lc",
            "sed -n 's/^Password: //p' /etc/gitlab/initial_root_password",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    password = result.stdout.strip()
    if not password:
        raise RuntimeError("GitLab initial root password is unavailable")
    return password


def _gitlab_auth_headers() -> dict[str, str]:
    """Create trusted session and CSRF headers for reads and mutations."""

    password = _initial_root_password()
    with httpx.Client(
        base_url=GITLAB_WEB_URL,
        follow_redirects=True,
        timeout=30,
        trust_env=False,
    ) as client:
        sign_in = client.get("/users/sign_in")
        sign_in.raise_for_status()
        token_match = re.search(
            r'name="authenticity_token" value="([^"]+)"',
            sign_in.text,
        )
        if token_match is None:
            raise RuntimeError("GitLab sign-in token was not found")
        signed_in = client.post(
            "/users/sign_in",
            data={
                "authenticity_token": unescape(token_match.group(1)),
                "user[login]": "root",
                "user[password]": password,
                "user[remember_me]": "0",
            },
        )
        signed_in.raise_for_status()
        identity = client.get("/api/v4/user")
        if identity.status_code != 200:
            raise RuntimeError("GitLab root session did not authenticate the API")
        csrf_match = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            signed_in.text,
        )
        if csrf_match is None:
            raise RuntimeError("GitLab authenticated CSRF token was not found")
        return {
            "Cookie": "; ".join(
                f"{name}={value}" for name, value in client.cookies.items()
            ),
            "X-CSRF-Token": unescape(csrf_match.group(1)),
        }


def _default_env_file() -> Path:
    """Find the ignored model configuration from a checkout or its main tree."""

    checkout_env = PROJECT_ROOT / ".env"
    if checkout_env.is_file():
        return checkout_env
    return PROJECT_ROOT.parents[1] / ".env"


def _write_json(path: Path, value: Any) -> None:
    """Write readable live evidence after authentication has been excluded."""

    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _all_phoenix_spans(project_name: str) -> list[dict[str, Any]]:
    """Read every span page for this run's unique Phoenix project."""

    endpoint = (
        f"{PHOENIX_URL}/v1/projects/"
        f"{quote(project_name, safe='')}/spans"
    )
    spans: list[dict[str, Any]] = []
    cursor: str | None = None
    seen: set[str] = set()
    for _page in range(100):
        params: dict[str, str | int] = {"limit": 1000}
        if cursor is not None:
            params["cursor"] = cursor
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.get(endpoint, params=params)
            response.raise_for_status()
            payload = response.json()
        spans.extend(payload.get("data", []))
        cursor = payload.get("next_cursor")
        if cursor is None:
            return spans
        if cursor in seen:
            raise AssertionError("Phoenix returned a repeating span cursor")
        seen.add(cursor)
    raise AssertionError("Phoenix span pagination exceeded 100 pages")


def _wait_for_phoenix_spans(project_name: str) -> list[dict[str, Any]]:
    """Wait for the batched exporter to expose the completed App trace."""

    deadline = time.monotonic() + 60
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            spans = _all_phoenix_spans(project_name)
            if any(span["name"] == "RESTScopeApp.run" for span in spans):
                return spans
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(0.5)
    raise AssertionError(
        "Phoenix did not expose the GitLab Projects trace within 60 seconds"
    ) from last_error


def _coverage(report: Any) -> list[dict[str, Any]]:
    """Summarize Batch evidence by operation without copying response bodies."""

    attempts: dict[str, list[Any]] = defaultdict(list)
    for attempt in report.attempts:
        key = (
            f"{attempt.operation.method.upper()} "
            f"{attempt.operation.path}"
        )
        attempts[key].append(attempt)

    rows: list[dict[str, Any]] = []
    for operation_key in LIVE_OPERATION_KEYS:
        operation_attempts = attempts.get(operation_key, [])
        batches = [
            batch
            for attempt in operation_attempts
            for batch in attempt.smoke_result.batch_reports
        ]
        rows.append(
            {
                "operation_key": operation_key,
                "attempt_count": len(operation_attempts),
                "batch_count": len(batches),
                "case_count": sum(len(batch.cases) for batch in batches),
                "statuses": [
                    attempt.smoke_result.status
                    for attempt in operation_attempts
                ],
                "failure_kinds": [
                    attempt.smoke_result.failure_kind
                    for attempt in operation_attempts
                    if attempt.smoke_result.failure_kind is not None
                ],
            }
        )
    return rows


def test_gitlab_projects_operations_complete_batches_without_technical_errors() -> None:
    """Five real Projects operations each reach Batch execution successfully."""

    from restscope import RESTScopeApp, RESTScopeRunRequest
    from restscope.openapi_parser import OpenAPIParser
    from restscope.restscope_config import (
        DBConfig,
        RESTScopeConfig,
        TracingConfig,
    )

    spec_path = Path(
        os.environ.get(
            "RESTSCOPE_GITLAB_PROJECTS_SPEC",
            str(
                PROJECT_ROOT
                / "assets"
                / "openapi"
                / "gitlab-18.9.2-openapi.yaml"
            ),
        )
    ).expanduser()
    source_ir = OpenAPIParser.parse(spec_path)
    missing = set(LIVE_OPERATION_KEYS) - set(source_ir.operations)
    assert not missing, (
        "GitLab spec does not contain the five approved operations; set "
        f"RESTSCOPE_GITLAB_PROJECTS_SPEC to the full document: {sorted(missing)}"
    )

    run_id = (
        "gitlab-projects-five-"
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid4().hex[:8]}"
    )
    artifact_root = Path(
        os.environ.get(
            "RESTSCOPE_GITLAB_ARTIFACT_DIR",
            str(PROJECT_ROOT / "artifacts" / "gitlab-projects-five-live"),
        )
    ).expanduser()
    run_dir = artifact_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    project_name = f"restscope-{run_id}"

    config = RESTScopeConfig.from_environment(
        Path(
            os.environ.get(
                "RESTSCOPE_GITLAB_ENV_FILE",
                str(_default_env_file()),
            )
        ).expanduser()
    )
    if not config.llm.thinking.model or not config.llm.thinking.api_key:
        raise AssertionError("configured THINK/DeepSeek model is required")
    if not config.llm.fast.model or not config.llm.fast.api_key:
        raise AssertionError("configured FAST/DeepSeek model is required")
    config = replace(
        config,
        db=DBConfig(url=f"sqlite:///{run_dir / 'evidence.sqlite'}"),
        tracing=TracingConfig(
            enabled=True,
            collector_endpoint=PHOENIX_URL,
            project_name=project_name,
            protocol="http/protobuf",
            batch=True,
            max_content_bytes=65536,
            flush_timeout_seconds=10,
        ),
    )
    _write_json(
        run_dir / "run-metadata.json",
        {
            "run_id": run_id,
            "spec_path": str(spec_path),
            "target": GITLAB_WEB_URL,
            "phoenix_project": project_name,
            "operation_keys": list(LIVE_OPERATION_KEYS),
            "case_count": EXPECTED_CASES_PER_BATCH,
            "hard_timeout_seconds": 600,
            "thinking_provider": config.llm.thinking.provider,
            "thinking_model": config.llm.thinking.model,
            "fast_provider": config.llm.fast.provider,
            "fast_model": config.llm.fast.model,
        },
    )

    app = RESTScopeApp.from_config(config)
    try:
        context = app.initialize(
            schema_source={"kind": "file", "path": str(spec_path)},
            base_url=GITLAB_WEB_URL,
            headers=_gitlab_auth_headers(),
        )
        # Filtering after initialization keeps production discovery unchanged
        # while ensuring this destructive test cannot schedule another endpoint.
        context.ir.operations = {
            operation_key: context.ir.operations[operation_key]
            for operation_key in LIVE_OPERATION_KEYS
        }
        report = app.run(
            RESTScopeRunRequest(
                metadata={"task_id": run_id},
                max_operation_attempts=1,
            )
        )
    finally:
        app.close()

    _write_json(run_dir / "report.json", report.model_dump(mode="json"))
    coverage = _coverage(report)
    _write_json(run_dir / "coverage.json", coverage)
    spans = _wait_for_phoenix_spans(project_name)
    _write_json(run_dir / "phoenix-spans.json", spans)
    _write_json(
        run_dir / "phoenix-summary.json",
        {
            "span_count": len(spans),
            "span_names": dict(
                sorted(Counter(span["name"] for span in spans).items())
            ),
            "status_codes": dict(
                sorted(Counter(span["status_code"] for span in spans).items())
            ),
        },
    )
    print(f"GitLab Projects artifacts: {run_dir}")
    print(f"Phoenix project: {project_name}")

    assert report.unattempted_operations == []
    assert {row["operation_key"] for row in coverage} == set(
        LIVE_OPERATION_KEYS
    )
    assert all(row["attempt_count"] == 1 for row in coverage)
    assert all(row["batch_count"] >= 1 for row in coverage)
    assert all(
        row["case_count"] >= EXPECTED_CASES_PER_BATCH
        for row in coverage
    )
    assert all(row["statuses"] == ["passed"] for row in coverage)
    assert all(not row["failure_kinds"] for row in coverage)

    smoke_spans = [
        span
        for span in spans
        if span["name"] == "OperationSmokeCoordinator.run"
    ]
    assert len(smoke_spans) == len(LIVE_OPERATION_KEYS)
    assert all(span["status_code"] != "ERROR" for span in smoke_spans)
