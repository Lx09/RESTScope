"""Expose RESTScope's application lifecycle without revealing its object graph.

Callers construct :class:`RESTScopeApp`, initialize one OpenAPI target, start
the Main Agent, and close the App. Database, Monitor, Target API, Request
Generation, UI, tracing, and Harness composition remain private to this package.
"""

from .runtime import RESTScopeApp

__all__ = ["RESTScopeApp"]
