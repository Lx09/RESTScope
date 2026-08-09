"""Install explicit process logging after the App has loaded configuration.

Importing RESTScope must not change the root logger or create files.  The App
therefore calls :func:`configure_logging` with already validated settings at
construction time.  This Module owns only Python logging setup; callers obtain
ordinary named loggers directly from :mod:`logging`.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from restscope.config import LoggingConfig

# Third-party loggers to suppress at DEBUG level (set to WARNING instead)
# These libraries are verbose at DEBUG level and typically not useful for debugging
_SUPPRESSED_LOGGERS = [
    "urllib3",  # HTTP library
    "httpx",  # Target and LLM HTTP client summaries
    "httpcore",  # Low-level headers, including Set-Cookie
    "openai",  # OpenAI-compatible provider request details
    "asyncio",  # Async runtime
]


def configure_logging(
    settings: LoggingConfig,
    *,
    log_file: Path,
) -> None:
    """Replace process handlers with RESTScope's explicit App configuration.

    Args:
        settings: Validated levels and message format from ``RESTScopeConfig``.
        log_file: Concrete destination resolved by the App. Its parent is
            created here because logging owns the file side effect.

    This function intentionally mutates process-wide logging. It must be called
    by App construction, never by a package import.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(
        level=getattr(logging, settings.level.upper(), logging.INFO),
        format=settings.format,
        handlers=handlers,
        force=True,
    )
    _suppress_verbose_loggers(settings.third_party_level)


def _suppress_verbose_loggers(level_name: str) -> None:
    """Apply one explicit threshold to noisy dependency loggers."""
    level = getattr(logging, level_name.upper(), logging.WARNING)

    for logger_name in _SUPPRESSED_LOGGERS:
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)
