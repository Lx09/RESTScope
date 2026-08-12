"""Behavioral contracts for the six-tool global OpenAPI backend."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path


def _ir():
    """Build two operations with varied inputs, bodies, and response fallbacks."""
    from restscope.openapi_parser import OpenAPIParser

    return OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Lookup", "version": "1"},
            "paths": {
                "/projects/{id}": {
                    "post": {
                        "parameters": [
                            {
                                "name": "id",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer", "minimum": 1},
                            },
                            {
                                "name": "page",
                                "in": "query",
                                "schema": {"type": "integer", "maximum": 100},
                            },
                            {
                                "name": "X-Trace",
                                "in": "header",
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "mode",
                                "in": "cookie",
                                "schema": {"type": "string"},
                            },
                        ],
                        "requestBody": {
                            "required": True,
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["name"],
                                        "properties": {
                                            "name": {
                                                "type": "string",
                                                "minLength": 3,
                                                "description": (
                                                    "Project name accepted by "
                                                    "this operation."
                                                ),
                                                "example": "example-project",
                                            }
                                        },
                                    }
                                },
                                "multipart/form-data": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "name": {"type": "string"},
                                            "avatar": {
                                                "type": "string",
                                                "format": "binary",
                                            },
                                        },
                                    }
                                },
                            },
                        },
                        "responses": {
                            "201": {
                                "description": "created",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "id": {"type": "integer"},
                                                "internal": {
                                                    "type": "string",
                                                    "writeOnly": True,
                                                },
                                                "items": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "properties": {
                                                            "name": {
                                                                "type": "string"
                                                            }
                                                        },
                                                    },
                                                },
                                            },
                                            "oneOf": [
                                                {
                                                    "type": "object",
                                                    "properties": {
                                                        "kind": {
                                                            "type": "string"
                                                        }
                                                    },
                                                }
                                            ],
                                        }
                                    }
                                },
                            },
                            "3XX": {
                                "description": "redirect",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "location": {"type": "string"}
                                            },
                                        }
                                    }
                                },
                            },
                            "4XX": {
                                "description": "client error",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "message": {"type": "string"}
                                            },
                                        }
                                    },
                                    "application/problem+json": {
                                        "schema": {
                                            "type": "object",
                                            "required": ["errors"],
                                            "properties": {
                                                "errors": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "required": ["code"],
                                                        "properties": {
                                                            "code": {
                                                                "type": "string",
                                                                "enum": ["invalid", "missing"],
                                                            }
                                                        },
                                                    },
                                                }
                                            },
                                        }
                                    },
                                },
                            },
                            "default": {
                                "description": "other error",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "detail": {"type": "string"}
                                            },
                                        }
                                    }
                                },
                            },
                        },
                    }
                },
                "/health": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "healthy",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "status": {
                                                    "type": "string",
                                                    "enum": ["ok"],
                                                }
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                },
            },
        }
    )


def _capability():
    """Bind one global Capability to a trusted in-memory ToolContext."""
    from restscope.tools.openapi import OpenAPIToolBackend
    from restscope.tools.context import ToolContext

    context = ToolContext(ir=_ir(), baseline_schema_source={})
    return OpenAPIToolBackend(context_provider=lambda: context)


def _operation_candidate_capability():
    """Build GitLab-like operation names used to recover from model guesses."""
    from restscope.tools.openapi import OpenAPIToolBackend
    from restscope.tools.context import ToolContext
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Candidate lookup", "version": "1"},
            "paths": {
                "/api/v4/projects": {
                    "post": {
                        "operationId": "postApiV4Projects",
                        "summary": "Create a project",
                        "responses": {"201": {"description": "created"}},
                    }
                },
                "/api/v4/project_aliases": {
                    "post": {
                        "operationId": "postApiV4ProjectAliases",
                        "summary": "Create a project alias",
                        "responses": {"201": {"description": "created"}},
                    }
                },
                "/health": {
                    "get": {
                        "operationId": "getHealth",
                        "summary": "Check service health",
                        "responses": {"200": {"description": "healthy"}},
                    }
                },
            },
        }
    )
    context = ToolContext(ir=ir, baseline_schema_source={})
    return OpenAPIToolBackend(context_provider=lambda: context)


def _observed_catalog(tmp_path: Path):
    """Create a real bounded observation reader for OpenAPI intersection."""
    from restscope.api_behavior_monitor.catalog import (
        ObservationWrite,
        OperationDefinition,
        APIBehaviorCatalog,
    )
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'observed.sqlite'}")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    catalog = APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(sessions)
    )

    class ObservedCatalog:
        """Add concise test observations and expose the real Catalog reads."""

        def __init__(self) -> None:
            self.count = 0

        def record_observation(
            self,
            *,
            operation_key,
            status_code,
            media_type,
            response_json,
        ) -> None:
            """Persist one exact successful response through the public seam."""

            method, _separator, path = operation_key.partition(" ")
            catalog.ensure_operation(
                OperationDefinition(
                    operation_id=operation_key,
                    method=method,
                    path=path,
                )
            )
            catalog.record_observation(
                ObservationWrite(
                    operation_id=operation_key,
                    timestamp=datetime(2026, 8, 11, tzinfo=UTC)
                    + timedelta(seconds=self.count),
                    status_code=status_code,
                    media_type=media_type,
                    request_json={"path": path},
                    response_json=response_json,
                )
            )
            self.count += 1

        def list_observed_response_coordinates(self):
            """Delegate coordinate discovery without loading response bodies."""

            return catalog.list_observed_response_coordinates()

        def list_observations(self, **arguments):
            """Delegate one exact bounded observation page."""

            return catalog.list_observations(**arguments)

    return ObservedCatalog()


def _grouped_observed_ir():
    """Build wildcard/default responses with several equivalent field names."""
    from restscope.openapi_parser import OpenAPIParser

    return OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Observed lookup", "version": "1"},
            "paths": {
                "/projects": {
                    "get": {
                        "responses": {
                            "2XX": {
                                "description": "projects",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "project_id": {"type": "integer"},
                                                "projectId": {"type": "integer"},
                                                "project_name": {"type": "string"},
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                },
                "/repositories": {
                    "get": {
                        "responses": {
                            "default": {
                                "description": "fallback",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "project-id": {"type": "integer"}
                                            },
                                        }
                                    }
                                },
                            }
                        }
                    }
                },
            },
        }
    )


def _toolbox():
    """Register the five schema-document Tools through one Interface."""
    from restscope.tools import (
        AgentToolbox,
    )
    from restscope.tools.openapi import (
        openapi_get_input_schema_tool_spec,
        openapi_get_response_field_schema_tool_spec,
        openapi_list_inputs_tool_spec,
        openapi_list_operations_tool_spec,
        openapi_list_response_fields_tool_spec,
    )

    capability = _capability()
    toolbox = AgentToolbox()
    toolbox.register(
        spec=openapi_list_operations_tool_spec(),
        execute=capability.list_operations,
    )
    toolbox.register(
        spec=openapi_list_inputs_tool_spec(),
        execute=capability.list_inputs,
    )
    toolbox.register(
        spec=openapi_list_response_fields_tool_spec(),
        execute=capability.list_response_fields,
    )
    toolbox.register(
        spec=openapi_get_input_schema_tool_spec(),
        execute=capability.get_input_schema,
    )
    toolbox.register(
        spec=openapi_get_response_field_schema_tool_spec(),
        execute=capability.get_response_field_schema,
    )
    return toolbox


def _execute(name: str, arguments: dict):
    """Execute one model-shaped call through validation and output checking."""
    from restscope.llm import ToolCall

    return _toolbox().execute(
        ToolCall(id="openapi-query", name=name, arguments=arguments)
    )


def test_operation_lookup_specs_explain_exact_key_format_without_limiting_keys() -> None:
    """Models see RESTScope's key syntax while every existing operation stays queryable."""
    from restscope.tools.openapi import (
        openapi_get_input_schema_tool_spec,
        openapi_get_response_field_schema_tool_spec,
        openapi_list_inputs_tool_spec,
        openapi_list_response_fields_tool_spec,
    )

    specs = [
        openapi_list_inputs_tool_spec(),
        openapi_list_response_fields_tool_spec(),
        openapi_get_input_schema_tool_spec(),
        openapi_get_response_field_schema_tool_spec(),
    ]

    for spec in specs:
        operation_key = spec.input_schema["properties"]["operation_key"]
        description = operation_key["description"]
        assert "METHOD /path" in description
        assert "POST /api/v4/projects" in description
        assert "operationId" in description
        assert "camelCase" in description
        assert "snake_case" in description
        assert "enum" not in operation_key
        assert "const" not in operation_key


