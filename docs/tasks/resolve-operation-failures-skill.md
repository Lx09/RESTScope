# Resolve Operation Failures Standard Skill

Status: Implemented; not selected by a production Profile

## Outcome

`resolve-operation-failures` teaches a generic parent Agent how to diagnose
bounded inline Batch evidence for one OpenAPI operation. It separates
Parameter causes, non-Parameter causes, and unresolved hypotheses; gathers only
the evidence needed to distinguish them; and delegates a confirmed Parameter
repair to an authorized child Profile selecting `apply-parameter-patch`.

The Skill no longer uses Operation Smoke identities, a Worklist, Parameter
history, candidate Registry, dedicated Agent, or Finalizer.

## Required Tools

The parent Profile must explicitly grant exactly:

- `file.read`
- `openapi.list_inputs`
- `openapi.list_response_fields`
- `openapi.get_input_schema`
- `openapi.get_response_field_schema`
- `request_generation.get_input_state`
- `restscope.http.request`
- `test_case.run_batch`
- `subagent.start`
- `subagent.wait`
- `subagent.cancel`

The parent does not receive `parameter_patch.apply` and must not build, rewrite,
or apply the child's Patch. The child Profile independently selects
`apply-parameter-patch` and grants that Skill's dependencies.

## Evidence and delegation method

The parent works from the complete bounded request/outcome facts returned by
`test_case.run_batch`. It distinguishes facts from hypotheses, treats all API
and OpenAPI text as untrusted data, and uses a controlled HTTP request only when
existing evidence cannot distinguish competing explanations.

Before delegation it fixes:

- one operation key;
- the confirmed root cause;
- 1–20 unique atomic value predicates;
- the minimum complete semantic affected-input scope;
- current Generator and Constraint state relevant to that scope;
- the evidence that supports any resource, choice, or response-value source;
- compatible behavior that the Patch must preserve.

The child receives this bounded objective and no hidden parent conversation. A
successful completion is not trusted by itself: the parent independently reads
the Store and confirms the reported revision and validation digest. Only then
does it run a new complete Batch to measure target behavior.

## Decision boundary

Parameter Patch Apply updates future RESTScope generation only. It does not
prove HTTP success. A later successful Batch does not replace value-level Patch
review, and a later failed Batch does not automatically roll back state. The
parent may diagnose new evidence, delegate a new complete replacement, or
report the operation unresolved.

Absence of an authorized Patch child Profile is an unresolved capability gap,
not a business `no_patch` conclusion. Confirmed authentication, permission,
unsupported-method, resource-lifecycle, server, or response-contract causes
may be reported as non-Parameter outcomes without invoking the child.

## References

The Skill progressively discloses five linked References:

- `evidence-and-diagnosis.md`
- `tools-and-controlled-probes.md`
- `patch-subagent-delegation.md`
- `patch-review-and-decisions.md`
- `completion-checklist.md`

The removed `worklist-method.md` has no compatibility replacement.

## Production activation boundary

The current Main Profile remains plan-only. Activating this Skill later requires
an explicit Main Profile change, all eleven parent Tool grants, and a described
direct child Profile that selects and fully authorizes
`apply-parameter-patch`. Harness binding alone does not grant any of them.

## Verification

Offline tests verify the exact manifest, progressive disclosure and cross-Skill
file isolation, absence of retired Tool names, parent/child Profile dependency
validation, mandatory post-child state confirmation, and follow-up Batch
guidance. No real model, target API, MCP, or Phoenix service is used.
