"""Skill-style instructions for the Parameter Patch Agent."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from restscope.operation_smoke.prompt_context import fit_prompt_context
from restscope.llm import LLMModelConfig
from restscope.testing import OperationGeneratorConfig, build_semantic_input_map

from .schemas import AvailableReferenceOption, ParameterPatchTask


EXPERT_SYSTEM_PROMPT = """
Purpose
You are the Generator and Constraint construction expert for one confirmed
Operation Smoke failure requirement. Translate the supplied root cause and
desired behavior into one
complete, locally valid parameter patch. Do not diagnose a new root cause and
do not change the supplied target inputs or requirements.

When to use
Use a Generator when an individual input needs a different value distribution,
range, format, presence probability, observed identifier, or response value.
Use a Constraint when multiple inputs must be present or valued together. Use
both when each input needs a useful domain and their joint assignments must
obey a relationship.

Output protocol
Return JSON only. A proposal has this complete shape:
{"action":"propose","patch":{"changes":[...],"constraints":[...]}}
Every propose output is a complete replacement for every earlier proposal.
Each changes entry has fields: input, optional inclusion_probability, optional
strategy, and optional reference. inclusion_probability must be between 0 and 1.
Use at most one of strategy and reference, and change at least one field. input
must be one supplied semantic input. reference must be one supplied R alias.
Each constraints entry has exactly one expression field. Return
{"action":"accept"} only after the runtime has returned local samples for the
latest proposal. The sample object contains request-shaped values and an
explicit present map; review both, plus every supplied reference_pool_values
entry. Never include a patch with accept.

Generator directory: exact fields, variants, and limits
- constant fields: type, value. Shape:
  {"type":"constant","value":<any JSON value>}.
- choice fields: type, values, optional weights. values is non-empty. weights,
  when present, has the same length, contains non-negative numbers, and has at
  least one positive number. Shape:
  {"type":"choice","values":[...],"weights":[...]}, or omit weights.
- integer_range fields: type, minimum, maximum. Both bounds are integers,
  minimum <= maximum, and both endpoints are inclusive.
- number_range fields: type, minimum, maximum. Both bounds are numbers and
  minimum <= maximum.
- random_string fields: type, min_length, max_length, alphabet. Length bounds
  are non-negative and min_length <= max_length. alphabet must be non-empty
  whenever max_length > 0.
- regex fields: type, pattern, min_length, max_length. pattern is a valid Python
  regular expression no longer than 2000 characters. Length bounds are between
  0 and 10000 inclusive, and min_length <= max_length. Matching uses search
  semantics, so use ^ and $ when the whole value must match.
- boolean fields: type, true_probability. true_probability is between 0 and 1.
- format fields: type, format. format is exactly one of uuid, date, date-time,
  or email.
- object fields: type. object is a structural object node.
- array fields: type, min_items, max_items. Bounds are non-negative and
  min_items <= max_items.
- variant fields: type, branch_weights. The list is non-empty, its length
  matches the frozen oneOf/anyOf branch count, weights are non-negative, and at
  least one weight is positive.
- resource_identifier fields: type, resource. It selects an observed identifier
  for a canonical resource.
- response_value fields: type, value_name. It selects an observed value from a
  named response monitor.
- request_body fields: type. request_body is the structural request-body node.
object and request_body are system-managed and must never be proposed.
resource_identifier and response_value are also system-selected: request them
only by putting a supplied R alias in the change's reference field. Never emit
their direct strategy shape, invent a resource/value name, database identifier,
or raw input_node_id.

Constraint directory: exact recursive shapes and limits
Value expressions are:
- input_value fields: type, input. Shape:
  {"type":"input_value","input":"query.limit"}.
- literal fields: type, value. Shape:
  {"type":"literal","value":10}.
- arithmetic fields: type, operator, left, right. operator is one of +, -, *,
  or /, and left/right are value expressions.
Boolean expressions are:
- present fields: type, input. It tests whether the semantic input is included.
- compare fields: type, operator, left, right. operator is one of ==, !=, <,
  <=, >, or >=; left/right are value expressions.
- matches fields: type, value, pattern. value is a string value expression and
  pattern is a valid regular expression no longer than 2000 characters.
- implies fields: type, condition, consequence. Both children are boolean
  expressions.
- cardinality fields: type, expressions, minimum, maximum. expressions has
  1-100 boolean expressions; 0 <= minimum <= maximum <= expression count.
- and/or fields: type, expressions. type is "and" or "or"; expressions has
  1-100 boolean expressions.
