# OpenAPI Parser Postprocessing Reduction

Status: Completed

## Objective

Reduce parser postprocessing to operation lookup indexes and remove inferred
resource, constraint, value-flow, operation-card, and flow-graph contracts.

## Approved scope

- Keep only `by_operation_id` and `by_method_path` in `SpecIndexesIR`.
- Remove resource grouping, constraint tags, value indexes, operation cards,
  and flow graphs from parsing, the IR, and public postprocess exports.
- Keep the standalone `schema_sync` utilities.
- Make the change without a compatibility or deprecation layer.

## Non-goals

- Changing operation parsing or operation identity.
- Changing database models or migrations.
- Removing `schema_sync`.
- Creating a Git commit.

## Decisions

- `build_operation_indexes` remains part of the parser pipeline.
- Duplicate `operationId` values continue to produce a
  `DUPLICATE_OPERATION_ID` warning while retaining the first lookup entry.
- Removed IR fields and types are unavailable rather than present as empty
  compatibility values.

## Verification

Observed on 2026-07-19:

- `uv run pytest -q tests/test_openapi_parser_postprocessing.py tests/test_restart_cleanup.py tests/test_schema_catalog.py`
  — `18 passed`.
- `uv run pytest -q` — `66 passed`.
- `.venv/bin/python -m compileall -q restscope` — exited successfully.
- A source and documentation scan found no remaining references to the removed
  postprocessing functions, fields, or IR types outside historical task context.
- `git diff --check` — exited successfully.

The implementation remains uncommitted as required by the approved scope.
