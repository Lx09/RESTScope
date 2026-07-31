"""Security contracts for application and third-party logging levels."""

from __future__ import annotations

import logging
from pathlib import Path


def test_setup_logging_suppresses_http_and_openai_debug_output(
    tmp_path: Path,
) -> None:
    """Transport libraries must not expose headers when app DEBUG is enabled."""
    from restscope.logging_config import setup_logging
    from restscope.restscope_config import CONFIG

    setup_logging(
        level="DEBUG",
        log_file=str(tmp_path / "restscope.log"),
    )

    expected_level = getattr(
        logging,
        CONFIG.logging.third_party_level.upper(),
        logging.WARNING,
    )
    assert logging.getLogger().level == logging.DEBUG
    for name in ("httpx", "httpcore", "openai"):
        assert logging.getLogger(name).level == expected_level
