# Composite Resource Identifier Discovery

Status: Implemented and locally verified; uncommitted

## Objective

Make Resource Identifier discovery match observable REST paths: inspect only
the response level that can represent the resource itself, give one System
Agent all bounded field and full-path evidence, and preserve multi-component
identifiers as complete ordered records through storage and request generation.

## Approved behavior

- A root object contributes only its direct fields at `$`. A root array
  contributes only each object item's direct fields at `$[]`. Nested objects,
  nested arrays, and arrays inside wrapper objects are never traversed.
- Only observed non-blank strings and integers become candidates. Booleans,
  floats, nulls, containers, and schema-only fields do not. OpenAPI Schema may
  add a description or format only to an already observed candidate.
- Every first decision, including a field named `id`, goes through the
  registered no-Tool `resource-identifier-selector` System Agent. A learned
  operation/group rule is reused without another model call.
- One task contains every candidate field and every related full OpenAPI path.
  The limits are 100 fields, 100 paths, and 20,000 rendered characters. If any
  complete evidence set does not fit, monitoring returns a warning and makes no
  partial or batched decision.
- Path evidence includes the current path when it contains a placeholder and a
  longer path only when the current path is its full-segment prefix and every
  added segment is a placeholder. Paths are deduplicated across HTTP methods.
- The Agent may return no identifier, one field with no path, or ordered fields
  for a supplied full path. Harness validation requires known unique aliases,
  a supplied path, and exactly one field for every placeholder in full-path
  order. Specific correction feedback continues without a retry limit.

## Domain and persistence

An **Identifier Definition** belongs to one resource and gives the identifier a
stable name plus one or more ordered component names. An **Identifier Record**
is one complete ordered tuple of typed component values observed together in a
single root object or root-array item.

The database baseline now has a `resource_identifier_definitions` table.
Operation rules reference a definition and store the chosen full path plus
ordered response-field mappings. `resource_identifiers` stores complete JSON
tuples and a type-sensitive digest. A missing or invalid component skips the
whole record and produces a warning; partial tuples are never stored. There is
no migration path for an older exploratory database.

`resource.list_ids` returns the definition name and ordered components only.
`ResourceIdentifierGenerator` names `resource`, `identifier`, and `component`.
A composite definition may bind only path parameters, and one Parameter Patch
must bind every component exactly once. Batch snapshotting freezes complete
records; deterministic generation selects one record per resource/definition
and assigns all components atomically. Constraint solving rejects assignments
that combine components from different observed records.

## Cross-cutting Python rule

Production code and tests may not import, alias, or qualify `typing.Any`.
Recursive JSON data uses the shared `JSONValue`/`JSONObject` types; deliberately
opaque external values use `object`; behavioral collaborators use concrete
contracts, generics, or Protocols. An AST test enforces the prohibition across
the Python repository.

## Verification

Required final checks:

```text
uv run pytest -q tests/test_resource_identifier_tracker.py \
  tests/test_resource_identifier_composites.py \
  tests/test_resource_catalog.py tests/test_resource_lookup_tools.py \
  tests/test_generic_batch_tool.py tests/test_no_typing_any.py
uv run pytest -q
uv run python -m compileall -q restscope tests
uv run alembic upgrade head
git diff --check
```

The opt-in live Provider test, a live target API, and external observability
services are outside local verification. No commit, merge, push, PR, branch
deletion, or worktree deletion is authorized by this implementation request.

## Verification results

Executed in the dedicated feature worktree on 2026-08-10:

```text
focused Resource Identifier, persistence, Tool, Batch, solver, boundary, and
Any-rule tests
55 passed

uv run pytest -q
555 passed, 14 skipped

uv run python -m compileall -q restscope tests
passed

fresh Alembic upgrade plus SQLite integrity checks
integrity_check=ok; foreign_key_check=[]; business_tables=14

production direct client.invoke scan
only restscope/agent/runtime.py

retired Resource Identifier name/selector/scalar compatibility scan
no matches

git diff --check
passed
```
