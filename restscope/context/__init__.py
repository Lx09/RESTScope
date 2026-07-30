"""Build safe, compact messages for any RESTScope language-model decision.

The package sits between domain-specific prompt builders and the provider-neutral
LLM client.  Domain code supplies already selected facts; this package encodes
those facts as bounded text, preserves complete tool-call groups, and reports
size metrics for traces.  It does not query memory, understand workflow DTOs,
choose tools, or validate a model's final domain decision.
"""

from .context import AgentContext, ContextLimits, ContextMetrics
from .writer import CompactTextWriter

__all__ = [
    "AgentContext",
    "CompactTextWriter",
    "ContextLimits",
    "ContextMetrics",
]
