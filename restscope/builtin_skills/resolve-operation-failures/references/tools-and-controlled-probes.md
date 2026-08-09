# Tools and controlled probes

## Select the smallest evidence call

Use the OpenAPI Tools to learn declared structure:

- `openapi.list_inputs` finds candidate inputs.
- `openapi.get_input_schema` inspects one returned input.
- `openapi.list_response_fields` finds a candidate field for a Failure status.
- `openapi.get_response_field_schema` inspects one returned response field.

Use the Test Case Tools to learn actual runtime facts:

- `test_case.get_parameter_value` reads one Parameter from named Test Cases.
- `test_case.find_parameters_by_value` finds other request locations carrying
  an exact typed value.
- `test_case.get_response_field_value` reads one failed response field.
- `test_case.find_response_fields_by_value` finds other response locations
  carrying an exact typed value.

Never treat an OpenAPI field as observed merely because it is declared. Never
invent a semantic handle or response field when a listing Tool returned none.

Call `lookup_parameter_history` for every affected input before delegating a
Patch. Applied Patches and conflicts are compatibility evidence. An old
`no_patch` outcome is not a value rule.

Read-only evidence calls may be grouped when their questions are independent.
A worklist write or HTTP Probe must be the only Tool call in its model output.

## Design a causal Probe

Probe only when read-only evidence leaves at least two plausible causes and one
bounded request can distinguish them. Before calling `restscope.http.request`:

1. State the active `WI-*` and the competing hypotheses.
2. Predict which exact Failure will appear, disappear, or change.
3. Start from the failed request's known values and presence.
4. Change only the inputs required by that prediction.
5. Preserve every other known input, including explicit absence.
6. Use the exact current operation method and a concrete path matching its
   template.
7. Decide whether the expected evidence is worth the target-state effect.

The Probe executes every time. POST, PUT, PATCH, DELETE, and repeated requests
may mutate the target and are not rolled back. Tool availability does not grant
authorization for a live external action; obtain that authorization through
the enclosing workflow before calling it.

## Interpret the result narrowly

Compare the observed result with the prediction. A matching result strengthens
the tested causal hypothesis; a mismatch requires revising or splitting it.
Do not generalize one Probe beyond the changed inputs and observed target state.

Do not use a successful request as an acceptance criterion. The Patch must be
reviewed against the required values, presence, and relationships. A later
complete Smoke Batch measures target effects after an applied Patch.
