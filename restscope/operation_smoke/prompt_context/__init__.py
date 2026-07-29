"""Fit complete Smoke evidence into a model window without losing its shape.

All four Operation Smoke roles use this small service so the same evidence
priority applies everywhere: current work is required, recent history is
preferred, and only genuinely oversized values are represented by marked
head-and-tail excerpts.
"""

from .fitting import (
    FittedMessageContext,
    FittedPromptContext,
    fit_message_context,
    fit_prompt_context,
)

__all__ = [
    "FittedMessageContext",
    "FittedPromptContext",
    "fit_message_context",
    "fit_prompt_context",
]
