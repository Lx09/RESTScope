# LLM-led Operation Smoke Live Verification Proposal

Status: Proposal only; not authorized and not executed

## Current blockers

The current checkout has no configured THINK or FAST model name. The target at
`http://127.0.0.1:37001` is historical local-test context and has not been
confirmed active or disposable for this task. No Live command may run until
the user confirms both exact model names and the target.

## Proposed bounded run

- OpenAPI source:
  `assets/openapi/project_swagger.yaml`
- Target:
  `http://127.0.0.1:37001`
- Operation:
  exact method/template `GET /app/api/projects/{id}`
- Model roles:
  the user-confirmed THINK model for Plan, Solve, and Effect; the
  user-confirmed FAST model for Parameter Patch
- Smoke settings:
  `case_count=3`, explicit recorded seed, 2xx threshold `0.8`,
  `max_plan_outputs=4`, `max_solve_outputs_per_todo=10`,
  `max_patch_outputs=6`, `max_effect_outputs=2`,
  `continuation_interval=10`
- HTTP hard boundary:
  all generated batches and Solve probes are limited to the exact GET
  operation above. A Live-only counting transport must stop after 50 total
  target requests so model-generated tool batches cannot exceed the approved
  cap.

## Data sent to models

The selected operation IR and Generator config, three generated requests,
generated values and presence information, complete bounded response bodies,
Plan/Solve/Patch/Effect history, HTTP probe observations, local Patch samples,
and available reference-pool metadata may be sent to the configured model
providers. Authorization, Cookie, proxy authorization, and API-key request
headers remain redacted/injected outside model control.

Target evidence can contain project identifiers, names, or other sensitive
application values. Model-provider retention and processing terms must be
acceptable before authorization.

## Expected side effects

The scoped target operation is GET, so no target mutation is expected, but the
server may log requests. RESTScope will write local Generator candidate
revisions and Behavior Monitor evidence to its disposable Live database.
Accepted runtime Constraints remain App-memory only and are cleared on close.
No repository commit, push, or branch operation is part of Live verification.

## Acceptance evidence

The Live report must show:

- one fixed Plan todo snapshot whose case codes are expanded downstream;
- a continuous Solve trace with any HTTP probes confined to the exact GET;
- a Patch review using three local samples;
- a complete same-seed candidate batch and Effect output;
- atomic candidate accept or rollback;
- a final complete-batch 2xx result; and
- no raw response body in the public `OperationSmokeResult`.

Any expansion of operation, target, model, request cap, exposed data, or
side-effect risk requires a new explicit authorization.
