# Single-provider 503 Safety

Status: Completed

## Objective

Treat retryable model-provider exhaustion as a bounded, explicit technical
failure. Preserve evidence from the current Operation Smoke attempt, then stop
the whole RESTScope run before another operation can send GitLab requests.

## Evidence and decisions

The authorized GitLab live run
`gitlab-projects-five-20260804T025831Z-3bcd22d4` attempted five operations. Its
single configured DeepSeek model repeatedly returned HTTP 503 `service too
busy`; the run eventually reported every operation as errored instead of
recognizing one shared provider outage. The run recorded 50 target cases, 38
logical LLM calls, and 269 spans, with no successful mutating target request.

The user approved these current decisions on 2026-08-04:

- Use the OpenAI-compatible SDK's built-in `max_retries=3` behavior for one
  model request; do not add another retry Module or configuration field.
- After those retries, classify HTTP 408, 409, 429, every 5xx response,
  connection failures, and timeouts as `ProviderUnavailableError`.
- A DeepSeek strict request whose Beta endpoint is unavailable may try the
  standard official URL once, inside the Adapter and before any tool call is
  returned to an Agent. If the standard endpoint is also unavailable, stop the
  run through the same provider-unavailable path.
- Do not retry an Agent turn, tool call, HTTP Probe, target request, operation,
  or workflow because of provider capacity.

## Follow-up live evidence

The authorized run
`gitlab-projects-five-20260804T033632Z-b34b2f92` ended after 538.25 seconds.
It completed four Batches and 40 GitLab cases. GET Projects passed; POST
Projects preserved two Batches before a direct Failure Solve HTTP 503 stopped
the run with `provider_unavailable`, leaving the final three operations
unattempted.

Phoenix also showed two earlier provider-unavailable failures inside
`ParameterPatchReviewAgent.run`: one transport failure and one HTTP 503. The
Review and Patch Modules correctly propagated them, but `AgentToolbox`
converted each to a generic `internal_tool_error` Tool Result. Failure Solve
therefore continued instead of applying the approved global stop rule.

The approved follow-up makes provider unavailability an explicit
`AgentToolbox.execute` and `execute_many` error mode. Ordinary tool failures
remain model-safe Tool Results; a nested shared model outage retains its type
and escapes to Operation Smoke. Tool tracing records only stable outage fields
and never follows the private provider cause into a response body.

## Approved scope

- Publish the stable provider-unavailable LLM exception without copying a
  provider response body into its message or trace fields.
- Configure both standard and lazily-created DeepSeek Beta clients with the
  same SDK retry limit.
- Preserve current Operation Smoke Batch and round summaries when the model
  fails.
- Record the current Supervisor attempt as errored, skip its normal operation
  retry queue, end the run with `technical_error`, and leave later operations
  unattempted.
- Record only the stable error code, optional HTTP status, and configured retry
  limit on the LLM failure span. The SDK does not expose a reliable actual
  retry count, so no such value is invented.

## Non-goals

- No second model/provider, health probe, router, persistent circuit breaker,
  recovery snapshot, or cross-run outage memory.
- No persistence of raw model responses or exception bodies.
- No real model, GitLab, or other external request during implementation and
  verification.
- No Git commit, merge, branch deletion, push, or pull request without separate
  authorization.

## Verification

Observed locally on 2026-08-04:

- Focused LLM, Operation Smoke, Supervisor, and tracing regression suite:
  `87 passed`.
- Complete suite: `uv run --group evaluation pytest -q` reported `655 passed,
  5 skipped`.
- Workflow package boundary suite: `8 passed`.
- `uv run python -m compileall -q restscope`: passed.
- `git diff --check`: passed.

No real model, GitLab, or other external request was made during the original
implementation verification. The later authorized live run is recorded in the
follow-up evidence above.

Follow-up repair verification observed on 2026-08-04:

- Toolbox, Failure Solve, tracing, Operation Smoke, and Supervisor focused
  suite: `67 passed`.
- Complete suite: `uv run --group evaluation pytest -q` reported `659 passed,
  5 skipped`.
- Workflow package boundary suite: `8 passed`.
- `uv run python -m compileall -q restscope`: passed.
- `git diff --check`: passed.

No second live run was performed; it requires separate authorization because
it calls the real model and may mutate the disposable GitLab target.
