"""Define the one strict tool used for independent Patch review output."""

from restscope.llm import ToolSpec


PARAMETER_PATCH_REVIEW_TOOL = "submit_parameter_patch_review"


def parameter_patch_review_tool_spec() -> ToolSpec:
    """Return a fixed-root DeepSeek strict schema for one semantic verdict."""
    return ToolSpec(
        name=PARAMETER_PATCH_REVIEW_TOOL,
        description=(
            "Review one compiled Parameter Patch candidate. List only concrete "
            "requirement mismatches; use an empty issues array when it passes."
        ),
        kind="local_function",
        strict=True,
        input_schema={
            "type": "object",
            "properties": {
                "accepted": {"type": "boolean"},
                "issues": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["accepted", "issues"],
            "additionalProperties": False,
        },
    )
