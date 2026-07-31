"""Live acceptance loop for only GitLab's ``POST /projects`` operation.

This opt-in test is intentionally separate from the offline suite because it
creates real projects in the local ``gitlab-test`` container and exports traces
to the local Phoenix service. Configured DeepSeek models are available if a
failed Batch reaches an Agent, but the successful first-Batch path deliberately
calls no LLM. It uses a one-operation OpenAPI document so neither the Supervisor
nor an Agent can schedule a different GitLab endpoint. Successful projects are
left in the disposable container because cleanup would exercise a second API
operation outside this test's authorization boundary.

Authentication stays outside model-visible data.  The test signs in with the
container's initial root password, keeps the resulting session Cookie and CSRF
token in memory, and passes both as trusted App headers.  Neither credential is
written to the artifact directory.
"""

from __future__ import annotations

from collections import Counter
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
GITLAB_API_URL = f"{GITLAB_WEB_URL}/api/v4"
PHOENIX_URL = "http://127.0.0.1:6006"
OPERATION_KEY = "POST /projects"

# The GitLab distribution's bundled OpenAPI description does not currently
# contain project creation.  This focused contract describes only the real
# endpoint under test and only the request fields needed to create a project.
GITLAB_CREATE_PROJECT_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Focused GitLab project creation",
        "version": "18.9.2",
    },
    "paths": {
        "/projects": {
            "post": {
                "operationId": "createProject",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name"],
                                "additionalProperties": False,
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "minLength": 12,
                                        "maxLength": 20,
                                    },
                                    "visibility": {
                                        "type": "string",
                                        "enum": ["private"],
                                    },
                                },
                            }
                        }
                    },
                },
                "responses": {
                    "201": {
                        "description": "Project created",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["id", "name"],
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "name": {"type": "string"},
                                        "path_with_namespace": {
                                            "type": "string"
                                        },
                                    },
                                }
                            }
                        },
                    },
                    "400": {"description": "Invalid or duplicate project"},
                    "401": {"description": "Authentication required"},
                },
            }
        }
    },
}


def _initial_root_password() -> str:
    """Read the disposable container credential without printing or persisting it."""
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
    """Create the trusted Cookie and CSRF headers required by GitLab writes."""
    password = _initial_root_password()
    with httpx.Client(
        base_url=GITLAB_WEB_URL,
        follow_redirects=True,
        timeout=30,
        trust_env=False,
    ) as client:
        sign_in = client.get("/users/sign_in")
        sign_in.raise_for_status()
        match = re.search(
            r'name="authenticity_token" value="([^"]+)"',
            sign_in.text,
        )
        if match is None:
            raise RuntimeError("GitLab sign-in token was not found")
        response = client.post(
            "/users/sign_in",
            data={
                "authenticity_token": unescape(match.group(1)),
                "user[login]": "root",
                "user[password]": password,
                "user[remember_me]": "0",
            },
        )
        response.raise_for_status()
        identity = client.get("/api/v4/user")
        if identity.status_code != 200:
            raise RuntimeError(
                "GitLab root session did not authenticate the API"
            )
        # GitLab accepts the session Cookie for read APIs but protects writes
        # with the CSRF token embedded in the authenticated dashboard page.
        # Omitting this header yields 401 even though /api/v4/user succeeds.
        csrf_match = re.search(
            r'<meta name="csrf-token" content="([^"]+)"',
            response.text,
        )
        if csrf_match is None:
            raise RuntimeError("GitLab authenticated CSRF token was not found")
        return {
            "Cookie": "; ".join(
                f"{name}={value}" for name, value in client.cookies.items()
            ),
            "X-CSRF-Token": unescape(csrf_match.group(1)),
        }


def _phoenix_spans(project_name: str) -> list[dict[str, Any]]:
    """Poll the local Phoenix project until the flushed App trace is readable."""
    endpoint = (
        f"{PHOENIX_URL}/v1/projects/"
        f"{quote(project_name, safe='')}/spans"
    )
    deadline = time.monotonic() + 60
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=10, trust_env=False) as client:
                response = client.get(endpoint, params={"limit": 1000})
                response.raise_for_status()
                spans = response.json().get("data", [])
            if any(span["name"] == "RESTScopeApp.run" for span in spans):
                return spans
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(0.5)
    raise AssertionError(
        "Phoenix did not expose the flushed GitLab Smoke trace within 60 seconds"
    ) from last_error


