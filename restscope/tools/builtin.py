"""Assemble the immutable Catalog of every RESTScope-owned Tool contract."""

from __future__ import annotations

from functools import lru_cache

from restscope.tools.database import database_query_tool_spec
from restscope.tools.file import file_read_tool_spec
from restscope.tools.http import http_request_tool_spec
from restscope.tools.openapi import (
    openapi_find_observed_response_fields_tool_spec,
    openapi_get_input_schema_tool_spec,
    openapi_get_response_field_schema_tool_spec,
    openapi_list_inputs_tool_spec,
    openapi_list_operations_tool_spec,
    openapi_list_response_fields_tool_spec,
)
from restscope.tools.parameter_patch import parameter_patch_apply_tool_spec
from restscope.tools.plan import plan_read_tool_spec, plan_update_tool_spec
from restscope.tools.request_generation import (
    request_generation_get_input_state_tool_spec,
    request_generation_validate_patch_tool_spec,
)
from restscope.tools.resource import (
    resource_list_ids_tool_spec,
    resource_list_resources_tool_spec,
)
from restscope.tools.skill import skill_read_tool_spec
from restscope.tools.subagent import (
    subagent_cancel_tool_spec,
    subagent_start_tool_spec,
    subagent_wait_tool_spec,
)
from restscope.tools.test_case import (
    test_case_get_batch_results_tool_spec,
    test_case_get_tool_spec,
    test_case_run_batch_tool_spec,
)

from .catalog import ToolCatalog, ToolDefinition


@lru_cache(maxsize=1)
def builtin_tool_catalog() -> ToolCatalog:
    """Return the one immutable Catalog of built-in Tool definitions."""
    grouped = (
        ("database", (database_query_tool_spec(),)),
        ("http", (http_request_tool_spec(),)),
        (
            "openapi",
            (
                openapi_list_operations_tool_spec(),
                openapi_list_inputs_tool_spec(),
                openapi_list_response_fields_tool_spec(),
                openapi_find_observed_response_fields_tool_spec(),
                openapi_get_input_schema_tool_spec(),
                openapi_get_response_field_schema_tool_spec(),
            ),
        ),
        (
            "resource",
            (resource_list_resources_tool_spec(), resource_list_ids_tool_spec()),
        ),
        (
            "test_case",
            (
                test_case_run_batch_tool_spec(),
                test_case_get_batch_results_tool_spec(),
                test_case_get_tool_spec(),
            ),
        ),
        (
            "request_generation",
            (
                request_generation_get_input_state_tool_spec(),
                request_generation_validate_patch_tool_spec(),
            ),
        ),
        ("parameter_patch", (parameter_patch_apply_tool_spec(),)),
        (
            "plan",
            (plan_read_tool_spec(), plan_update_tool_spec()),
        ),
        ("skill", (skill_read_tool_spec(),)),
        ("file", (file_read_tool_spec(),)),
        (
            "subagent",
            (
                subagent_start_tool_spec(),
                subagent_wait_tool_spec(),
                subagent_cancel_tool_spec(),
            ),
        ),
    )
    return ToolCatalog(
        ToolDefinition(subject=subject, spec=spec)
        for subject, specs in grouped
        for spec in specs
    )
