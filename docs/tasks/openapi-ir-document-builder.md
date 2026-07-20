# OpenAPI IR Document Builder

Status: Completed

## Objective

Build a normalized OpenAPI document from an `OpenAPISpecIR` and one or more
explicitly selected operations.

## Approved scope

- Expose a generic builder that accepts an operation-key sequence and returns a
  Python dictionary.
- Generate OpenAPI 3.1.0 from typed IR for Swagger 2 and OpenAPI 3 inputs.
- Inline ordinary schemas and retain only the minimum components needed for
  recursive schemas and security schemes.
- Preserve security AND/OR grouping and effective per-operation servers.
- Omit callbacks and response links to keep the output operation set explicit.
- Merge raw attributes for every existing raw-bearing IR node while keeping
  typed IR fields authoritative.
- Preserve unmodeled JSON Schema keywords recursively, including raw-only
  `multipleOf`, and normalize all emitted schema fragments to OpenAPI 3.1.
- Inline local schema and example component references found in raw data;
  retain the minimum schema component closure only when recursion requires it.
- Filter non-schema raw data through OpenAPI 3.1 node allowlists while
  retaining `x-*` extensions and removing Swagger 2 or parser-internal fields.

## Non-goals

- Complete lossless source-document round trips.
- Adding raw storage to Operation, PathItem, Meta, or Server IR.
- Restoring callbacks or response links.
- Adding a typed `SchemaIR.multiple_of` field.
- Producing JSON or YAML strings.
- Integrating the builder into OperationTestAgent or Schemathesis MCP.
- Changing database models or creating a Git commit.

## Decisions

- Duplicate operation keys are deduplicated in first-seen order; an empty
  selection or any unknown key fails the entire build.
- Operations sharing a path are grouped under one Path Item.
- Typed fields are authoritative. A typed `None` or empty collection actively
  removes any corresponding raw value; boolean fields retain their typed
  semantics rather than falling back to raw.
- Schema raw preserves all unmodeled JSON Schema attributes. Known schema
  substructures are recursively normalized rather than copied opaquely.
- Non-schema raw preserves only node-legal OpenAPI 3.1 fields not represented
  by typed IR plus `x-*` extensions.
- Local schema and example component references in raw are inlined. Recursive
  schema references use a minimal components closure; external, wrong-kind,
  and missing references fail explicitly.
- Raw dictionaries are deep-copied and never mutated by document generation.

## Verification

Observed on 2026-07-20 in the isolated worktree:

- `uv run pytest -q tests/test_openapi_document_builder.py tests/test_restart_cleanup.py tests/test_openapi_catalog.py tests/test_operation_agent_mvp.py`
  — `30 passed`.
- `uv run pytest -q` — `92 passed`.
- `uv run python -m compileall -q restscope` — exited successfully.
- `git diff --check` — exited successfully.

The implementation was initially left uncommitted as required. The user later
authorized committing both feature worktrees and merging them into `main`.
