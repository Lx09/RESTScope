# Database Persistence Redesign

Status: Implemented and verified in feature worktree

## Objective

Replace the exploratory 23-table persistence model with the user-approved
19-table model.  Keep one database bound to one App run while retaining only
current OpenAPI/Generator/Constraint state and the narrow audit evidence that
has a concrete runtime or inspection consumer.

## Approved scope

- Persist one normalized current OpenAPI document and append-only response
  contract change events for inspection and export, not App recovery.
- Store current Generators per input, current executable Constraints, and
  accepted change events without revisions or repeated operation snapshots.
- Simplify Resource Identifier and Response Value tables, including bounded
  response observations and value pools.
- Replace Failure Observations, Investigation records, Parameter directory
  rows, and Applied Patch rows with stable Failures and append-only Solve
  Attempts.
- Keep Agents free of database CRUD decisions.  Deterministic runtime code
  owns diffs, replacement scope, retention, and transactions.
- Enable SQLite foreign-key enforcement and replace the one-shot database
  baseline with the final schema.
- Remove revision-oriented public results and rename Investigation-facing
  interfaces to Solve Attempt.

## Non-goals

- Reopening or recovering an existing database.
- Migrating, deleting, or modifying existing database artifacts.
- Frontend work, persisted test progress, scheduler state, plans, queues,
  Batch/Test Case history, raw responses, Patch samples, or LLM transcripts.
- Git staging, commit, merge, worktree cleanup, or branch deletion.

## Decisions

- Existing database paths remain rejected without modification.
- OpenAPI current state and response change events are audit artifacts even
  though they are not used to resume an App.
- A complete Constraint Patch replaces every old Constraint owner connected by
  direct or transitive input overlap; Generator-only Patches leave Constraints
  untouched.
- Resource identifiers are retained without a capacity limit.  Response value
  pools retain 100 recent typed values, response history retains 100
  observations per operation, and responses with more than 1000 scalar values
  are skipped in full.
- Stable Failures use operation, normalized message set, and the three-state
  suspected-input value (`null`, empty, or exact non-empty set) as identity.
- Every terminal Solve conclusion is retained.  Accepted Generator/Constraint
  changes are committed atomically with their Solve Attempt.

## Verification

- Full schema and App lifecycle focus: 52 tests passed.  The schema test checks
  all 19 tables' exact fields and primary keys plus every declared UNIQUE,
  CHECK, required index, and foreign-key target.
- Generator/Constraint conflict and Failure Solve focus: 24 tests passed before
  the final strict DTO and suspected-input boundary checks were added.
- Full suite: `uv run pytest -q` completed with `495 passed, 18 skipped`.
- `uv run python -m compileall -q restscope tests` passed.
- `git diff --check` passed.
- A fresh database initialized from the local GitLab 18.9.2 sample contained
  1740 operations, 13,850 input Generator rows, 19 business tables plus
  `alembic_version`, `integrity_check=ok`, and zero `foreign_key_check` issues.
- That measured database was 11,448,320 bytes (about 10.92 MiB).  `dbstat`
  attributed 6,078,464 bytes to `openapi_current`, 3,391,488 bytes to current
  input rows, 1,007,616 bytes to their operation index, and 724,992 bytes to
  their primary-key index.  No operation or Generator-history snapshot table
  exists.

## Remaining risks

- This is an intentional compatibility break across the persistence schema and
  several public workflow DTOs.
- Full normalized OpenAPI updates rewrite a multi-megabyte singleton document.
  The measured 1740-operation current document occupies about 5.80 MiB, so a
  real contract change deliberately pays that bounded audit-write cost.
