"""
RESTScope Logging Configuration

Provides centralized logging setup for the entire project.
This module should be imported early in the package initialization.

Usage:
    >>> from restscope.logging_config import setup_logging, get_logger
    >>> setup_logging()  # Uses CONFIG.logging.level
    >>> logger = get_logger(__name__)
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from .restscope_config import CONFIG

# Third-party loggers to suppress at DEBUG level (set to WARNING instead)
# These libraries are verbose at DEBUG level and typically not useful for debugging
_SUPPRESSED_LOGGERS = [
    "urllib3",  # HTTP library
    "httpx",  # Target and LLM HTTP client summaries
    "httpcore",  # Low-level headers, including Set-Cookie
    "openai",  # OpenAI-compatible provider request details
    "asyncio",  # Async runtime
]


def setup_logging(
    level: Optional[str] = None,
    log_format: Optional[str] = None,
    log_file: Optional[str] = None,
) -> None:
    """
    Setup global logging configuration.

    Uses CONFIG values as defaults, can be overridden via parameters.

    Args:
        level: Log level, overrides CONFIG.logging.level
        log_format: Custom log format string, overrides CONFIG.logging.format
        log_file: Optional file path to write logs, overrides CONFIG.log_file
    """
    # Use CONFIG values directly (no fallback chains)
    log_level = level or CONFIG.logging.level
    fmt = log_format or CONFIG.logging.format
    file_path = log_file or str(CONFIG.log_file)

    # Build handlers
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    # Ensure directory exists
    Path(file_path).parent.mkdir(parents=True, exist_ok=True)
    handlers.append(logging.FileHandler(file_path))

    # Configure root logger
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format=fmt,
        handlers=handlers,
        force=True,  # Override existing configuration
    )

    # Suppress verbose third-party loggers
    _suppress_verbose_loggers()


def _suppress_verbose_loggers() -> None:
    """Suppress verbose third-party library loggers to WARNING level."""
    level_name = CONFIG.logging.third_party_level.upper()
    level = getattr(logging, level_name, logging.WARNING)

    for logger_name in _SUPPRESSED_LOGGERS:
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance with the given name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)
