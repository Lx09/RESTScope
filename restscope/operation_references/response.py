"""Define semantic response-field references shared across runtime workflows.

OpenAPI lookup describes response fields with ``body`` handles, while the API
Behavior Monitor stores the same paths as ``$`` selectors and Test Case evidence
may contain concrete array indexes. ``ResponseFieldReference`` owns conversion
between those spellings so every caller uses one field identity grammar. It is
pure in-memory code and never reads an OpenAPI document or stored response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from restscope.target_api.media_type import normalize_media_type

_SchemaVariant = Literal["oneOf", "anyOf", "allOf"]
_COMBINERS = {"oneOf", "anyOf", "allOf"}


@dataclass(frozen=True, slots=True)
class _PathStep:
    """Retain one property, array, or Schema-variant traversal step."""

    kind: Literal["property", "items", "variant"]
    name: str | None = None
    index: int | None = None


@dataclass(frozen=True, slots=True)
class ResponseFieldReference:
    """Connect one semantic response handle to its observation selector.

    Property and array steps exist in both the Schema and actual response JSON.
    A combiner branch such as ``oneOf[1]`` makes an OpenAPI field unique but is
    not a JSON key, so ``selector`` deliberately omits that step.

    Field names are assumed not to contain dots or square brackets, matching
    the existing request-input and response-observation grammar.
    """

    _steps: tuple[_PathStep, ...] = ()

    @classmethod
    def body(cls) -> ResponseFieldReference:
        """Create the root response Body reference."""
        return cls()

    @classmethod
    def from_selector(cls, selector: str) -> ResponseFieldReference:
        """Parse one stored ``$`` observation selector.

        Args:
            selector: A selector such as ``$.items[].id``.

        Returns:
            The equivalent semantic reference.

        Raises:
            ValueError: The selector uses unsupported syntax or has no ``$``
                root. Observation selectors cannot encode Schema variants.
        """
        if not selector.startswith("$"):
            raise ValueError("Response selector must start with $")
        reference = cls.body()
        remainder = selector[1:]
        while remainder:
            if remainder.startswith("[]"):
                reference = reference.items()
                remainder = remainder[2:]
                continue
            if not remainder.startswith("."):
                raise ValueError(f"Invalid response selector: {selector}")
            remainder = remainder[1:]
            boundary = min(
                (
                    index
                    for marker in (".", "[")
                    if (index := remainder.find(marker)) >= 0
                ),
                default=len(remainder),
            )
            name = remainder[:boundary]
            if not name:
                raise ValueError(f"Invalid response selector: {selector}")
            reference = reference.property(name)
            remainder = remainder[boundary:]
        return reference

    @classmethod
    def from_handle(cls, handle: str) -> ResponseFieldReference:
        """Parse a semantic handle or normalize its concrete array indexes.

        Args:
            handle: A path beginning with ``body``. Non-combiner indexes such
                as ``[3]`` become the semantic array step ``[]``; combiner
                indexes remain exact Schema branches.

        Returns:
            The normalized immutable reference.

        Raises:
            ValueError: The handle is malformed or does not start at ``body``.
        """
        if not handle.startswith("body"):
            raise ValueError("Response field handle must start with body")
        reference = cls.body()
        remainder = handle[4:]
        while remainder:
            if remainder.startswith("["):
                _raw_index, remainder = _take_index(remainder, handle=handle)
                reference = reference.items()
                continue
            if not remainder.startswith("."):
                raise ValueError(f"Invalid response field handle: {handle}")
            remainder = remainder[1:]
            boundary = min(
                (
                    index
                    for marker in (".", "[")
                    if (index := remainder.find(marker)) >= 0
                ),
                default=len(remainder),
            )
            name = remainder[:boundary]
            if not name:
                raise ValueError(f"Invalid response field handle: {handle}")
            remainder = remainder[boundary:]
            if name in _COMBINERS:
                if not remainder.startswith("["):
                    raise ValueError(
                        f"Schema combiner requires one branch index: {handle}"
                    )
                raw_index, remainder = _take_index(remainder, handle=handle)
                if not raw_index.isdigit():
                    raise ValueError(
                        f"Schema combiner requires a numeric index: {handle}"
                    )
                reference = reference.variant(name, int(raw_index))
                continue
            reference = reference.property(name)
        return reference

    @property
    def handle(self) -> str:
        """Return the stable model-facing ``body`` field handle."""
        output = "body"
        for step in self._steps:
            if step.kind == "property":
                output += f".{step.name}"
            elif step.kind == "items":
                output += "[]"
            else:
                output += f".{step.name}[{step.index}]"
        return output

    @property
    def selector(self) -> str:
        """Return the persisted ``$`` selector for actual response JSON."""
        output = "$"
        for step in self._steps:
            if step.kind == "property":
                output += f".{step.name}"
            elif step.kind == "items":
                output += "[]"
        return output

    @property
    def property_names(self) -> tuple[str, ...]:
        """Return direct JSON property names without arrays or Schema branches."""
        return tuple(
            step.name
            for step in self._steps
            if step.kind == "property" and step.name is not None
        )

    def select_values(self, document: object) -> tuple[object, ...]:
        """Return values reached by this Reference in one response document.

        Property steps read dictionary keys and item steps expand arrays. Schema
        variant steps are intentionally ignored because ``oneOf`` and similar
        branches identify an OpenAPI Schema location rather than a runtime JSON
        key. Missing keys and values of the wrong container type contribute no
        result; an explicitly present null remains a result so the caller can
        apply its own scalar or null policy.

        Args:
            document: Parsed response JSON or another opaque value to inspect.

        Returns:
            Values in response order. This method does not copy or interpret
            them and changes no state.
        """

        current: list[object] = [document]
        for step in self._steps:
            if step.kind == "variant":
                continue
            next_values: list[object] = []
            if step.kind == "items":
                for value in current:
                    if isinstance(value, list):
                        next_values.extend(value)
            else:
                assert step.name is not None
                for value in current:
                    if isinstance(value, dict) and step.name in value:
                        next_values.append(value[step.name])
            current = next_values
        return tuple(current)

    def property(self, name: str) -> ResponseFieldReference:
        """Return an immutable child for one direct JSON property name."""
        if not name or any(marker in name for marker in (".", "[", "]")):
            raise ValueError("Response property name is not representable")
        return self._child(_PathStep(kind="property", name=name))

    def items(self) -> ResponseFieldReference:
        """Return the semantic item reference for one array level."""
        return self._child(_PathStep(kind="items"))

    def variant(
        self,
        kind: _SchemaVariant,
        index: int,
    ) -> ResponseFieldReference:
        """Return one exact OpenAPI Schema-combination branch."""
        if kind not in _COMBINERS:
            raise ValueError(f"Unsupported Schema variant: {kind}")
        if index < 0:
            raise ValueError("Schema variant index must be non-negative")
        return self._child(
            _PathStep(kind="variant", name=kind, index=index)
        )

    def _child(self, step: _PathStep) -> ResponseFieldReference:
        """Append one trusted path step without changing the current reference."""
        return ResponseFieldReference((*self._steps, step))


class ResponseSourceCoordinate(BaseModel):
    """Identify one exact field in successful responses from a producer.

    This shared value owns the source coordinates used by Generator strategies,
    in-memory Generation State bindings, and persisted consumer propositions.
    It normalizes media-type parameters and proves that the readable field name
    agrees with the single selector grammar owned by
    :class:`ResponseFieldReference`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    producer_operation_id: str = Field(min_length=1, max_length=2000)
    status_code: int = Field(ge=200, le=299)
    media_type: str = Field(min_length=1, max_length=500)
    selector: str = Field(min_length=2, max_length=2000)
    field_name: str = Field(min_length=1, max_length=500)

    @field_validator("media_type")
    @classmethod
    def normalize_media_type_value(cls, value: str) -> str:
        """Remove transport parameters and use case-insensitive HTTP spelling."""

        normalized = normalize_media_type(value)
        if normalized is None:
            raise ValueError("media_type cannot be blank")
        return normalized

    @model_validator(mode="after")
    def require_selector_field_name(self) -> ResponseSourceCoordinate:
        """Reject a display field that contradicts the exact JSON selector."""

        reference = ResponseFieldReference.from_selector(self.selector)
        if not reference.property_names:
            raise ValueError("source selector must identify a response property")
        if reference.property_names[-1] != self.field_name:
            raise ValueError("field_name must equal the selector's final property")
        return self


def _take_index(value: str, *, handle: str) -> tuple[str, str]:
    """Remove one leading square-bracket index from a response handle."""
    closing = value.find("]")
    if not value.startswith("[") or closing < 0:
        raise ValueError(f"Invalid response field index: {handle}")
    raw_index = value[1:closing]
    if raw_index and not raw_index.isdigit():
        raise ValueError(f"Invalid response field index: {handle}")
    return raw_index, value[closing + 1 :]
