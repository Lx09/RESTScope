"""Keep the bounded identifier-selection prompt used by Resource Monitor.

Resource persistence and response traversal now live in the unified Monitor
Modules.  This package remains only as the stable home of the System Agent's
small prompt/result contract.
"""

from .prompts import (
    IDENTIFIER_SYSTEM_AGENT_INSTRUCTIONS,
    IdentifierSelectionDecision,
)

__all__ = [
    "IDENTIFIER_SYSTEM_AGENT_INSTRUCTIONS",
    "IdentifierSelectionDecision",
]
