"""Tests for sanitization and target allowlist security helpers."""

import pytest

from schemathesis_mcp.security import Sanitizer, TargetNotAllowed, TargetPolicy


def test_sanitizer_redacts_headers_urls_and_nested_values() -> None:
    sanitizer = Sanitizer()
    value = {
        "headers": {
            "Authorization": "Bearer top-secret",
            "X-Api-Key": "api-secret",
            "Content-Type": "application/json",
        },
        "url": "https://user:pass@example.com/items?token=abc&visible=yes",
        "nested": {"password": "hunter2", "ok": 42},
    }

    assert sanitizer.sanitize(value) == {
        "headers": {
            "Authorization": "[REDACTED]",
            "X-Api-Key": "[REDACTED]",
            "Content-Type": "application/json",
        },
        "url": "https://[REDACTED]@example.com/items?token=%5BREDACTED%5D&visible=yes",
        "nested": {"password": "[REDACTED]", "ok": 42},
    }


def test_target_policy_restricts_remote_hosts_but_allows_local_schema_files(tmp_path) -> None:
    policy = TargetPolicy(allowed_hosts={"api.example.com"})

    policy.validate(str(tmp_path / "openapi.yaml"))
    policy.validate("https://api.example.com/openapi.json")

    with pytest.raises(TargetNotAllowed):
        policy.validate("https://internal.example.net/openapi.json")
