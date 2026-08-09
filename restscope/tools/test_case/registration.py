"""Combine the four authorized Test Case evidence behavior bindings."""

from restscope.tools.runtime import AgentToolbox, ToolBinding

from .contracts import TestCaseToolBackend
from .parameter_queries import (
    find_parameters_by_value_tool_spec,
    get_parameter_value_tool_spec,
    parameter_tool_bindings,
)
from .response_queries import (
    find_response_fields_by_value_tool_spec,
    get_response_field_value_tool_spec,
    response_tool_bindings,
)


def test_case_tool_bindings(backend: TestCaseToolBackend) -> tuple[ToolBinding, ...]:
    """Return all four behavior bindings in stable Profile order."""
    return (*parameter_tool_bindings(backend), *response_tool_bindings(backend))


def register_test_case_tools(*, toolbox: AgentToolbox, catalog: TestCaseToolBackend) -> None:
    """Register the four evidence reads in the supplied Agent toolbox."""
    specs = {
        item.name: item
        for item in (
            get_parameter_value_tool_spec(),
            find_parameters_by_value_tool_spec(),
            get_response_field_value_tool_spec(),
            find_response_fields_by_value_tool_spec(),
        )
    }
    for binding in test_case_tool_bindings(catalog):
        toolbox.register(spec=specs[binding.name], execute=binding.execute)
