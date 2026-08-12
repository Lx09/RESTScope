# Python 3.12 Baseline

Status: Implemented and verified; uncommitted

## Objective

Raise RESTScope's minimum supported Python version from 3.11 to 3.12 and keep
the local interpreter selection, package metadata, and dependency lock aligned.

## Approved scope

- Set `.python-version` to Python 3.12.
- Set the package and lock-file requirement to `>=3.12`.
- State the requirement in the development instructions.
- Verify the complete project with a real Python 3.12 interpreter.
- Add a small regression test for the three active version declarations.

## Non-goals

- Do not require one exact Python 3.12 patch release.
- Do not add an upper Python-version bound or a CI system that the repository
  does not currently have.
- Do not rewrite historical task documents that truthfully record Python 3.11
  as their implementation-era technology stack.
- Do not upgrade application dependencies merely because the lock file is
  regenerated.
- Do not commit, merge, push, or remove the feature worktree without separate
  Git authorization.

## Decisions

- `>=3.12` means Python 3.12 is the minimum supported language runtime while
  later compatible Python releases remain allowed.
- `.python-version` uses the minor selector `3.12`, allowing `uv` to choose an
  available patched 3.12 interpreter instead of pinning a vulnerable patch.

## Verification

- `uv sync --python 3.12 --locked --all-extras` completed with Python 3.12.12.
- `uv run --locked python --version` reported `Python 3.12.12`.
- `uv run --locked pytest -q` passed: 567 tests passed and 2 were skipped.
- `uv run --locked pytest -q tests/test_no_typing_any.py tests/test_python_support.py`
  passed all 3 focused checks.
- `uv run --locked python -m compileall -q restscope tests` passed.
- `uv lock --check` passed, and package-name/version lines were unchanged from
  the prior lock; the generated diff removes Python 3.11-only resolution data.
- A freshly built wheel reports `Requires-Python: >=3.12` in its metadata.
- `git diff --check` passed.
- The main and feature-worktree `.venv` environments both report Python
  3.12.12. The main worktree remained clean because `.venv` is generated and
  ignored.

## Remaining delivery

The implementation remains unstaged and uncommitted in the dedicated feature
worktree. Commit, merge into local `main`, merged-result verification, worktree
cleanup, and branch deletion require explicit Git authorization. Push remains a
separate action.
