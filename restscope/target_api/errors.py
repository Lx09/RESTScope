"""Expose stable failures shared by target request preparation and sending.

Request preparation and network execution both use these exceptions so Tool
adapters can translate failures without depending on provider exception text.
"""


class TargetAPIError(RuntimeError):
    """Carry a stable target API error code and a redacted explanation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TargetAPITimeout(TimeoutError):
    """Indicate that one target request exceeded its configured timeout."""
