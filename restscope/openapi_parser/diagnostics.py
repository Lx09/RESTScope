"""Diagnostics module for error reporting."""

from .ir import DiagnosticItemIR


def make_diagnostic(
    severity: str,
    code: str,
    message: str,
    path: str | None = None,
    method: str | None = None,
    pointer: str | None = None,
    exc: Exception | None = None,
    extras: dict[str, object] | None = None,
) -> DiagnosticItemIR:
    """
    Create a diagnostic item.

    Args:
        severity: The severity level (error, warning, info).
        code: The diagnostic code.
        message: The diagnostic message.
        path: The path template if applicable.
        method: The HTTP method if applicable.
        pointer: The JSON Pointer if applicable.
        exc: The exception that caused this diagnostic.
        extras: Additional context information.

    Returns:
        A DiagnosticItemIR instance.
    """
    return DiagnosticItemIR(
        severity=severity,
        code=code,
        message=message,
        path=path,
        method=method,
        pointer=pointer,
        exception_type=type(exc).__name__ if exc is not None else None,
        extras=extras or {},
    )
