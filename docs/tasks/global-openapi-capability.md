# Global OpenAPI Capability

Status: Implemented and locally verified

## Objective

Replace the operation-bound full OpenAPI projection with three compact,
read-only tools that query the App's current in-memory OpenAPI IR by exact
operation key:

- `openapi.list_inputs` lists paginated semantic request handles without
  Schemas;
- `openapi.get_input_schema` returns one exact request-input Schema summary;
- `openapi.get_response_field_schema` returns one exact response-body field
  Schema summary.

The change is intended to reduce prompt tokens while allowing Failure Solve to
request only the contract evidence needed for its current diagnosis.

## Approved scope

- Keep all three tools in one shared `OpenAPICapability` Module.
- Read the current IR from the trusted App `ToolContext` at tool execution.
- Require an exact `operation_key` in every model call.
- Register only input listing for Failure Dedup and all three tools for Failure
  Solve through their Agent-owned toolboxes.
- Paginate input listing and bound Schema literals and error choices.
- Match response status as exact, class wildcard, then `default`.
- Normalize concrete response array indexes to semantic `[]` handles while
  preserving OpenAPI combiner branch indexes.
- Remove the old `openapi.lookup_operation` tool without a compatibility alias.

## Non-goals

- No executable App-wide tool registry or automatic tool injection.
- No fuzzy Operation search, `operationId` lookup, or concrete-path matching.
- No recursive full-Schema response, response Header lookup, or raw OpenAPI
  exposure.
- No new persistence, database change, live LLM call, or target API request.
- No commit, merge, push, branch deletion, or worktree cleanup without separate
  authorization.

## Decisions and assumptions

- Tool context is injected into `OpenAPICapability` out of band; IR never enters
  model arguments, prompts, or tool results.
- A missing media type is inferred only when one JSON Schema or one total
  Schema-bearing media type is available. Ambiguous contracts fail closed.
- `openapi.list_inputs` defaults to 100 results and accepts at most 200 per
  call. Ordinary HTTP Parameters remain visible when a request media filter is
  supplied.
- Exact-node Schema summaries retain structural and validation keywords but
  omit descriptions, examples, raw Schema data, security, and sibling fields.

## Verification

- Focused OpenAPI, Dedup, Solve, ToolContext, and tracing contracts:
  `36 passed`.
- Operation Smoke, ToolContext, HTTP, package-boundary, App bootstrap, and
  tracing integration regressions: `104 passed`.
- Offline Operation Smoke evaluation contracts: `13 passed`.
- Full local suite: `550 passed, 5 skipped`. The skips require explicitly
  enabled live GitLab, Swagger Validator, DeepSeek, or local Phoenix runs.
- Full Python compilation and `git diff --check`: passed.

No live LLM, target API, external validator, or local Phoenix call was made.
The implementation is ready for the explicitly authorized local Git lifecycle.
