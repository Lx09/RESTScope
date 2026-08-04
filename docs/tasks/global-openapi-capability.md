# Global OpenAPI Capability

Status: Implemented and locally verified

## Objective

Replace the operation-bound full OpenAPI projection with four compact,
read-only tools that query the App's current in-memory OpenAPI IR by exact
operation key:

- `openapi.list_inputs` lists paginated semantic request handles without
  Schemas;
- `openapi.list_response_fields` lists paginated semantic response-body field
  handles for one response status without Schemas;
- `openapi.get_input_schema` returns one exact request-input Schema summary;
- `openapi.get_response_field_schema` returns one exact response-body field
  Schema summary.

The change is intended to reduce prompt tokens while allowing Failure Solve to
request only the contract evidence needed for its current diagnosis.

## Approved scope

- Keep all four tools in one shared `OpenAPICapability` Module.
- Read the current IR from the trusted App `ToolContext` at tool execution.
- Require an exact `operation_key` in every model call.
- Keep Agent registrations outside this extension; later task records remain
  authoritative for the tools currently available to each Agent.
- Paginate input and response-field listing, and bound Schema literals and
  error choices.
- Match response status as exact, class wildcard, then `default`.
- Normalize concrete response array indexes to semantic `[]` handles while
  preserving OpenAPI combiner branch indexes.
- Remove the old `openapi.lookup_operation` tool without a compatibility alias.

## Non-goals

- No executable App-wide tool registry or automatic tool injection.
- No fuzzy Operation search, `operationId` lookup, or concrete-path matching.
- No recursive full-Schema response, response Header lookup, or raw OpenAPI
  exposure.
- Do not register `openapi.list_response_fields` with an existing Agent in this
  change.
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
- `openapi.list_response_fields` accepts only `operation_key`, `status_code`,
  `offset`, and `limit`. It defaults to 100 results, accepts at most 200, and
  requires the selected response to have one unambiguous Schema-bearing media
  type.
- Exact-node Schema summaries retain structural and validation keywords but
  omit descriptions, examples, raw Schema data, security, and sibling fields.

## Verification

- Focused OpenAPI, ToolContext, and package-boundary contracts: `24 passed`.
- Full local suite: `570 passed, 18 skipped`. The skips require explicitly
  enabled live integrations.

No live LLM, target API, external validator, or local Phoenix call was made.
Local commit, merge, and cleanup were explicitly authorized on 2026-08-04.
