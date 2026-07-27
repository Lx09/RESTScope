# String Regex Generator

Status: Completed

## Objective

Add a deterministic, bounded `regex` string Generator and connect it to
OpenAPI pattern defaults, stored Generator configurations, constraint solving,
text request bodies, and the Parameter Patch Agent.

## Approved scope

- Add the `regex` strategy with a Python pattern and whole-value length bounds.
- Use `rstr` behind a local output and work boundary.
- Derive the strategy for pattern-only string schemas after concrete OpenAPI
  values have been considered.
- Expose the strategy through existing configuration DTOs, testing tools, and
  Parameter Patch structured output.
- Preserve JSON persistence without adding a database migration.

## Non-goals

- Supporting a separate ECMAScript regex engine or changing existing
  `re.search` validation semantics.
- Automatically enabling schemas that combine `pattern` with `format`.
- Adding a new Agent, tool, persistence boundary, or live external call.
- Committing, merging, pushing, or cleaning up the feature branch without
  separate user authorization.

## Decisions

- `pattern` is limited to 2000 characters. Generated values use a 0–10000
  length interval, defaulting to 0–100.
- The same strategy and seed produce the same value. Generation tries at most
  20 candidates and fails closed when the expression is unsupported or cannot
  meet its bounds.
- Open-ended regex quantifiers expand at most 100 times and never beyond the
  configured output length. Nested expressions also receive a finite work
  budget.
- Explicit feedback patches remain authoritative over the frozen OpenAPI
  value constraints, matching the existing Generator Catalog decision.

## Verification

TDD red evidence:

- `.venv/bin/pytest -q tests/test_testing_generation.py -k
  'regex_generator'`: 12 failed because `regex` was not a recognized strategy.
- The second focused batch failed on the four missing integration points:
  default derivation, text value construction, constraint candidates, and
  Parameter Patch sampling.
- The empty-repeat safety regression initially returned a value instead of
  consuming the finite traversal budget.
- Final self-review tests exposed an open-ended repeat exceeding its 100-item
  segment limit and hash-dependent ordering in negated character classes.

Green evidence:

- `.venv/bin/pytest -q tests/test_testing_generation.py`: 36 passed before the
  integration batch.
- The focused regex integration selection passed 21 tests.
- The six planned focused test files passed 115 tests after updating two old
  scenarios whose pattern-only premise was deliberately superseded.
- `uv run pytest -q`: 477 passed and 16 skipped.
- `uv run python -m compileall -q restscope tests`: completed successfully.
- `git diff --check`: completed successfully.

No real API, LLM, or Phoenix call was made. The verified changes remain
uncommitted in the dedicated feature worktree pending explicit authorization.
