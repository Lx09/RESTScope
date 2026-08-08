"""Assemble the immutable Catalog of every RESTScope-owned Tool contract."""

from __future__ import annotations

from functools import lru_cache

from restscope.tools.http import http_request_tool_spec
from restscope.tools.openapi import (
    openapi_find_observed_response_fields_tool_spec,
    openapi_get_input_schema_tool_spec,
    openapi_get_response_field_schema_tool_spec,
    openapi_list_inputs_tool_spec,
    openapi_list_response_fields_tool_spec,
)
from restscope.tools.plan import plan_read_tool_spec, plan_update_tool_spec
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
from restscope.tools.worklist import (
    read_worklist_tool_spec,
    write_worklist_tool_spec,
)
from restscope.tools.test_case import (
    find_parameters_by_value_tool_spec,
    find_response_fields_by_value_tool_spec,
    get_failure_messages_tool_spec,
    get_parameter_value_tool_spec,
    get_response_field_value_tool_spec,
)

from .catalog import ToolCatalog, ToolDefinition
from .parameter import (
    generate_parameter_patch_tool_spec,
    parameter_history_tool_spec,
    read_candidate_tool_spec,
)


@lru_cache(maxsize=1)
def builtin_tool_catalog() -> ToolCatalog:
    """Return the one immutable Catalog of built-in Tool definitions."""
    grouped = (
        ("http", (http_request_tool_spec(),)),
        (
            "openapi",
            (
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
                get_parameter_value_tool_spec(),
                find_parameters_by_value_tool_spec(),
                get_response_field_value_tool_spec(),
                find_response_fields_by_value_tool_spec(),
                get_failure_messages_tool_spec(),
            ),
        ),
        (
            "worklist",
            (read_worklist_tool_spec(), write_worklist_tool_spec()),
        ),
        (
            "plan",
            (plan_read_tool_spec(), plan_update_tool_spec()),
        ),
        ("skill", (skill_read_tool_spec(),)),
        (
            "parameter",
            (
                parameter_history_tool_spec(),
                generate_parameter_patch_tool_spec(),
                read_candidate_tool_spec(),
            ),
        ),
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
