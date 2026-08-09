"""Identify request inputs and response fields across RESTScope workflows.

OpenAPI Schemas, generated requests, retained Test Cases, and observed response
values use different concrete path spellings.  This Module exposes the two
immutable reference types that translate those spellings into stable semantic
identities without reading runtime state or granting Tool access.
"""

from .request import RequestInputLocation, RequestInputReference
from .response import ResponseFieldReference

__all__ = [
    "RequestInputLocation",
    "RequestInputReference",
    "ResponseFieldReference",
]
