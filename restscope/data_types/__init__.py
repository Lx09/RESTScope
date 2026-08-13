"""Define recursive JSON data shared by independent RESTScope Modules.

OpenAPI documents, target responses, Tool payloads, Provider payloads, and
observer projections all use the same closed data language. These aliases keep
that language explicit without making the package root a utility surface.
Foreign values that RESTScope deliberately does not inspect use ``object``.
"""

from __future__ import annotations

from typing import TypeAlias
from typing_extensions import TypeAliasType


type JSONScalar = None | bool | int | float | str

type JSONValue = (
    JSONScalar
    | list[JSONValue]
    | dict[str, JSONValue]
)

type JSONObject = dict[str, JSONValue]

__all__ = ["JSONObject", "JSONScalar", "JSONValue"]
