# Structured Test Case Request JSON

Status: Completed

Merged into local `main` on 2026-08-03.

## Objective

Give OpenAPI lookup, deterministic Testing, and the run-local Test Case Catalog
one shared semantic request-input Interface. Store each Test Case request as
direct-name JSON while retaining unique handles such as `query.sort` for
cross-tool and Agent references.

## Approved scope

- Add the pure in-memory `restscope.request_inputs` Module without a registry or
  persistence.
- Replace flattened Catalog Parameter maps with `path`, `query`, `header`,
  `cookie`, and optional `body` JSON.
- Return selected request and response JSON fragments from the existing five
  single-purpose tools without compatibility aliases.
- Normalize generated Batch cases and current-operation HTTP Probes into the
  same Catalog request shape.
- Explain direct JSON names versus semantic handles in Dedup and Solve prompts.
- Preserve the user's existing Dedup prompt deletion as part of this change.

## Decisions

- `RequestInputReference` owns handle construction, request lookup, and request
  fragment projection. OpenAPI IR and Testing snapshots remain separate source
  Adapters.
- Tool inputs and final Agent decisions continue to use semantic handles.
  Structured JSON uses the direct Parameter or property name inside its request
  location.
- An omitted Body has no `body` key; an explicitly sent JSON null has
  `"body": null`.
- Object paths return minimal ancestry. Once a path enters an array, the
  smallest complete real array container is retained rather than fabricating
  placeholder values.
- The MVP assumes Parameter and property names do not contain dots or square
  brackets. It adds no escaping, detection, or rejection behavior.

## Non-goals

- No complete-Test-Case tool or initial-prompt request dump.
- No Memory schema, database migration, tool registry, compatibility alias, or
  live external call.
- No change to semantic handles for ordinary MVP input names.

## Verification

Fresh local verification on 2026-08-03:

- Focused request-input, Catalog, Batch, Probe, Dedup, Solve, Coordinator,
  transport, HTTP-tool, and package-boundary tests: `123 passed`.
- Complete local suite: `558 passed, 5 skipped`.
- Offline Operation Smoke Agent evaluations: `13 passed`.
- Python compilation for `restscope`, `tests`, and `evaluations`: passed.
- `git diff --check`: passed.

No real LLM, target API, Phoenix, GitLab, or other external service was called.
