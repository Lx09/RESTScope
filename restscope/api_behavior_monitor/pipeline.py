"""Provide typed, owner-written annotations for one response Pipeline pass.

Each matched exchange receives a fresh :class:`PipelineAnnotations`. Modules
publish small in-memory facts under namespaced keys and later Modules read them
without learning the producing implementation. No annotation is persisted as a
generic database value.
"""

from __future__ import annotations

from dataclasses import dataclass

from .response_evidence import ResponseEvidence


@dataclass(frozen=True, slots=True)
class AnnotationKey[ValueT]:
    """Name one typed fact and the sole Pipeline Module allowed to write it."""

    name: str
    owner: str
    value_type: type[ValueT]


class PipelineAnnotations:
    """Store advisory typed facts for one matched request only."""

    def __init__(self) -> None:
        """Create an empty request-local annotation set."""

        self._values: dict[str, object] = {}

    def write[ValueT](
        self,
        owner: str,
        key: AnnotationKey[ValueT],
        value: ValueT,
    ) -> None:
        """Publish a fact once after checking namespace ownership and runtime type."""

        if owner != key.owner:
            raise PermissionError(f"Annotation {key.name!r} is owned by {key.owner!r}")
        if key.name in self._values:
            raise ValueError(f"Annotation {key.name!r} is already written")
        if not isinstance(value, key.value_type):
            raise TypeError(f"Annotation {key.name!r} has the wrong value type")
        self._values[key.name] = value

    def read[ValueT](self, key: AnnotationKey[ValueT]) -> ValueT | None:
        """Return one typed fact when its owner published it earlier in the Pipeline."""

        value = self._values.get(key.name)
        if value is None:
            return None
        if not isinstance(value, key.value_type):
            raise TypeError(f"Annotation {key.name!r} contains the wrong value type")
        return value


OBSERVATION_ID = AnnotationKey("observation.id", "observation", str)
RESPONSE_EVIDENCE = AnnotationKey(
    "observation.response_evidence",
    "observation",
    ResponseEvidence,
)