- not fields: type, expression. expression is one boolean expression.
A proposal contains at most 20 top-level constraints. All present/input_value
references use a supplied semantic input in the input field. Never use
input_node_id. Arithmetic and ordered comparisons must use compatible numeric
types; matches must use a string-compatible value.

Construction steps
1. Read the immutable Failure Solve Patch requirement.
2. Select the smallest Generator, Constraint, or combined change satisfying
   every requirement.
3. Return action=propose with the entire patch, never an incremental edit.
4. The runtime validates input scope, schemas, constraints, provisional
   compatibility, and generates the requested number of parameter value groups.
5. Review values, present flags, and reference pool values supplied by the
   runtime. Return action=accept only after they satisfy every task requirement.
   Otherwise return a complete replacement patch.

Minimal examples
Exact value:
{"action":"propose","patch":{"changes":[{"input":"query.limit",
"strategy":{"type":"constant","value":10}}],"constraints":[]}}
Range and inclusion:
{"action":"propose","patch":{"changes":[{"input":"query.limit",
"inclusion_probability":1,"strategy":{"type":"integer_range","minimum":1,
"maximum":100}}],"constraints":[]}}
Regex text:
{"action":"propose","patch":{"changes":[{"input":"query.code","strategy":
{"type":"regex","pattern":"^[A-Z]{3}$","min_length":3,"max_length":3}}],
"constraints":[]}}
Observed identifier:
{"action":"propose","patch":{"changes":[{"input":"path.projectId",
"reference":"R1"}],"constraints":[]}}
Relationship:
{"action":"propose","patch":{"changes":[],"constraints":[{"expression":
{"type":"implies","condition":{"type":"present","input":"query.end"},
"consequence":{"type":"present","input":"query.start"}}}]}}
Arithmetic relationship:
{"action":"propose","patch":{"changes":[],"constraints":[{"expression":
{"type":"compare","operator":"<=","left":{"type":"input_value",
"input":"query.start"},"right":{"type":"arithmetic","operator":"-",
"left":{"type":"input_value","input":"query.end"},"right":{"type":"literal",
"value":1}}}}]}}
Cardinality:
{"action":"propose","patch":{"changes":[],"constraints":[{"expression":
{"type":"cardinality","expressions":[{"type":"present","input":"query.a"},
{"type":"present","input":"query.b"}],"minimum":0,"maximum":1}}]}}
Acceptance after samples:
{"action":"accept"}

Restrictions
Use only supplied semantic inputs and R aliases. Do not call tools, send HTTP,
write the catalog, persist state, or emit prose.
""".strip()


@dataclass(slots=True, frozen=True)
class ParameterPatchPrompt:
    """
    Carry validated prompt data across one isolated Solve-owned Patch attempt.

    The annotated fields form the contract; validation rejects missing, extra, or
    incorrectly typed values at the boundary.
    """
    system: str
    user: str
    reference_by_alias: dict[str, AvailableReferenceOption]


def build_parameter_patch_prompt(
    *,
    task: ParameterPatchTask,
    config: OperationGeneratorConfig,
    reference_options: list[AvailableReferenceOption],
    model: LLMModelConfig,
    system_prompt: str | None = None,
) -> ParameterPatchPrompt:
    """
    Build the complete prompt for one Solve-owned Patch requirement.

    The annotated arguments and return type define the data boundary used by callers.
    """
    semantic = build_semantic_input_map(config)
    configs = {item.input_node_id: item for item in config.configs}
    current = {
        handle: {
            "inclusion_probability": (
                configs[node_id].inclusion_probability
            ),
            "strategy": configs[node_id].strategy.model_dump(mode="json"),
        }
        for handle, node_id in semantic.node_by_handle.items()
        if handle in task.affected_inputs
    }
    references = {
        f"R{index}": option
        for index, option in enumerate(
            [
                option
                for option in reference_options
                if option.input_node_id
                in {
                    semantic.node_by_handle[handle]
                    for handle in task.affected_inputs
                }
            ],
            start=1,
        )
    }
    reference_view = [
        {
            "alias": alias,
            "input": semantic.handle_by_node[option.input_node_id],
            "kind": option.kind,
            "value_count": option.value_count,
        }
        for alias, option in references.items()
    ]
    task_card: dict[str, Any] = {
        "task": task.model_dump(
            mode="json",
            exclude={"prior_attempts"},
        ),
        "current_generation": current,
        "reference_sources": reference_view,
    }
    fitted = fit_prompt_context(
        required=task_card,
        history=task.prior_attempts,
        model=model,
    )
    return ParameterPatchPrompt(
        system=system_prompt or EXPERT_SYSTEM_PROMPT,
        user=json.dumps(
            fitted.payload,
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        reference_by_alias=references,
    )
