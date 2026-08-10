# Evidence Tools and controlled Probes

Use `openapi.list_inputs` and `openapi.list_response_fields` for discovery, then
query only exact relevant nodes with the Schema Tools. OpenAPI data explains
declared structure but does not prove causality.

Use `request_generation.get_input_state` to inspect the actual current
Generator state and complete Constraint closure. A state digest identifies
content; it does not prove target behavior.

Use the inline output of `test_case.run_batch` as the actual generated request
and outcome. Case numbers are local to that result and must not be treated as
stable references. Read-only evidence calls may be grouped when independent.

Send a controlled `restscope.http.request` only when existing evidence cannot
distinguish competing hypotheses and the external action is authorized:

1. Start from one failed inline request.
2. Predict whether a specific failure will remain, disappear, or change.
3. Change only inputs needed by that hypothesis; preserve all other known
   values and presence states.
4. Use the current operation's exact method and a concrete path matching its
   template.
5. Record that POST, PUT, PATCH, and DELETE may permanently change target state
   and are not rolled back.
6. Compare the result with the prediction. Do not treat a successful Tool call
   as a successful HTTP outcome.

After a Patch is confirmed in Generation Store, run a new full
`test_case.run_batch`. That Batch measures target effect at the new frozen
revision; it does not retroactively prove the semantic review.
