"""Own the authoritative metadata Catalog for model-callable Tools.

The Catalog validates complete Tool contracts once during Harness construction.
It does not grant execution permission or hold session implementations; an
Agent Profile and Tool Bindings decide what one Agent can actually call.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from jsonschema import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict, model_validator

from restscope.llm import ToolSpec


ToolSubject = Literal[
    "http",
    "openapi",
    "resource",
    "test_case",
    "worklist",
    "parameter",
    "subagent",
    "external",
]


class ToolDefinition(BaseModel):
    """Pair one complete Tool contract with the thing the Tool handles.

    Args:
        subject: Stable noun used to group Tools for discovery and review.
        spec: Complete model-visible name, description, and JSON contracts.

    The executable implementation is deliberately absent. Runtime state is
    bound only after an Agent Profile selects this definition.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: ToolSubject
    spec: ToolSpec

    @property
    def name(self) -> str:
        """Return the globally unique model-visible Tool name."""
        return self.spec.name

    @model_validator(mode="after")
    def require_complete_local_contract(self) -> "ToolDefinition":
        """Require a closed, described contract for every RESTScope Tool."""
        self.check_local_contract()
        return self

    def check_local_contract(self) -> None:
        """Reject local contracts that leave their top-level shape implicit.

        External MCP servers own their contracts, so the Harness validates only
        that their JSON Schemas are syntactically valid. RESTScope-owned Tools
        must additionally describe a closed object at both sides of the model
        boundary. Nested arbitrary JSON is allowed only where the owning Tool
        explicitly documents why it is open.
        """
        if self.subject == "external":
            return
        if not self.spec.description.strip():
            raise ValueError(f"RESTScope Tool requires a description: {self.name}")
        if self.spec.input_schema.get("type") != "object":
            raise ValueError(f"RESTScope Tool input must be an object: {self.name}")
        if self.spec.input_schema.get("additionalProperties") is not False:
            raise ValueError(
                f"RESTScope Tool input must reject additional properties: {self.name}"
            )
        output = self.spec.output_schema
        if output is None:
            raise ValueError(f"RESTScope Tool requires an output schema: {self.name}")
        if output.get("type") != "object":
            raise ValueError(f"RESTScope Tool output must be an object: {self.name}")
        if output.get("additionalProperties") is not False:
            raise ValueError(
                f"RESTScope Tool output must reject additional properties: {self.name}"
            )


class ToolCatalog:
    """Expose immutable, schema-checked Tool definitions by exact name.

    The constructor rejects duplicate names and invalid JSON Schema before any
    Agent request can be built. Callers may enumerate or select definitions but
    cannot add or replace entries after construction.
    """

    def __init__(self, definitions: Iterable[ToolDefinition] = ()) -> None:
        """Validate and freeze the supplied definitions in declaration order."""
        indexed: dict[str, ToolDefinition] = {}
        for definition in definitions:
            self._check_schema(definition, "input", definition.spec.input_schema)
            if definition.spec.output_schema is not None:
                self._check_schema(
                    definition,
                    "output",
                    definition.spec.output_schema,
                )
            # Recheck the semantic contract here because callers can construct
            # unvalidated Pydantic copies before handing definitions to a Catalog.
            definition.check_local_contract()
            if definition.name in indexed:
                raise ValueError(f"Tool is already defined: {definition.name}")
            indexed[definition.name] = definition
        self._definitions = indexed

    @staticmethod
    def _check_schema(
        definition: ToolDefinition,
        contract: str,
        schema: dict,
    ) -> None:
        """Reject an invalid JSON Schema with the owning Tool in the message."""
        try:
            validator_for(schema).check_schema(schema)
        except SchemaError as exc:
            raise ValueError(
                f"Tool has an invalid {contract} schema: {definition.name}"
            ) from exc

    def definitions(
        self,
        *,
        subject: ToolSubject | None = None,
    ) -> tuple[ToolDefinition, ...]:
        """Return definitions in stable declaration order, optionally filtered."""
        values = tuple(self._definitions.values())
        if subject is None:
            return values
        return tuple(item for item in values if item.subject == subject)

    def get(self, name: str) -> ToolDefinition:
        """Return one exact definition or raise for an unknown Profile grant."""
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise KeyError(f"Unknown Tool: {name}") from exc

    def select(self, names: Iterable[str]) -> tuple[ToolDefinition, ...]:
        """Resolve an Agent Profile's ordered Tool names without broadening it."""
        return tuple(self.get(name) for name in names)
