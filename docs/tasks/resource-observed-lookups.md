# Resource and Observed Response Field Lookups

## Status

Implemented and freshly verified in `codex/resource-observed-lookups`.
The feature remains uncommitted pending separate Git authorization.

## Objective

Add three paginated read-only public tools without registering them with an
Agent: `resource.list_resources`, `resource.list_ids`, and
`openapi.find_observed_response_fields`.

## Approved behavior

- Resource listing returns canonical names only.
- Identifier lookup accepts a canonical resource or alias and returns only
  typed values; an unknown resource is a successful empty result.
- Observed response-field lookup intersects retained successful scalar
  observations with the current OpenAPI IR, applies deterministic high-
  precision name matching, and groups one page of fields by response contract.
- Request inputs continue to use the shared `RequestInputReference`; response
  selectors and handles gain one shared reference Interface.
- All lists use `offset=0`, `limit=100`, and a maximum limit of 200.

## Non-goals

- No Agent tool registration or prompt change.
- No vector search, embedding dependency, response values, Schema output, or
  database migration.
- No live LLM, GitLab, Phoenix, or other external call.
- No feature commit, merge, push, or cleanup without separate authorization.

## Verification

- Focused Resource, Response Value, OpenAPI, runtime, Agent-toolbox, and package
  boundary suite: `184 passed`.
- Regression retest after restoring fail-closed dotted-property handling:
  `50 passed`.
- Full suite: `uv run --group evaluation pytest -q` reported `672 passed,
  5 skipped`.
- `uv run python -m compileall -q restscope tests evaluations` passed.
- `git diff --check` passed.
- No live external service was called.