def test_unknown_operation_returns_the_closest_real_operation_key() -> None:
    """An operationId-like guess receives a real METHOD/path recovery choice."""
    import pytest

    from restscope.tools import ToolFailure

    with pytest.raises(ToolFailure) as caught:
        _operation_candidate_capability().get_input_schema(
            operation_key="createProject",
            input="body.name",
        )

    assert caught.value.code == "openapi_operation_not_found"
    assert caught.value.safe_message == (
        "OpenAPI operation was not found: createProject. Closest existing "
        "operation keys: POST /api/v4/projects, "
        "POST /api/v4/project_aliases, GET /health"
    )


def test_operation_candidates_normalize_common_model_guess_styles() -> None:
    """Camel, snake, and operationId-like guesses rank the same real key first."""
    import pytest

    from restscope.tools import ToolFailure

    capability = _operation_candidate_capability()
    for guessed_key in (
        "createProject",
        "create_projects",
        "post_api_v4_projects",
        "createProjectV4",
    ):
        with pytest.raises(ToolFailure) as caught:
            capability.get_input_schema(
                operation_key=guessed_key,
                input="body.name",
            )

        candidates = caught.value.safe_message.split(
            "Closest existing operation keys: ",
            maxsplit=1,
        )[1]
        assert candidates.split(", ", maxsplit=1)[0] == (
            "POST /api/v4/projects"
        )


