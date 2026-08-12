# Harness Runtime Navigation Cleanup

Status: Complete

## Objective

Make the concrete `HarnessRuntime` the only Harness object accepted by
`RESTScopeApp`. Remove the duplicate private Protocol, dynamic method probing,
partial runtime test doubles, and the last retired target-HTTP field name.

## Approved decisions

- Keep `HarnessRuntime`, `build_harness()`, and the Harness facade public.
- App injection accepts a caller-owned concrete `HarnessRuntime`, not an object
  that merely has some similarly named methods.
- Tests use real Harness instances and vary Agent behavior through the existing
  Agent runtime definition.
- Rename `target_http_tool` to `http_request_tool` without renaming the accurate
  `TargetHTTPRequestTool` class.

## Non-goals

- No change to Harness responsibilities, Agent behavior, Tool Schema,
  persistence, or runtime ordering.
- No global Protocol cleanup, compatibility aliases, or runtime type checks.
- No Git staging, commit, or push.

## Verification

- Concrete Harness construction and App adoption.
- App context, Main Agent, UI, tracing, MCP Host, and cleanup behavior.
- Package navigation, full tests, `typing.Any` guard, compilation, and diff
  hygiene.

Fresh results on 2026-08-12:

- Cross-module App/Harness/Agent/MCP/UI suite: 129 passed, 1 skipped.
- `uv run pytest -q`: 561 passed, 2 skipped.
- `typing.Any` guard, Python compilation, retired-name scan, and
  `git diff --check` passed.
