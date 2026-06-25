"""Prompt and response redaction helpers."""

from __future__ import annotations

import re


class Redactor:
    """Remove common secret-looking values before LLM submission or logging."""

    SECRET_KEYS = {
        "authorization",
        "cookie",
        "set-cookie",
        "password",
        "passwd",
        "token",
        "api_key",
        "access_token",
        "refresh_token",
        "client_secret",
        "session_id",
        "jwt",
    }

    TEXT_PATTERNS = [
        r"Bearer\s+[A-Za-z0-9\-._~+/]+=*",
        r"api[_-]?key\s*[:=]\s*[A-Za-z0-9_.\-]+",
        r"access_token\s*[:=]\s*[A-Za-z0-9_.\-]+",
        r"refresh_token\s*[:=]\s*[A-Za-z0-9_.\-]+",
    ]

    def redact_text(self, text: str) -> str:
        result = text
        for pattern in self.TEXT_PATTERNS:
            result = re.sub(pattern, "***REDACTED***", result, flags=re.IGNORECASE)
        return result

    def redact_mapping(self, value: dict) -> dict:
        redacted: dict = {}
        for key, item in value.items():
            if str(key).lower() in self.SECRET_KEYS:
                redacted[key] = "***REDACTED***"
            elif isinstance(item, dict):
                redacted[key] = self.redact_mapping(item)
            elif isinstance(item, str):
                redacted[key] = self.redact_text(item)
            else:
                redacted[key] = item
        return redacted