def test_operation_candidates_are_bounded_stable_and_expose_only_real_keys() -> None:
    """Large documents return ten deterministic METHOD/path choices, not aliases."""
    import pytest

    from restscope.tools.openapi import OpenAPIToolBackend
    from restscope.tools.context import ToolContext
    from restscope.tools import ToolFailure
    from restscope.openapi_parser import OpenAPIParser

    paths = {
        f"/items/{index:02d}": {
            "get": {
                "operationId": f"lookupAlias{index:02d}",
                "summary": f"Read item {index:02d}",
                "responses": {"200": {"description": "found"}},
            }
        }
        for index in range(12)
    }
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Bounded candidates", "version": "1"},
            "paths": paths,
        }
    )
    capability = OpenAPIToolBackend(
        context_provider=lambda: ToolContext(
            ir=ir,
            baseline_schema_source={},
        )
    )

    messages = []
    for _attempt in range(2):
        with pytest.raises(ToolFailure) as caught:
            capability.get_input_schema(
                operation_key="lookup_alias_11",
                input="query.id",
            )
        messages.append(caught.value.safe_message)

    assert messages[0] == messages[1]
    candidates = messages[0].split(
        "Closest existing operation keys: ",
        maxsplit=1,
    )[1].split(", ")
    assert len(candidates) == 10
    assert candidates[0] == "GET /items/11"
    assert all(candidate.startswith("GET /items/") for candidate in candidates)
    assert all("lookupAlias" not in candidate for candidate in candidates)


def test_unknown_operation_keeps_plain_error_when_the_ir_has_no_operations() -> None:
    """An empty document cannot offer a fabricated recovery choice."""
    import pytest

    from restscope.tools.openapi import OpenAPIToolBackend
    from restscope.tools.context import ToolContext
    from restscope.tools import ToolFailure
    from restscope.openapi_parser import OpenAPIParser

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Empty lookup", "version": "1"},
            "paths": {},
        }
    )
    capability = OpenAPIToolBackend(
        context_provider=lambda: ToolContext(
            ir=ir,
            baseline_schema_source={},
        )
    )

    with pytest.raises(ToolFailure) as caught:
        capability.list_inputs(operation_key="getAnything")

    assert caught.value.safe_message == (
        "OpenAPI operation was not found: getAnything"
    )


def test_list_inputs_returns_only_one_bounded_page_of_handles() -> None:
    """Listing inputs stays compact and identifies duplicate Body media types."""
    result = _execute(
        "openapi.list_inputs",
        {"operation_key": "POST /projects/{id}", "offset": 0, "limit": 2},
    )

    assert result.status == "succeeded"
    assert result.structured["total"] > 2
    assert result.structured["next_offset"] == 2
    assert len(result.structured["inputs"]) == 2
    assert "schema" not in repr(result.structured)


def test_list_operations_returns_stable_exact_operation_identities() -> None:
    """Operation discovery is sorted, paginated, and does not invent aliases."""
    result = _execute("openapi.list_operations", {"offset": 0, "limit": 1})

    assert result.status == "succeeded"
    assert result.structured == {
        "operations": [
            {
                "operation_key": "GET /health",
                "method": "GET",
                "path": "/health",
                "deprecated": False,
            }
        ],
        "total": 2,
        "offset": 0,
        "next_offset": 1,
    }


