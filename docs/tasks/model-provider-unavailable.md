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

No real model, GitLab, or other external request was made. The implementation
remains uncommitted in its dedicated feature worktree pending separate Git
authorization.
