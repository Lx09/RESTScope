"""Define semantic request-input references shared across runtime workflows.

OpenAPI lookup and deterministic testing build references from different
representations of the same operation.  The Test Case Catalog then uses those
references to read structured request JSON and return small evidence fragments.
This pure in-memory Module owns that common handle grammar and JSON traversal;
it does not own an OpenAPI document, Generator configuration, Catalog, Agent,
tool registration, or persistent state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping


RequestInputLocation = Literal["path", "query", "header", "cookie", "body"]
_SchemaVariant = Literal["oneOf", "anyOf", "allOf"]


@dataclass(frozen=True, slots=True)
class _PathStep:
    """Retain one trusted Schema traversal step behind the public Interface."""

    kind: Literal["property", "items", "variant"]
    name: str | None = None
    index: int | None = None


@dataclass(frozen=True, slots=True)
class RequestInputReference:
    """Connect one unique semantic handle to structured request JSON.

    Callers create a path/query/header/cookie Parameter reference or the request
    Body reference, then derive child Schema references with ``property``,
    ``items``, and ``variant``.  ``read`` reports presence separately from the
    value so an explicit JSON null is not confused with an omitted input.
    ``fragment`` returns only the selected JSON ancestry; when traversal enters
    an array it keeps the complete real array instead of inventing placeholders.

    The MVP assumes real Parameter and property names do not contain dots or
    square brackets.  It deliberately adds no escaping or rejection policy.
    """

    location: RequestInputLocation
    _parameter_name: str | None = None
    _steps: tuple[_PathStep, ...] = ()

    @classmethod
    def parameter(
        cls,
        location: Literal["path", "query", "header", "cookie"],
        name: str,
    ) -> "RequestInputReference":
        """Create one ordinary OpenAPI Parameter reference.

        Args:
            location: The Parameter's OpenAPI ``in`` location.
            name: Its direct name inside that request-location JSON object.

        Returns:
            A root reference such as ``query.sort``.

        Raises:
            ValueError: The location or name cannot form an ordinary Parameter.
        """
        if location not in {"path", "query", "header", "cookie"}:
            raise ValueError(f"Unsupported Parameter location: {location}")
        if not name:
            raise ValueError("Parameter name must not be empty")
        return cls(location=location, _parameter_name=name)

    @classmethod
    def body(cls) -> "RequestInputReference":
        """Create the request Body root reference."""
        return cls(location="body")

    @property
    def handle(self) -> str:
        """Return the stable model-facing handle for this request input."""
        output = (
            "body"
            if self.location == "body"
            else f"{self.location}.{self._parameter_name}"
        )
        for step in self._steps:
            if step.kind == "property":
                output += f".{step.name}"
            elif step.kind == "items":
                output += "[]"
            else:
                output += f".{step.name}[{step.index}]"
        return output

    def property(self, name: str) -> "RequestInputReference":
        """Return a reference to one object property below this input.

        Args:
            name: The property's direct JSON key.

        Returns:
            A new immutable child reference; the current reference is unchanged.
        """
        if not name:
            raise ValueError("Property name must not be empty")
        return self._child(_PathStep(kind="property", name=name))

    def items(self) -> "RequestInputReference":
        """Return the semantic item reference for an array Schema."""
        return self._child(_PathStep(kind="items"))

    def variant(
        self,
        kind: _SchemaVariant,
        index: int,
    ) -> "RequestInputReference":
        """Return one Schema-combination branch reference.

        Combination branches make handles unique but do not add a key to the
        concrete request JSON, so traversal skips this step later.
        """
        if kind not in {"oneOf", "anyOf", "allOf"}:
            raise ValueError(f"Unsupported Schema variant: {kind}")
        if index < 0:
            raise ValueError("Schema variant index must be non-negative")
        return self._child(
            _PathStep(kind="variant", name=kind, index=index)
        )

    def read(
        self,
        request: Mapping[str, Any],
    ) -> tuple[bool, Any | None]:
        """Read this input from one structured Test Case request.

        Args:
            request: JSON-shaped request evidence with location objects and an
                optional Body.

        Returns:
            ``(True, value)`` when sent, including an explicit null value;
            otherwise ``(False, None)``.  Array child properties are projected
            in encounter order, matching existing semantic-input behavior.
        """
        present, current = self._root_value(request)
        if not present:
            return False, None
        for step in self._steps:
            if step.kind in {"items", "variant"}:
                if step.kind == "items" and not isinstance(current, list):
                    return False, None
                continue
            assert step.name is not None
            if isinstance(current, list):
                projected = [
                    item[step.name]
                    for item in current
                    if isinstance(item, Mapping) and step.name in item
                ]
                if not projected:
                    return False, None
                current = projected
                continue
            if not isinstance(current, Mapping) or step.name not in current:
                return False, None
            current = current[step.name]
        return True, current

    def fragment(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Project this present input as direct-name request JSON.

        Args:
            request: The structured Test Case request containing this input.

        Returns:
            The smallest ordinary-object ancestry.  If the handle crosses an
            array, the smallest trustworthy prefix includes the complete first
            array container so indices and neighboring values remain factual.

        Raises:
            KeyError: The referenced input is not present in ``request``.
        """
        first_array = next(
            (
                index
                for index, step in enumerate(self._steps)
                if step.kind == "items"
            ),
            None,
        )
        selected = self
        if first_array is not None:
            selected = RequestInputReference(
                location=self.location,
                _parameter_name=self._parameter_name,
                _steps=self._steps[:first_array],
            )
        present, value = selected.read(request)
        if not present:
            raise KeyError(f"Request input was not used: {self.handle}")
        return selected._wrap(value)

    def _child(self, step: _PathStep) -> "RequestInputReference":
        """Append one trusted step without exposing the internal representation."""
        return RequestInputReference(
            location=self.location,
            _parameter_name=self._parameter_name,
            _steps=(*self._steps, step),
        )

    def _root_value(
        self,
        request: Mapping[str, Any],
    ) -> tuple[bool, Any | None]:
        """Read the Body or one direct name from its location object."""
        if self.location == "body":
            return (
                (True, request["body"])
                if "body" in request
                else (False, None)
            )
        container = request.get(self.location)
        if not isinstance(container, Mapping):
            return False, None
        assert self._parameter_name is not None
        if self._parameter_name not in container:
            return False, None
        return True, container[self._parameter_name]

    def _wrap(self, value: Any) -> dict[str, Any]:
        """Rebuild direct-name JSON ancestry around one selected value."""
        current = value
        for step in reversed(self._steps):
            if step.kind == "property":
                current = {step.name: current}
        if self.location == "body":
            return {"body": current}
        assert self._parameter_name is not None
        return {self.location: {self._parameter_name: current}}