def test_list_inputs_filters_body_media_but_keeps_ordinary_parameters() -> None:
    """A media filter narrows Body duplicates without hiding path/query inputs."""
    result = _execute(
        "openapi.list_inputs",
        {
            "operation_key": "POST /projects/{id}",
            "media_type": "application/json",
            "prefix": "body.name",
        },
    )

    assert result.status == "succeeded"
    assert result.structured["inputs"] == [
        {"name": "body.name", "media_type": "application/json"}
    ]

    all_names = {
        item["name"]
        for item in _execute(
            "openapi.list_inputs",
            {
                "operation_key": "POST /projects/{id}",
                "media_type": "application/json",
            },
        ).structured["inputs"]
    }
    assert {"path.id", "query.page", "header.x-trace", "cookie.mode"} <= all_names


def test_list_response_fields_returns_one_compact_page() -> None:
    """Response discovery returns sorted handles without exposing Schemas."""
    result = _execute(
        "openapi.list_response_fields",
        {
            "operation_key": "POST /projects/{id}",
            "status_code": 201,
            "limit": 2,
        },
    )

    assert result.status == "succeeded"
    assert result.structured == {
        "operation_key": "POST /projects/{id}",
        "requested_status_code": "201",
        "matched_status_code": "201",
        "media_type": "application/json",
        "fields": [{"name": "body"}, {"name": "body.id"}],
        "total": 7,
        "offset": 0,
        "next_offset": 2,
    }
    assert "schema" not in repr(result.structured)


def test_find_observed_response_fields_returns_only_matching_current_ir_fields(
    tmp_path: Path,
) -> None:
    """Observed scalar evidence is intersected with the current response Schema."""
    from restscope.tools import (
        AgentToolbox,
    )
    from restscope.tools.context import ToolContext
    from restscope.tools.openapi import (
        OpenAPIToolBackend,
        openapi_find_observed_response_fields_tool_spec,
    )
    from restscope.llm import ToolCall

    catalog = _observed_catalog(tmp_path)
    catalog.record_observation(
        operation_key="POST /projects/{id}",
        status_code=201,
        media_type="application/json",
        response_json=(
            '{"id":7,"items":[{"name":"visible but not similar"}],'
            '"internal":"write-only","ghost":"not in current IR"}'
        ),
    )
    context = ToolContext(ir=_ir(), baseline_schema_source={})
    capability = OpenAPIToolBackend(
        context_provider=lambda: context,
        observed_response_reader=catalog,
    )
    toolbox = AgentToolbox()
    toolbox.register(
        spec=openapi_find_observed_response_fields_tool_spec(),
        execute=capability.find_observed_response_fields,
    )

    result = toolbox.execute(
        ToolCall(
            id="observed-fields",
            name="openapi.find_observed_response_fields",
            arguments={"name": "ID"},
        )
    )

    assert result.status == "succeeded"
    assert result.structured == {
        "requested_name": "ID",
        "responses": [
            {
                "operation_key": "POST /projects/{id}",
                "status_code": 201,
                "matched_status_code": "201",
                "media_type": "application/json",
                "fields": [
                    {
                        "field": "body.id",
                        "similarity_score": 1.0,
                        "match_basis": "normalized_exact",
                    }
                ],
            }
        ],
        "total": 1,
        "offset": 0,
    }
    assert "schema" not in repr(result.structured)
    assert "write-only" not in repr(result.structured)


