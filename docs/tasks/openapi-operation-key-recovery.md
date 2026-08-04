# OpenAPI Operation Key Recovery

## Status

Implemented and verified in `codex/openapi-operation-key-candidates`; Git
preservation is not yet authorized.

## Objective

Help model callers use RESTScope's exact `METHOD /path` operation keys without
restricting a lookup to the current operation or silently accepting aliases.
When an unknown spelling is supplied, return a bounded list of the closest
real keys so the caller can retry explicitly.

## Live evidence and decision

The authorized GitLab run
`gitlab-projects-five-20260804T093813Z-087bbc4b` recorded five recoverable
`openapi.get_input_schema` failures. Failure Solve received the exact current
key `POST /api/v4/projects`, but the tool Schema described `operation_key` only
as a non-empty string. DeepSeek tried `createProject`, `create_projects`,
`post_api_v4_projects`, and `createProjectV4`; the not-found result supplied no
real alternatives, so it kept guessing naming styles.

The user decided that `operation_key` must remain unrestricted and must not use
an enum or const. Every OpenAPI lookup that accepts the parameter now explains
the exact `METHOD /path` format. A failed exact lookup ranks current IR
operations against the supplied spelling using each operation's real key,
OpenAPI operationId, and summary, but returns only up to ten real operation
keys. This is error recovery, not alias resolution or automatic selection.

## Non-goals

- No current-operation restriction in Failure Solve.
- No operationId compatibility, alias persistence, operation-listing tool,
  database state, dependency, or configuration field.
- No change to successful exact lookup results.
- No GitLab, DeepSeek, Phoenix, HTTP Probe, or other live call during repair
  verification.

## Verification

- Focused OpenAPI lookup, Failure Solve, Toolbox, Tool Context, and workflow
  boundary suite: `81 passed`.
- Complete evaluation-enabled suite:
  `uv run --group evaluation pytest -q` → `685 passed, 5 skipped`.
- `uv run python -m compileall -q restscope tests` passed.
- `git diff --check` passed.
- No real GitLab, DeepSeek, Phoenix, HTTP Probe, or other live call was made.