def _write_json(path: Path, value: Any) -> None:
    """Write human-readable diagnostic evidence without credentials."""
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _default_env_file() -> Path:
    """Find the ignored local model config from main or a feature worktree.

    A normal checkout keeps ``.env`` at ``PROJECT_ROOT``. Git worktrees do not
    copy that ignored file, so this repository's ``.worktrees/<name>`` layout
    falls back to the owning main checkout two parents above.
    """
    checkout_env = PROJECT_ROOT / ".env"
    if checkout_env.is_file():
        return checkout_env
    return PROJECT_ROOT.parents[1] / ".env"


@pytest.mark.live_e2e
def test_gitlab_post_projects_reaches_the_smoke_success_threshold() -> None:
    """Scenario: one real GitLab operation reaches at least 80% Batch success."""
    if os.environ.get("RUN_GITLAB_POST_PROJECTS_SMOKE_E2E") != "1":
        pytest.skip(
            "set RUN_GITLAB_POST_PROJECTS_SMOKE_E2E=1 for the authorized live run"
        )

    from restscope import RESTScopeApp, RESTScopeRunRequest
    from restscope.restscope_config import (
        DBConfig,
        RESTScopeConfig,
        TracingConfig,
    )

    run_id = (
        "gitlab-post-projects-smoke-"
        f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-"
        f"{uuid4().hex[:8]}"
    )
    artifact_root = Path(
        os.environ.get(
            "RESTSCOPE_GITLAB_ARTIFACT_DIR",
            str(PROJECT_ROOT / "artifacts" / "gitlab-post-projects-smoke"),
        )
    ).expanduser()
    run_dir = artifact_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    project_name = f"restscope-{run_id}"
    database = run_dir / "evidence.sqlite"

    env_file = Path(
        os.environ.get(
            "RESTSCOPE_GITLAB_ENV_FILE",
            str(_default_env_file()),
        )
    ).expanduser()
    config = RESTScopeConfig.from_environment(env_file)
    if not config.llm.thinking.model or not config.llm.thinking.api_key:
        raise AssertionError("configured THINK/DeepSeek model is required")
    if not config.llm.fast.model or not config.llm.fast.api_key:
        raise AssertionError("configured FAST/DeepSeek model is required")
    config = replace(
        config,
        db=DBConfig(url=f"sqlite:///{database}"),
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

    # Metadata contains reproducibility and routing facts only.  Authentication
    # is deliberately acquired after this write and never enters an artifact.
    _write_json(
        run_dir / "run-metadata.json",
        {
            "run_id": run_id,
            "operation_key": OPERATION_KEY,
            "target": GITLAB_API_URL,
            "phoenix_project": project_name,
            "thinking_provider": config.llm.thinking.provider,
            "thinking_model": config.llm.thinking.model,
            "fast_provider": config.llm.fast.provider,
            "fast_model": config.llm.fast.model,
        },
    )

    app = RESTScopeApp.from_config(config)
    try:
        app.initialize(
            schema_source={
                "kind": "inline",
                "format": "json",
                "content": json.dumps(GITLAB_CREATE_PROJECT_SPEC),
            },
            base_url=GITLAB_API_URL,
            headers=_gitlab_auth_headers(),
        )
        report = app.run(
            RESTScopeRunRequest(
                metadata={"task_id": run_id},
                max_operation_attempts=1,
            )
        )
    finally:
        # Closing forces bounded trace export before Phoenix is queried.
        app.close()

    _write_json(run_dir / "report.json", report.model_dump(mode="json"))
    spans = _phoenix_spans(project_name)
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
    print(f"GitLab Smoke artifacts: {run_dir}")
    print(f"Phoenix project: {project_name}")

    # This is stricter than the Supervisor's general passed state. A no-Patch
    # terminal result does not satisfy the live request: the latest complete
    # Batch itself must demonstrate at least 80% success.
    assert report.operations[0].method.upper() == "POST"
    assert report.operations[0].path == "/projects"
    assert len(report.operations) == 1
    assert report.attempt_count == 1
    assert len(report.attempts) == 1
    smoke = report.attempts[0].smoke_result
    assert smoke.operation_key == OPERATION_KEY
    assert smoke.status == "passed"
    assert smoke.stop_reason == "success_rate_reached"
    assert smoke.success_rate >= smoke.required_success_rate == 0.8
    assert smoke.batch_run_ids

    names = Counter(span["name"] for span in spans)
    assert names["RESTScopeApp.run"] == 1
    assert names["RESTScopeMainGraph.run"] == 1
    assert names["RESTScopeMainGraph.operation_attempt"] == 1
    assert names["OperationSmokeCoordinator.run"] == 1
    assert names["OperationTestingService.run_smoke_batch"] >= 1
    assert names["RESTScopeTestCase.execute"] >= 10
    assert not any("Effect" in name for name in names)
