"""Record each attempted Failure Resolution HTTP Probe as a run-local Test Case.

The HTTP Tool validates and sends the request. This Harness-owned adapter then
normalizes the Tool result into the same direct-name request representation and
Failure types used by generated Batch cases, assigns the next ``TC*`` identity,
and returns only bounded feedback to the resolving Agent.
"""

from __future__ import annotations

from copy import deepcopy
from http.cookies import CookieError, SimpleCookie
from typing import Any
from urllib.parse import unquote

from restscope.llm import ToolCall, ToolResult
from restscope.request_generation import OperationGeneratorConfig

from .test_case_catalog import (
    CatalogTestCaseDraft,
    TestCaseCatalog,
    parse_http_failure,
    parse_transport_failure,
)


def record_probe_result(
    *,
    catalog: TestCaseCatalog,
    config: OperationGeneratorConfig,
    tool_call: ToolCall,
    result: ToolResult,
) -> ToolResult:
    """Record one attempted Probe in run-local memory and return compact evidence."""

    request = _probe_request(config=config, arguments=tool_call.arguments)
    structured = result.structured if isinstance(result.structured, dict) else {}
    status_code = structured.get("status_code")
    if result.status == "succeeded" and isinstance(status_code, int):
        headers = structured.get("headers")
        headers = headers if isinstance(headers, dict) else {}
        media_type = str(headers.get("content-type") or "").split(";", 1)[0]
        body = structured.get("body") if 400 <= status_code < 600 else None
        failure = parse_http_failure(
            status_code=status_code,
            reason_phrase=str(structured.get("reason_phrase") or ""),
            media_type=media_type,
            response_body=body,
            body_truncated=False,
        )
        case = catalog.record(
            CatalogTestCaseDraft(request=request, response_body=body, failure=failure)
        )
        return ToolResult(
            tool_call_id=tool_call.id,
            name=tool_call.name,
            status="succeeded",
            structured={
                "case_id": case.case_id,
                "status_code": status_code,
                "failure": failure.model_dump(mode="json") if failure else None,
            },
        )

    error = result.error or {}
    failure = parse_transport_failure(
        code=str(error.get("code") or error.get("type") or result.status),
        message=str(error.get("message") or "HTTP probe failed"),
    )
    case = catalog.record(
        CatalogTestCaseDraft(request=request, response_body=None, failure=failure)
    )
    return ToolResult(
        tool_call_id=tool_call.id,
        name=tool_call.name,
        status=result.status,
        structured={"case_id": case.case_id, "failure": failure.model_dump(mode="json")},
        error=result.error,
    )


def _probe_request(
    *,
    config: OperationGeneratorConfig,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Normalize HTTP arguments into the Test Case Catalog request shape."""

    request: dict[str, Any] = {
        "path": {},
        "query": deepcopy(arguments.get("query") or {}),
        "header": {},
        "cookie": {},
    }
    actual_segments = str(arguments["path"]).split("/")
    template_segments = config.snapshot.path.split("/")
    for actual, template in zip(actual_segments, template_segments):
        if template.startswith("{") and template.endswith("}"):
            name = template[1:-1]
            request["path"][name] = _typed_path_value(
                config=config,
                name=name,
                value=unquote(actual),
            )
    for name, value in (arguments.get("headers") or {}).items():
        if name.casefold() == "cookie":
            request["cookie"].update(
                _declared_probe_cookies(config=config, header=str(value))
            )
        else:
            request["header"][name.lower()] = deepcopy(value)
    if "json_body" in arguments:
        request["body"] = deepcopy(arguments["json_body"])
    elif "form_body" in arguments:
        request["body"] = deepcopy(arguments["form_body"])
    elif "text_body" in arguments:
        request["body"] = arguments["text_body"]
    return request


def _declared_probe_cookies(
    *,
    config: OperationGeneratorConfig,
    header: str,
) -> dict[str, str]:
    """Extract only operation-declared Cookie Parameters from one header."""

    parsed = SimpleCookie()
    try:
        parsed.load(header)
    except CookieError:
        return {}
    declared = {
        item.name for item in config.snapshot.parameters if item.location == "cookie"
    }
    return {
        name: parsed[name].value for name in sorted(declared) if name in parsed
    }


def _typed_path_value(
    *,
    config: OperationGeneratorConfig,
    name: str,
    value: str,
) -> Any:
    """Recover the OpenAPI scalar type hidden by a concrete URL path."""

    parameter = next(
        (
            item
            for item in config.snapshot.parameters
            if item.location == "path" and item.name == name
        ),
        None,
    )
    if parameter is None:
        return value
    node = next(
        (
            item
            for item in config.snapshot.input_nodes
            if item.input_node_id == parameter.input_node_id
        ),
        None,
    )
    schema_type = (
        node.schema_contract.type
        if node is not None and node.schema_contract is not None
        else None
    )
    types = set(schema_type if isinstance(schema_type, list) else [schema_type])
    try:
        if "integer" in types:
            return int(value)
        if "number" in types:
            return float(value)
        if "boolean" in types and value.casefold() in {"true", "false"}:
            return value.casefold() == "true"
    except ValueError:
        # An intentionally invalid value remains the exact string sent. Its
        # mismatch may be the diagnostic evidence Resolution is seeking.
        pass
    return value
