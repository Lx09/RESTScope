"""Build a normalized OpenAPI document from selected parsed operations."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from .exceptions import OperationDocumentGenerationError
from .ir import (
    ExampleIR,
    HeaderIR,
    MediaTypeIR,
    OpenAPISpecIR,
    OperationIR,
    ParameterIR,
    RequestBodyIR,
    ResponseIR,
    SchemaIR,
    SecuritySchemeIR,
    ServerIR,
)


_SCHEMA_MODELED_KEYS = {
    "type",
    "format",
    "title",
    "description",
    "properties",
    "required",
    "items",
    "enum",
    "const",
    "default",
    "nullable",
    "readOnly",
    "writeOnly",
    "deprecated",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "minLength",
    "maxLength",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
    "allOf",
    "anyOf",
    "oneOf",
    "not",
    "additionalProperties",
    "example",
    "examples",
    "discriminator",
    "xml",
    "externalDocs",
}
_SCHEMA_SINGLE_KEYS = {
    "additionalItems",
    "contains",
    "contentSchema",
    "else",
    "if",
    "propertyNames",
    "then",
    "unevaluatedItems",
    "unevaluatedProperties",
}
_SCHEMA_ARRAY_KEYS = {"allOf", "anyOf", "oneOf", "prefixItems"}
_SCHEMA_MAP_KEYS = {
    "$defs",
    "definitions",
    "dependentSchemas",
    "patternProperties",
    "properties",
}

_PARAMETER_ALLOWED_KEYS = {
    "name",
    "in",
    "description",
    "required",
    "deprecated",
    "allowEmptyValue",
    "style",
    "explode",
    "allowReserved",
    "schema",
    "example",
    "examples",
    "content",
}
_REQUEST_BODY_ALLOWED_KEYS = {"description", "required", "content"}
_RESPONSE_ALLOWED_KEYS = {"description", "headers", "content", "links"}
_HEADER_ALLOWED_KEYS = {
    "description",
    "required",
    "deprecated",
    "allowEmptyValue",
    "style",
    "explode",
    "allowReserved",
    "schema",
    "example",
    "examples",
    "content",
}
_MEDIA_TYPE_ALLOWED_KEYS = {"schema", "example", "examples", "encoding"}
_EXAMPLE_ALLOWED_KEYS = {"summary", "description", "value", "externalValue", "$ref"}
_SECURITY_SCHEME_ALLOWED_KEYS = {
    "type",
    "description",
    "name",
    "in",
    "scheme",
    "bearerFormat",
    "flows",
    "openIdConnectUrl",
}


def build_openapi_document(
    ir: OpenAPISpecIR,
    operation_keys: Sequence[str],
) -> dict[str, Any]:
    """Build an OpenAPI 3.1 document containing exactly the selected operations."""
    if isinstance(operation_keys, (str, bytes)):
        raise OperationDocumentGenerationError(
            "operation_keys must be a sequence of operation keys, not a string"
        )

    unique_keys: list[str] = []
    seen: set[str] = set()
    for operation_key in operation_keys:
        if not isinstance(operation_key, str) or not operation_key:
            raise OperationDocumentGenerationError(
                "operation_keys must contain non-empty strings"
            )
        if operation_key not in seen:
            unique_keys.append(operation_key)
            seen.add(operation_key)

    if not unique_keys:
        raise OperationDocumentGenerationError("At least one operation key is required")

    missing = [key for key in unique_keys if key not in ir.operations]
    if missing:
        raise OperationDocumentGenerationError(
            f"Unknown operation key(s): {', '.join(missing)}"
        )

    operations = [ir.operations[key] for key in unique_keys]
    return _DocumentBuilder(ir).build(operations)


class _DocumentBuilder:
    def __init__(self, ir: OpenAPISpecIR) -> None:
        self.ir = ir
        self.schema_components: dict[str, dict[str, Any] | bool] = {}
        self.security_components: dict[str, dict[str, Any]] = {}
        self._pending_schema_components: set[str] = set()
        self._active_raw_schema_refs: set[str] = set()
        self._active_example_refs: set[str] = set()
        self._component_names_by_id = {
            id(schema): name for name, schema in ir.components.schemas.items()
        }

    def build(self, operations: list[OperationIR]) -> dict[str, Any]:
        document: dict[str, Any] = {
            "openapi": "3.1.0",
            "info": self._serialize_info(operations),
            "paths": {},
        }
        if self.ir.meta.external_docs is not None:
            document["externalDocs"] = deepcopy(self.ir.meta.external_docs)

        paths: dict[str, dict[str, Any]] = document["paths"]
        for operation in operations:
            path_item = paths.setdefault(
                operation.path,
                self._serialize_path_metadata(operation.path),
            )
            method = operation.method.lower()
            if method in path_item:
                raise OperationDocumentGenerationError(
                    f"Multiple selected operations resolve to {method.upper()} {operation.path}"
                )
            path_item[method] = self._serialize_operation(operation)

        components: dict[str, Any] = {}
        if self.schema_components:
            components["schemas"] = self.schema_components
        if self.security_components:
            components["securitySchemes"] = self.security_components
        if components:
            document["components"] = components
        return document

    def _serialize_info(self, operations: list[OperationIR]) -> dict[str, Any]:
        meta = self.ir.meta
        if meta.title:
            title = meta.title
        elif len(operations) == 1:
            title = operations[0].operation_key
        else:
            title = "RESTScope selected operations"

        info: dict[str, Any] = {
            "title": title,
            "version": meta.version or "0.0.0",
        }
        _set_if_not_none(info, "summary", meta.summary)
        _set_if_not_none(info, "description", meta.description)
        _set_if_not_none(info, "termsOfService", meta.terms_of_service)
        _set_if_not_none(info, "contact", meta.contact)
        _set_if_not_none(info, "license", meta.license)
        return info

    def _serialize_path_metadata(self, path: str) -> dict[str, Any]:
        path_ir = self.ir.paths.get(path)
        if path_ir is None:
            return {}
        result: dict[str, Any] = {}
        _set_if_not_none(result, "summary", path_ir.summary)
        _set_if_not_none(result, "description", path_ir.description)
        result.update(deepcopy(path_ir.extensions))
        return result

    def _serialize_operation(self, operation: OperationIR) -> dict[str, Any]:
        if not operation.responses.by_status:
            raise OperationDocumentGenerationError(
                f"Operation {operation.operation_key} has no responses"
            )

        result: dict[str, Any] = {}
        _set_if_not_none(result, "operationId", operation.operation_id)
        if operation.tags:
            result["tags"] = list(operation.tags)
        _set_if_not_none(result, "summary", operation.summary)
        _set_if_not_none(result, "description", operation.description)
        if operation.deprecated:
            result["deprecated"] = True

        parameters = [
            *operation.path_parameters,
            *operation.query_parameters,
            *operation.header_parameters,
            *operation.cookie_parameters,
        ]
        if parameters:
            result["parameters"] = [self._serialize_parameter(item) for item in parameters]
        if operation.request_body is not None:
            result["requestBody"] = self._serialize_request_body(operation.request_body)

        result["responses"] = {
            status_code: self._serialize_response(response)
            for status_code, response in operation.responses.by_status.items()
        }

        effective_servers = operation.servers or self.ir.meta.servers
        if effective_servers:
            result["servers"] = [self._serialize_server(server) for server in effective_servers]

        if operation.security.requirement_sets:
            result["security"] = self._serialize_security(operation)

        result.update(deepcopy(operation.extensions))
        return result

    def _serialize_parameter(self, parameter: ParameterIR) -> dict[str, Any]:
        result = _raw_extras(
            parameter.raw,
            allowed=_PARAMETER_ALLOWED_KEYS,
            modeled=_PARAMETER_ALLOWED_KEYS,
        )
        result["name"] = parameter.name
        result["in"] = parameter.location
        if parameter.required or parameter.location == "path":
            result["required"] = True
        _set_if_not_none(result, "description", parameter.description)
        if parameter.deprecated:
            result["deprecated"] = True
        if parameter.allow_empty_value:
            result["allowEmptyValue"] = True
        _set_if_not_none(result, "style", parameter.style)
        _set_if_not_none(result, "explode", parameter.explode)
        if parameter.allow_reserved:
            result["allowReserved"] = True
        if parameter.example is not None:
            result["example"] = deepcopy(parameter.example)
        if parameter.examples:
            result["examples"] = {
                name: self._serialize_example(example)
                for name, example in parameter.examples.items()
            }

        if parameter.content:
            result["content"] = {
                media_type: self._serialize_media_type(media)
                for media_type, media in parameter.content.items()
            }
        else:
            result["schema"] = (
                self._serialize_schema(parameter.schema)
                if parameter.schema is not None
                else {}
            )
        return result

    def _serialize_request_body(self, body: RequestBodyIR) -> dict[str, Any]:
        if not body.contents:
            raise OperationDocumentGenerationError("Request body has no media types")
        result = _raw_extras(
            body.raw,
            allowed=_REQUEST_BODY_ALLOWED_KEYS,
            modeled=_REQUEST_BODY_ALLOWED_KEYS,
        )
        result["content"] = {
            media_type: self._serialize_media_type(media)
            for media_type, media in body.contents.items()
        }
        _set_if_not_none(result, "description", body.description)
        if body.required:
            result["required"] = True
        return result

    def _serialize_response(self, response: ResponseIR) -> dict[str, Any]:
        result = _raw_extras(
            response.raw,
            allowed=_RESPONSE_ALLOWED_KEYS,
            modeled=_RESPONSE_ALLOWED_KEYS,
        )
        result["description"] = response.description or ""
        if response.headers:
            result["headers"] = {
                name: self._serialize_header(header)
                for name, header in response.headers.items()
            }
        if response.contents:
            result["content"] = {
                media_type: self._serialize_media_type(media)
                for media_type, media in response.contents.items()
            }
        return result

    def _serialize_header(self, header: HeaderIR) -> dict[str, Any]:
        result = _raw_extras(
            header.raw,
            allowed=_HEADER_ALLOWED_KEYS,
            modeled={
                "description",
                "required",
                "deprecated",
                "allowEmptyValue",
                "style",
                "explode",
                "schema",
                "content",
            },
        )
        _set_if_not_none(result, "description", header.description)
        if header.required:
            result["required"] = True
        if header.deprecated:
            result["deprecated"] = True
        if header.allow_empty_value:
            result["allowEmptyValue"] = True
        _set_if_not_none(result, "style", header.style)
        _set_if_not_none(result, "explode", header.explode)
        raw_examples = result.get("examples")
        if isinstance(raw_examples, dict):
            result["examples"] = {
                name: self._normalize_raw_example(value)
                for name, value in raw_examples.items()
            }
        if header.content:
            result["content"] = {
                media_type: self._serialize_media_type(media)
                for media_type, media in header.content.items()
            }
        else:
            result["schema"] = (
                self._serialize_schema(header.schema)
                if header.schema is not None
                else {}
            )
        return result

    def _serialize_media_type(self, media: MediaTypeIR) -> dict[str, Any]:
        result = _raw_extras(
            media.raw,
            allowed=_MEDIA_TYPE_ALLOWED_KEYS,
            modeled=_MEDIA_TYPE_ALLOWED_KEYS,
        )
        if media.schema is not None:
            result["schema"] = self._serialize_schema(media.schema)
        if media.example is not None:
            result["example"] = deepcopy(media.example)
        if media.examples:
            result["examples"] = {
                name: self._serialize_example(example)
                for name, example in media.examples.items()
            }
        if media.encoding:
            result["encoding"] = deepcopy(media.encoding)
        return result

    def _serialize_example(self, example: ExampleIR) -> dict[str, Any]:
        result = self._example_raw_base(example.raw)
        _set_if_not_none(result, "summary", example.summary)
        _set_if_not_none(result, "description", example.description)
        if example.value is not None:
            result["value"] = deepcopy(example.value)
        _set_if_not_none(result, "externalValue", example.external_value)
        return result

    @staticmethod
    def _serialize_server(server: ServerIR) -> dict[str, Any]:
        result: dict[str, Any] = {"url": server.url}
        _set_if_not_none(result, "description", server.description)
        if server.variables:
            variables: dict[str, Any] = {}
            for name, variable in server.variables.items():
                item: dict[str, Any] = {"default": variable.default or ""}
                if variable.enum:
                    item["enum"] = list(variable.enum)
                _set_if_not_none(item, "description", variable.description)
                variables[name] = item
            result["variables"] = variables
        return result

    def _serialize_security(self, operation: OperationIR) -> list[dict[str, list[str]]]:
        serialized: list[dict[str, list[str]]] = []
        for requirement_set in operation.security.requirement_sets:
            item: dict[str, list[str]] = {}
            for requirement in requirement_set:
                scheme = operation.security.resolved_schemes.get(requirement.scheme_name)
                if scheme is None:
                    raise OperationDocumentGenerationError(
                        f"Security scheme '{requirement.scheme_name}' required by "
                        f"{operation.operation_key} is unavailable"
                    )
                existing = self.security_components.get(requirement.scheme_name)
                serialized_scheme = self._serialize_security_scheme(scheme)
                if existing is not None and existing != serialized_scheme:
                    raise OperationDocumentGenerationError(
                        f"Security scheme '{requirement.scheme_name}' has conflicting definitions"
                    )
                self.security_components[requirement.scheme_name] = serialized_scheme
                item[requirement.scheme_name] = list(requirement.scopes)
            serialized.append(item)
        return serialized

    @staticmethod
    def _serialize_security_scheme(scheme: SecuritySchemeIR) -> dict[str, Any]:
        result = _raw_extras(
            scheme.raw,
            allowed=_SECURITY_SCHEME_ALLOWED_KEYS,
            modeled=_SECURITY_SCHEME_ALLOWED_KEYS,
        )
        result["type"] = scheme.type
        if scheme.type == "apiKey":
            if not scheme.api_key_name or not scheme.location:
                raise OperationDocumentGenerationError(
                    f"API key security scheme '{scheme.name}' lacks name or location"
                )
            result["name"] = scheme.api_key_name
            result["in"] = scheme.location
        elif scheme.type == "http":
            if not scheme.scheme:
                raise OperationDocumentGenerationError(
                    f"HTTP security scheme '{scheme.name}' lacks a scheme"
                )
            result["scheme"] = scheme.scheme
            _set_if_not_none(result, "bearerFormat", scheme.bearer_format)
        elif scheme.type == "oauth2":
            if not scheme.flows:
                raise OperationDocumentGenerationError(
                    f"OAuth2 security scheme '{scheme.name}' lacks flows"
                )
            result["flows"] = deepcopy(scheme.flows)
        elif scheme.type == "openIdConnect":
            if not scheme.open_id_connect_url:
                raise OperationDocumentGenerationError(
                    f"OpenID security scheme '{scheme.name}' lacks openIdConnectUrl"
                )
            result["openIdConnectUrl"] = scheme.open_id_connect_url
        _set_if_not_none(result, "description", scheme.description)
        return result

    def _serialize_schema(
        self,
        schema: SchemaIR,
        *,
        active: set[int] | None = None,
    ) -> dict[str, Any] | bool:
        if schema.ref_path:
            name = self._component_name_from_ref(schema.ref_path)
            self._ensure_schema_component(name)
            return {"$ref": self._component_ref(name)}

        if schema.raw.keys() == {"__bool_schema__"}:
            return bool(schema.raw["__bool_schema__"])

        if active is None:
            active = set()
        schema_id = id(schema)
        if schema_id in active:
            name = self._component_names_by_id.get(schema_id)
            if name is None:
                raise OperationDocumentGenerationError(
                    "An anonymous recursive SchemaIR cannot be serialized"
                )
            self._ensure_schema_component(name)
            return {"$ref": self._component_ref(name)}

        active.add(schema_id)
        try:
            result = self._schema_raw_extras(schema.raw)
            schema_type = deepcopy(schema.type)
            schema_format = schema.format
            if schema_type == "file":
                schema_type = "string"
                schema_format = schema_format or "binary"
            if schema.nullable:
                if isinstance(schema_type, str):
                    schema_type = [schema_type, "null"]
                elif isinstance(schema_type, list) and "null" not in schema_type:
                    schema_type = [*schema_type, "null"]
            _set_if_not_none(result, "type", schema_type)
            _set_if_not_none(result, "format", schema_format)
            _set_if_not_none(result, "title", schema.title)
            _set_if_not_none(result, "description", schema.description)

            if schema.properties:
                result["properties"] = {
                    name: self._serialize_schema(child, active=active)
                    for name, child in schema.properties.items()
                }
            if schema.required:
                result["required"] = list(schema.required)
            if schema.items is not None:
                result["items"] = self._serialize_schema(schema.items, active=active)
            if schema.enum is not None:
                result["enum"] = deepcopy(schema.enum)
            if schema.const is not None:
                result["const"] = deepcopy(schema.const)
            if schema.default is not None:
                result["default"] = deepcopy(schema.default)
            if schema.read_only is not None:
                result["readOnly"] = schema.read_only
            if schema.write_only is not None:
                result["writeOnly"] = schema.write_only
            if schema.deprecated is not None:
                result["deprecated"] = schema.deprecated

            self._serialize_numeric_constraints(schema, result)
            _set_if_not_none(result, "minLength", schema.min_length)
            _set_if_not_none(result, "maxLength", schema.max_length)
            _set_if_not_none(result, "pattern", schema.pattern)
            _set_if_not_none(result, "minItems", schema.min_items)
            _set_if_not_none(result, "maxItems", schema.max_items)
            _set_if_not_none(result, "uniqueItems", schema.unique_items)
            _set_if_not_none(result, "minProperties", schema.min_properties)
            _set_if_not_none(result, "maxProperties", schema.max_properties)

            if schema.all_of:
                result["allOf"] = [
                    self._serialize_schema(item, active=active) for item in schema.all_of
                ]
            if schema.any_of:
                result["anyOf"] = [
                    self._serialize_schema(item, active=active) for item in schema.any_of
                ]
            if schema.one_of:
                result["oneOf"] = [
                    self._serialize_schema(item, active=active) for item in schema.one_of
                ]
            if schema.not_schema is not None:
                result["not"] = self._serialize_schema(schema.not_schema, active=active)
            if schema.additional_properties is not None:
                result["additionalProperties"] = (
                    self._serialize_schema(schema.additional_properties, active=active)
                    if isinstance(schema.additional_properties, SchemaIR)
                    else schema.additional_properties
                )

            if schema.example is not None:
                result["example"] = deepcopy(schema.example)
            if schema.examples:
                result["examples"] = deepcopy(schema.examples)
            _set_if_not_none(result, "discriminator", schema.discriminator)
            _set_if_not_none(result, "xml", schema.xml)
            _set_if_not_none(result, "externalDocs", schema.external_docs)
            return result
        finally:
            active.remove(schema_id)

    def _schema_raw_extras(self, raw: dict[str, object]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        raw_ref = raw.get("$ref")
        if raw_ref is not None:
            if not isinstance(raw_ref, str):
                raise OperationDocumentGenerationError("Schema raw $ref must be a string")
            result.update(self._resolve_raw_schema_ref(raw_ref))
            for key in _SCHEMA_MODELED_KEYS:
                result.pop(key, None)

        for key, value in raw.items():
            if key in _SCHEMA_MODELED_KEYS or key in {"$ref", "__bool_schema__"}:
                continue
            result[key] = self._normalize_schema_keyword(key, value)
        return result

    def _normalize_schema_keyword(self, key: str, value: Any) -> Any:
        if key in _SCHEMA_SINGLE_KEYS:
            return self._normalize_raw_schema(value)
        if key in _SCHEMA_ARRAY_KEYS:
            if not isinstance(value, list):
                return deepcopy(value)
            return [self._normalize_raw_schema(item) for item in value]
        if key in _SCHEMA_MAP_KEYS:
            if not isinstance(value, dict):
                return deepcopy(value)
            return {
                name: self._normalize_raw_schema(item)
                for name, item in value.items()
            }
        if key == "items":
            if isinstance(value, list):
                return [self._normalize_raw_schema(item) for item in value]
            return self._normalize_raw_schema(value)
        if key == "dependencies" and isinstance(value, dict):
            return {
                name: (
                    self._normalize_raw_schema(item)
                    if isinstance(item, (dict, bool))
                    else deepcopy(item)
                )
                for name, item in value.items()
            }
        return deepcopy(value)

    def _normalize_raw_schema(self, value: Any) -> Any:
        if isinstance(value, bool):
            return value
        if not isinstance(value, dict):
            return deepcopy(value)

        raw_ref = value.get("$ref")
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        normalized = {
            key: self._normalize_schema_keyword(key, item)
            for key, item in siblings.items()
        }
        self._normalize_raw_schema_dialect(normalized)

        if raw_ref is None:
            return normalized
        if not isinstance(raw_ref, str):
            raise OperationDocumentGenerationError("Schema raw $ref must be a string")
        resolved = self._resolve_raw_schema_ref(raw_ref)
        if normalized:
            return {"allOf": [resolved, normalized]}
        return resolved

    @staticmethod
    def _normalize_raw_schema_dialect(schema: dict[str, Any]) -> None:
        schema_type = schema.get("type")
        if schema_type == "file":
            schema["type"] = "string"
            schema.setdefault("format", "binary")
        if schema.pop("nullable", False):
            schema_type = schema.get("type")
            if isinstance(schema_type, str):
                schema["type"] = [schema_type, "null"]
            elif isinstance(schema_type, list) and "null" not in schema_type:
                schema["type"] = [*schema_type, "null"]

        exclusive_minimum = schema.get("exclusiveMinimum")
        if exclusive_minimum is True and "minimum" in schema:
            schema["exclusiveMinimum"] = schema.pop("minimum")
        elif exclusive_minimum is False:
            schema.pop("exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if exclusive_maximum is True and "maximum" in schema:
            schema["exclusiveMaximum"] = schema.pop("maximum")
        elif exclusive_maximum is False:
            schema.pop("exclusiveMaximum")

    def _resolve_raw_schema_ref(self, ref_path: str) -> dict[str, Any] | bool:
        name = self._component_name_from_ref(ref_path)
        if name in self._active_raw_schema_refs:
            self._ensure_schema_component(name)
            return {"$ref": self._component_ref(name)}

        schema = self.ir.components.schemas[name]
        self._active_raw_schema_refs.add(name)
        try:
            return self._serialize_schema(schema, active=set())
        finally:
            self._active_raw_schema_refs.remove(name)

    def _example_raw_base(self, raw: dict[str, object]) -> dict[str, Any]:
        raw_ref = raw.get("$ref")
        if raw_ref is None:
            return _raw_extras(
                raw,
                allowed=_EXAMPLE_ALLOWED_KEYS,
                modeled={"summary", "description", "value", "externalValue", "$ref"},
            )
        if not isinstance(raw_ref, str):
            raise OperationDocumentGenerationError("Example raw $ref must be a string")
        result = self._resolve_example_ref(raw_ref)
        result.update(
            _raw_extras(
                raw,
                allowed=_EXAMPLE_ALLOWED_KEYS,
                modeled={"summary", "description", "value", "externalValue", "$ref"},
            )
        )
        return result

    def _normalize_raw_example(self, value: Any) -> Any:
        if not isinstance(value, dict):
            return deepcopy(value)
        raw_ref = value.get("$ref")
        if raw_ref is None:
            return _raw_extras(value, allowed=_EXAMPLE_ALLOWED_KEYS, modeled=set())
        if not isinstance(raw_ref, str):
            raise OperationDocumentGenerationError("Example raw $ref must be a string")
        result = self._resolve_example_ref(raw_ref)
        result.update(
            _raw_extras(
                value,
                allowed=_EXAMPLE_ALLOWED_KEYS,
                modeled={"$ref"},
            )
        )
        return result

    def _resolve_example_ref(self, ref_path: str) -> dict[str, Any]:
        prefix = "#/components/examples/"
        if not ref_path.startswith(prefix):
            raise OperationDocumentGenerationError(
                f"Example reference '{ref_path}' is not a local component example"
            )
        encoded_name = ref_path[len(prefix):]
        if not encoded_name or "/" in encoded_name:
            raise OperationDocumentGenerationError(
                f"Example reference '{ref_path}' does not identify one component"
            )
        name = encoded_name.replace("~1", "/").replace("~0", "~")
        example = self.ir.components.examples.get(name)
        if example is None:
            raise OperationDocumentGenerationError(
                f"Example component '{name}' is unavailable"
            )
        if name in self._active_example_refs:
            raise OperationDocumentGenerationError(
                f"Example component '{name}' contains a recursive reference"
            )
        self._active_example_refs.add(name)
        try:
            return self._serialize_example(example)
        finally:
            self._active_example_refs.remove(name)

    @staticmethod
    def _serialize_numeric_constraints(schema: SchemaIR, result: dict[str, Any]) -> None:
        minimum = schema.minimum
        maximum = schema.maximum
        exclusive_minimum = schema.exclusive_minimum
        exclusive_maximum = schema.exclusive_maximum

        if exclusive_minimum is True and minimum is not None:
            result["exclusiveMinimum"] = minimum
        else:
            _set_if_not_none(result, "minimum", minimum)
            if exclusive_minimum is not None and not isinstance(exclusive_minimum, bool):
                result["exclusiveMinimum"] = exclusive_minimum

        if exclusive_maximum is True and maximum is not None:
            result["exclusiveMaximum"] = maximum
        else:
            _set_if_not_none(result, "maximum", maximum)
            if exclusive_maximum is not None and not isinstance(exclusive_maximum, bool):
                result["exclusiveMaximum"] = exclusive_maximum

    def _component_name_from_ref(self, ref_path: str) -> str:
        prefixes = ("#/components/schemas/", "#/definitions/")
        prefix = next((item for item in prefixes if ref_path.startswith(item)), None)
        if prefix is None:
            raise OperationDocumentGenerationError(
                f"Recursive schema reference '{ref_path}' is not a local component schema"
            )
        encoded_name = ref_path[len(prefix):]
        if not encoded_name or "/" in encoded_name:
            raise OperationDocumentGenerationError(
                f"Recursive schema reference '{ref_path}' does not identify one component"
            )
        name = encoded_name.replace("~1", "/").replace("~0", "~")
        if name not in self.ir.components.schemas:
            raise OperationDocumentGenerationError(
                f"Recursive schema component '{name}' is unavailable"
            )
        return name

    def _ensure_schema_component(self, name: str) -> None:
        if name in self.schema_components or name in self._pending_schema_components:
            return
        schema = self.ir.components.schemas.get(name)
        if schema is None:
            raise OperationDocumentGenerationError(
                f"Recursive schema component '{name}' is unavailable"
            )
        self._pending_schema_components.add(name)
        try:
            serialized = self._serialize_schema(schema, active=set())
            self.schema_components[name] = serialized
        finally:
            self._pending_schema_components.remove(name)

    @staticmethod
    def _component_ref(name: str) -> str:
        encoded = name.replace("~", "~0").replace("/", "~1")
        return f"#/components/schemas/{encoded}"


def _raw_extras(
    raw: dict[str, object],
    *,
    allowed: set[str],
    modeled: set[str],
) -> dict[str, Any]:
    return {
        key: deepcopy(value)
        for key, value in raw.items()
        if key.startswith("x-") or (key in allowed and key not in modeled)
    }


def _set_if_not_none(target: dict[str, Any], key: str, value: Any) -> None:
    if value is not None:
        target[key] = deepcopy(value)
