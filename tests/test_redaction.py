"""Regression scenarios for redaction. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel


def test_redactor_only_replaces_registered_values_recursively() -> None:
    """Scenario: verify that redactor only replaces registered values recursively."""
    from restscope.observability import Redactor

    @dataclass
    class DataclassPayload:
        password: str

    class ModelPayload(BaseModel):
        token: str

    redactor = Redactor(["llm-key", "phoenix-key"])
    payload = redactor.redact(
        {
            "authorization": "Bearer target-token",
            "cookie": "session=target-cookie",
            "password": "generated-password",
            "reasoning_content": "full reasoning",
            "llm-key": "value-under-secret-key",
            "nested": [
                ModelPayload(token="generated-token"),
                DataclassPayload(password="prefix llm-key suffix"),
                b"phoenix-key",
            ],
        }
    )

    assert payload == {
        "authorization": "Bearer target-token",
        "cookie": "session=target-cookie",
        "password": "generated-password",
        "reasoning_content": "full reasoning",
        "***REDACTED***": "value-under-secret-key",
        "nested": [
            {"token": "generated-token"},
            {"password": "prefix ***REDACTED*** suffix"},
            "***REDACTED***",
        ],
    }


def test_redactor_registration_is_exact_value_based_and_repr_is_safe() -> None:
    """Scenario: verify that redactor registration is exact value based and repr is safe."""
    from restscope.observability import Redactor

    redactor = Redactor(["short-key"])
    redactor.register_secrets(["long-key", "", "short-key"])

    assert redactor.redact_text(
        "Bearer visible-token api_key=visible-key short-key long-key"
    ) == (
        "Bearer visible-token api_key=visible-key "
        "***REDACTED*** ***REDACTED***"
    )
    assert "short-key" not in repr(redactor)
    assert "long-key" not in repr(redactor)


def test_redactor_preserves_mapping_entries_when_redacted_keys_collide() -> None:
    """Scenario: verify that redactor preserves mapping entries when redacted keys collide."""
    from restscope.observability import Redactor

    redactor = Redactor(["first-key", "second-key"])

    assert redactor.redact(
        {
            "first-key": "first value",
            "second-key": "second value",
            "***REDACTED***": "literal value",
        }
    ) == {
        "***REDACTED***": "first value",
        "***REDACTED***#2": "second value",
        "***REDACTED***#3": "literal value",
    }


def test_llm_package_no_longer_exports_redactor() -> None:
    """Scenario: verify that llm package no longer exports redactor."""
    import restscope.llm as llm

    assert not hasattr(llm, "Redactor")