def test_observed_field_pagination_groups_one_page_by_response_contract(
    tmp_path: Path,
) -> None:
    """Field offsets stay global while repeated response metadata is grouped."""
    from restscope.tools import (
        AgentToolbox,
    )
    from restscope.tools.context import ToolContext
    from restscope.tools.openapi import (
        OpenAPIToolBackend,
        openapi_find_observed_response_fields_tool_spec,
    )
    from restscope.llm import ToolCall

    catalog = _observed_catalog(tmp_path)
    for status_code in (200, 201):
        catalog.record_observation(
            operation_key="GET /projects",
            status_code=status_code,
            media_type="application/json",
            response_json='{"project_id":1,"projectId":2}',
        )
    catalog.record_observation(
        operation_key="GET /repositories",
        status_code=204,
        media_type="application/json",
        response_json='{"project-id":3}',
    )
    context = ToolContext(ir=_grouped_observed_ir(), baseline_schema_source={})
    capability = OpenAPIToolBackend(
        context_provider=lambda: context,
        observed_response_reader=catalog,
    )
    toolbox = AgentToolbox()
    toolbox.register(
        spec=openapi_find_observed_response_fields_tool_spec(),
        execute=capability.find_observed_response_fields,
    )

    first = toolbox.execute(
        ToolCall(
            id="first",
            name="openapi.find_observed_response_fields",
            arguments={"name": "project_id", "offset": 0, "limit": 2},
        )
    )
    second = toolbox.execute(
        ToolCall(
            id="second",
            name="openapi.find_observed_response_fields",
            arguments={"name": "project_id", "offset": 2, "limit": 2},
        )
    )
    third = toolbox.execute(
        ToolCall(
            id="third",
            name="openapi.find_observed_response_fields",
            arguments={"name": "project_id", "offset": 4, "limit": 2},
        )
    )

    assert first.status == "succeeded"
    assert first.structured == {
        "requested_name": "project_id",
        "responses": [
            {
                "operation_key": "GET /projects",
                "status_code": 200,
                "matched_status_code": "2XX",
                "media_type": "application/json",
                "fields": [
                    {
                        "field": "body.projectId",
                        "similarity_score": 1.0,
                        "match_basis": "normalized_exact",
                    },
                    {
                        "field": "body.project_id",
                        "similarity_score": 1.0,
                        "match_basis": "normalized_exact",
                    },
                ],
            }
        ],
        "total": 5,
        "offset": 0,
        "next_offset": 2,
    }
    assert second.structured["responses"] == [
        {
            "operation_key": "GET /projects",
            "status_code": 201,
            "matched_status_code": "2XX",
            "media_type": "application/json",
            "fields": [
                {
                    "field": "body.projectId",
                    "similarity_score": 1.0,
                    "match_basis": "normalized_exact",
                },
                {
                    "field": "body.project_id",
                    "similarity_score": 1.0,
                    "match_basis": "normalized_exact",
                }
            ],
        }
    ]
    assert second.structured["total"] == 5
    assert second.structured["next_offset"] == 4
    assert third.structured["responses"] == [
        {
            "operation_key": "GET /repositories",
            "status_code": 204,
            "matched_status_code": "default",
            "media_type": "application/json",
            "fields": [
                {
                    "field": "body.project-id",
                    "similarity_score": 1.0,
                    "match_basis": "normalized_exact",
                }
            ],
        }
    ]


def test_observed_lookup_reuses_array_and_combiner_field_references(
    tmp_path: Path,
) -> None:
    """Observed selectors map to the same handles as exact Schema lookup."""
    from restscope.tools.openapi import OpenAPIToolBackend
    from restscope.tools.context import ToolContext

    catalog = _observed_catalog(tmp_path)
    catalog.record_observation(
        operation_key="POST /projects/{id}",
        status_code=201,
        media_type="application/json",
        response_json='{"items":[{"name":"one"}],"kind":"created"}',
    )
    context = ToolContext(ir=_ir(), baseline_schema_source={})
    capability = OpenAPIToolBackend(
        context_provider=lambda: context,
        observed_response_reader=catalog,
    )

    nested = capability.find_observed_response_fields(name="items_name")
    branch = capability.find_observed_response_fields(name="kind")

    assert nested["structured"]["responses"][0]["fields"] == [
        {
            "field": "body.items[].name",
            "similarity_score": 1.0,
            "match_basis": "path_exact",
        }
    ]
    assert branch["structured"]["responses"][0]["fields"] == [
        {
            "field": "body.oneOf[0].kind",
            "similarity_score": 1.0,
            "match_basis": "normalized_exact",
        }
    ]


def test_observed_lookup_keeps_only_high_precision_fuzzy_matches(
    tmp_path: Path,
) -> None:
    """One-character omission passes 0.95 while a broad prefix does not."""
    from restscope.tools.openapi import OpenAPIToolBackend
    from restscope.tools.context import ToolContext

    catalog = _observed_catalog(tmp_path)
    catalog.record_observation(
        operation_key="GET /projects",
        status_code=200,
        media_type="application/json",
        response_json='{"project_name":"alpha"}',
    )
    context = ToolContext(ir=_grouped_observed_ir(), baseline_schema_source={})
    capability = OpenAPIToolBackend(
        context_provider=lambda: context,
        observed_response_reader=catalog,
    )

    close = capability.find_observed_response_fields(name="project_nam")
    broad = capability.find_observed_response_fields(name="project")

    assert close["structured"]["responses"][0]["fields"] == [
        {
            "field": "body.project_name",
            "similarity_score": 0.952381,
            "match_basis": "high_similarity",
        }
    ]
    assert broad["structured"]["responses"] == []
    assert broad["structured"]["total"] == 0


