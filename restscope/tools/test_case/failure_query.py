"""Define the trusted parsed-Failure lookup contract.

Failure Resolution already receives its initial Failure messages, so this Tool
remains in the global Catalog for other callers but has no Resolution binding.
"""

from __future__ import annotations

from restscope.llm import ToolSpec

from .contracts import GET_FAILURE_MESSAGES_TOOL_NAME, _CaseIdsInput
from .schemas import _cases_schema


def get_failure_messages_tool_spec() -> ToolSpec:
    """Describe parsed Failure-message lookup for exact Test Cases."""
    return ToolSpec(
        name=GET_FAILURE_MESSAGES_TOOL_NAME,
        description=(
            "Get parsed Failure messages for known TC references. An empty list "
            "means that Test Case has no retained Failure messages."
        ),
        kind="local_function",
        input_schema=_CaseIdsInput.model_json_schema(),
        output_schema=_cases_schema(
            {
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "items": {
                            "description": (
                                "One parsed Failure value retained by the Harness; "
                                "its JSON type depends on the parser that produced it."
                            )
                        },
                    },
                },
                "required": ["messages"],
                "additionalProperties": False,
            }
        ),
    )
