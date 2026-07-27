# Operation Smoke Effect Response Evidence

Status: Implemented and locally verified

## Objective

Give the Effect Evaluator enough real HTTP evidence to distinguish an unchanged
parameter failure from a new failure that appears after the original parameter
has been corrected.

## Approved scope

- Pass baseline and candidate run-local private case evidence into effect
  validation.
- Include every non-2xx response body and omit every 2xx response body.
- Redact model-visible evidence, cap each value at 4 KiB, and cap the combined
  baseline/candidate effect payload at 64 KiB without dropping cases.
- Teach the Effect Evaluator that an unchanged status code does not by itself
  prove that the same parameter failure persists.

## Non-goals

- Adding response bodies to `OperationExecutionReport` or `ResponseSummary`.
- Persisting raw response bodies or changing the existing 1 MiB transport
  capture limit.
- Changing Patch schemas, Group acceptance rules, Provider interfaces, or the
  OpenAPI document.
- Running the Project API live test without separate authorization.

## Decisions

- Private response evidence remains App-lifetime-only and is joined to public
  cases only while constructing the Effect Evaluator prompt.
- The tracing runtime's shared `Redactor` is applied before the evidence is sent
  to the Provider or recorded by Phoenix.
- When forty large failure cases compete for the shared prompt budget, a
  uniform preview limit preserves every case and shortens all large values
  fairly.
- Missing, empty, text, invalid-JSON, and transport-truncated bodies remain
  distinguishable through explicit availability, size, and truncation fields.

## Verification

- The initial focused tests failed because `validate_effect()` did not accept
  private evidence and Operation Smoke did not pass candidate evidence.
- After implementation, the five focused response-evidence and orchestration
  tests passed.
- The initial related-suite run exposed one stale test fixture that cited `F1`
  where the current Effect protocol requires candidate reference `CF1`; the
  fixture was corrected.
- Related Operation Smoke and observability suites: 52 passed.
- Full suite: 500 passed, 4 skipped.
- `python -m compileall -q restscope tests`: passed.
- `git diff --check`: passed.

The Project API and Phoenix were not contacted, so live Effect Evaluator
behavior remains intentionally unverified.