def test_list_response_fields_reuses_schema_traversal_rules() -> None:
    """Arrays and combiners are listed while write-only response fields stay hidden."""
    result = _execute(
        "openapi.list_response_fields",
        {"operation_key": "POST /projects/{id}", "status_code": "201"},
    )

    assert result.status == "succeeded"
    assert [field["name"] for field in result.structured["fields"]] == [
        "body",
        "body.id",
        "body.items",
        "body.items[]",
        "body.items[].name",
        "body.oneOf[0]",
        "body.oneOf[0].kind",
    ]
    assert "body.internal" not in repr(result.structured)


def test_list_response_fields_matches_status_fallbacks() -> None:
    """The list query shares exact, class-wildcard, and default response matching."""
    exact = _execute(
        "openapi.list_response_fields",
        {"operation_key": "POST /projects/{id}", "status_code": 201},
    )
    wildcard = _execute(
        "openapi.list_response_fields",
        {"operation_key": "POST /projects/{id}", "status_code": 302},
    )
    fallback = _execute(
        "openapi.list_response_fields",
        {"operation_key": "POST /projects/{id}", "status_code": 503},
    )

    assert exact.structured["matched_status_code"] == "201"
    assert wildcard.structured["matched_status_code"] == "3XX"
    assert fallback.structured["matched_status_code"] == "default"


def test_list_response_fields_has_only_the_approved_inputs() -> None:
    """Media selection and prefix filtering stay outside this narrow Interface."""
    from restscope.tools.openapi import openapi_list_response_fields_tool_spec

    spec = openapi_list_response_fields_tool_spec()
    extra_argument = _execute(
        "openapi.list_response_fields",
        {
            "operation_key": "POST /projects/{id}",
            "status_code": 201,
            "media_type": "application/json",
        },
    )
    missing_status = _execute(
        "openapi.list_response_fields",
        {"operation_key": "POST /projects/{id}"},
    )
    excessive_limit = _execute(
        "openapi.list_response_fields",
        {
            "operation_key": "POST /projects/{id}",
            "status_code": 201,
            "limit": 201,
        },
    )
    beyond_end = _execute(
        "openapi.list_response_fields",
        {
            "operation_key": "GET /health",
            "status_code": 200,
            "offset": 100,
        },
    )

    assert set(spec.input_schema["properties"]) == {
        "operation_key",
        "status_code",
        "offset",
        "limit",
    }
    assert spec.input_schema["required"] == ["operation_key", "status_code"]
    assert extra_argument.status == "denied"
    assert extra_argument.error["code"] == "invalid_tool_arguments"
    assert missing_status.status == "denied"
    assert missing_status.error["code"] == "invalid_tool_arguments"
    assert excessive_limit.status == "denied"
    assert excessive_limit.error["code"] == "invalid_tool_arguments"
    assert beyond_end.status == "succeeded"
    assert beyond_end.structured["fields"] == []
    assert beyond_end.structured["total"] == 2
    assert "next_offset" not in beyond_end.structured


def test_list_response_fields_reports_media_ambiguity() -> None:
    """A document that violates the single-media assumption still fails safely."""
    result = _execute(
        "openapi.list_response_fields",
        {"operation_key": "POST /projects/{id}", "status_code": 400},
    )

    assert result.status == "failed"
    assert result.error["code"] == "openapi_media_type_ambiguous"


def test_one_global_capability_can_select_another_exact_operation() -> None:
    """Operation scope comes from each call instead of Capability construction."""
    result = _execute(
        "openapi.get_response_field_schema",
        {
            "operation_key": "GET /health",
            "status_code": 200,
            "field": "body.status",
        },
    )

    assert result.status == "succeeded"
    assert result.structured["operation_key"] == "GET /health"
    assert result.structured["schema"]["enum"] == ["ok"]


