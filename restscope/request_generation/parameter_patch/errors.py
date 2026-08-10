"""Define correctable failures shared by Patch compilation and projection."""


class ParameterPatchValidationError(ValueError):
    """Describe a semantic Patch or complete-output problem with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
