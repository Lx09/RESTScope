# Generalize built-in Skill language

Status: Implemented and verified; Git delivery awaits separate authorization

## Objective

Make all three built-in Skills understandable from ordinary API-testing
concepts before introducing RESTScope's internal tables, records, runtime
protocol, or request-generation DSL.

## Approved scope

- Rewrite Skill trigger descriptions and core guidance without naming runtime
  roles such as Orchestrator, Task Executor, Profile, or parent/child Agent.
- Organize References by user-facing purpose and define the RESTScope mapping
  before precise SQL, Tool, or DSL instructions.
- Rename old internal-term-first References without compatibility aliases.
- Preserve exact required Tools, risk levels, database safety rules, Patch
  application protocol, and failure-resolution capability boundaries.
- Update focused tests, current navigation documentation, and the stale
  delivery status of the database-query task record.
- Run one clean-context forward test for each Skill, with at most one new clean
  rerun for a failed scenario after correction.

## Non-goals

- No Tool, Profile, database schema, persistence, request-generation runtime,
  target API, or external service behavior changes.
- No change to architecture documents whose role-specific language records
  actual authorization.
- No compatibility copies of old Reference paths.
- No Git commit, merge, push, branch deletion, or Worktree cleanup without
  separate authorization.

## Decisions

- General concepts lead; internal terms remain only where exact execution needs
  them and are defined on first use.
- Skill content describes available capabilities and Tool protocols, not which
  runtime role may select the Skill.
- Forward tests receive the final Skill plus a raw task scenario, without the
  intended answer or this task's diagnosis.

## Verification

- Skill Creator `quick_validate.py` reported `Skill is valid!` for all three
  built-in Skill directories.
- Focused Skill/loader/file-read tests: 49 passed.
- `uv run pytest -q`: 657 passed, 13 skipped.
- `uv run ruff check restscope tests`: all checks passed.
- `uv run python -m compileall -q restscope tests`: passed.
- `uv run pytest -q tests/test_no_typing_any.py`: 1 passed.
- `git diff --check`: passed.
- Three independent clean-context forward tests selected the intended query,
  diagnosis, and input-rule workflows while preserving header redaction,
  evidence meaning, delegation boundaries, value-domain proof, and the rule
  that configuration success is not target API success.

The forward tests changed no files and contacted no live target. The complete
change remains unstaged and uncommitted in its dedicated Worktree.