def test_get_input_schema_returns_only_the_exact_node_summary() -> None:
    """A unique JSON body is selected without returning prose or sibling fields."""
    result = _execute(
        "openapi.get_input_schema",
        {
            "operation_key": "POST /projects/{id}",
            "input": "body.name",
        },
    )

    assert result.status == "succeeded"
    assert result.structured == {
        "operation_key": "POST /projects/{id}",
        "input": "body.name",
        "location": "body",
        "required": True,
        "media_type": "application/json",
        "schema": {
            "type": "string",
            "description": "Project name accepted by this operation.",
            "example": "example-project",
            "min_length": 3,
        },
    }
    assert "avatar" not in repr(result.structured)


def test_non_body_input_rejects_an_irrelevant_media_type() -> None:
    """The narrow Interface reports caller mistakes instead of ignoring them."""
    result = _execute(
        "openapi.get_input_schema",
        {
            "operation_key": "POST /projects/{id}",
            "input": "path.id",
            "media_type": "application/json",
        },
    )

    assert result.status == "failed"
    assert result.error["code"] == "openapi_input_media_type_not_allowed"


def test_response_lookup_matches_wildcard_and_normalizes_array_indexes() -> None:
    """Concrete failed-response paths resolve to their OpenAPI array item node."""
    result = _execute(
        "openapi.get_response_field_schema",
        {
            "operation_key": "POST /projects/{id}",
            "status_code": 422,
            "field": "body.errors[0].code",
            "media_type": "application/problem+json",
        },
    )

    assert result.status == "succeeded"
    assert result.structured["requested_status_code"] == "422"
    assert result.structured["matched_status_code"] == "4XX"
    assert result.structured["field"] == "body.errors[].code"
    assert result.structured["required"] is True
    assert result.structured["schema"]["enum"] == ["invalid", "missing"]


def test_response_lookup_uses_default_and_reports_media_ambiguity() -> None:
    """Fallback is deterministic while multiple JSON contracts require a choice."""
    fallback = _execute(
        "openapi.get_response_field_schema",
        {
            "operation_key": "POST /projects/{id}",
            "status_code": 503,
            "field": "body.detail",
        },
    )
    ambiguous = _execute(
        "openapi.get_response_field_schema",
        {
            "operation_key": "POST /projects/{id}",
            "status_code": 400,
            "field": "body.message",
        },
    )

    assert fallback.status == "succeeded"
    assert fallback.structured["matched_status_code"] == "default"
    assert ambiguous.status == "failed"
    assert ambiguous.error["code"] == "openapi_media_type_ambiguous"


def test_unknown_operation_and_old_tool_name_are_not_accepted() -> None:
    """Global lookup remains exact and the deleted scoped tool has no alias."""
    missing = _execute(
        "openapi.list_inputs",
        {"operation_key": "GET /missing"},
    )
    missing_response = _execute(
        "openapi.list_response_fields",
        {"operation_key": "GET /missing", "status_code": 200},
    )
    old = _execute("openapi.lookup_operation", {})

    assert missing.status == "failed"
    assert missing.error["code"] == "openapi_operation_not_found"
    assert missing_response.status == "failed"
    assert missing_response.error["code"] == "openapi_operation_not_found"
    assert old.status == "denied"
    assert old.error["code"] == "unknown_tool"


def test_observed_field_tool_validates_bounds_and_requires_catalog_injection() -> None:
    """The fifth OpenAPI tool stays unavailable without retained evidence."""
    from restscope.tools import (
        AgentToolbox,
    )
    from restscope.tools.openapi import (
        openapi_find_observed_response_fields_tool_spec,
    )
    from restscope.llm import ToolCall

    capability = _capability()
    toolbox = AgentToolbox()
    spec = openapi_find_observed_response_fields_tool_spec()
    toolbox.register(
        spec=spec,
        execute=capability.find_observed_response_fields,
    )

    unavailable = toolbox.execute(
        ToolCall(
            id="unavailable",
            name=spec.name,
            arguments={"name": "id"},
        )
    )
    excessive = toolbox.execute(
        ToolCall(
            id="excessive",
            name=spec.name,
            arguments={"name": "id", "limit": 201},
        )
    )

    assert set(spec.input_schema["properties"]) == {"name", "offset", "limit"}
    assert spec.input_schema["required"] == ["name"]
    assert unavailable.status == "failed"
    assert unavailable.error["code"] == "openapi_observation_catalog_unavailable"
    assert excessive.status == "denied"
    assert excessive.error["code"] == "invalid_tool_arguments"
