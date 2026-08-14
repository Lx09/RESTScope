# Database Query Tool and Purpose-Specific Skill

Status: Completed and merged into local `main` at `a8be8fc`

## Objective

Give the Orchestrator and Task Executor one bounded, read-only SQLite query
Tool plus a standard Skill that selects and lazily loads purpose-specific
database query guidance.

## Approved scope

- Add `database.query` for the current App-owned SQLite database only.
- Accept bounded parameterized `SELECT` and `WITH ... SELECT` statements.
- Deny every database mutation, schema/lifecycle operation, attached database,
  extension load, multi-statement call, and over-time query.
- Return bounded positional rows with explicit columns and truncation reasons.
- Allow Observation response bodies and complete Resource instance JSON with
  ordinary output limits and no content redaction.
- Allow complete response-header mappings only as a single output column;
  verify they are exact stored mappings and redact sensitive header values.
- Add `query-restscope-database` with seven independently readable References.
- Grant the Tool and Skill to Orchestrator and Task Executor only.

## Non-goals

- No external database, new configuration, schema migration, database write,
  persistent plan, recovery state, or general Agent memory.
- No database access for Parameter Patch, Resource Identifier, or Resource
  State Profiles.
- No replacement of `test-progress` as the Orchestrator's default summary.
- No Git commit, merge, push, branch deletion, or Worktree cleanup without
  separate authorization.

## Decisions

- The approved capability narrowly supersedes ADR 0007's no-Tool/no-Skill
  Orchestrator clause while preserving its no-child and scheduling ownership.
- SQLite's authorizer and progress handler are the execution safety seam.
- Arbitrary SQL does not preserve response-header provenance, so the only safe
  header result is one complete stored mapping that the Tool can verify and
  redact after execution.

## Verification

- Feature-Worktree verification: `uv run pytest -q` reported 657 passed and 13
  skipped.
- Merged-`main` verification: `uv run pytest -q` reported 676 passed and 2
  skipped.
- `uv run ruff check restscope tests`: all checks passed.
- `uv run python -m compileall -q restscope tests`: passed.
- `uv run pytest -q tests/test_no_typing_any.py`: 1 passed.
- Skill Creator `quick_validate.py` on `query-restscope-database`: valid.
- `git diff --check`: passed.

No real model, target API, external database, or other live service was called.
Commit `a8be8fc` was fast-forwarded into local `main`; its feature Worktree and
branch were removed after merged-result verification.
