# App Runtime and Composition Navigation

Status: Verified, uncommitted

## Objective

Keep `RESTScopeApp` as RESTScope's small public lifecycle Interface while
separating target/runtime behavior from the default production object graph.

## Approved decisions

- Work directly on local `main`; do not create a feature branch or worktree.
- Replace `restscope/app.py` with a private three-file `restscope.app` package.
- Keep every existing public constructor, lifecycle method, audit method, and
  supported import path.
- Move default database, Monitor, Target API, Request Generation, Harness,
  tracing, UI, and Agent Profile composition behind private `_AppResources`.
- Remove direct App attributes that expose Harness and domain internals.
- Do not add a public Builder, Factory class, Protocol, dependency container,
  compatibility module, or new product behavior.

## Preserved user work

`restscope/data_types/__init__.py` was modified before this task. It is outside
the approved scope and must remain untouched and unstaged.

## Verification

- `uv run pytest -q tests/test_app_database_bootstrap.py tests/test_app_tool_context.py tests/test_live_ui_app.py tests/test_workflow_package_boundaries.py`
  — `51 passed`.
- `uv run pytest -q` — `572 passed, 2 skipped`.
- `uv run pytest -q tests/test_no_typing_any.py` — passed as part of the full
  suite and separately during implementation.
- `uv run python -m compileall -q restscope tests` — passed.
- A clean wheel build contains only `restscope/app/__init__.py`,
  `composition.py`, and `runtime.py`; an isolated Python 3.12 environment
  imported the root and package facades as the same `RESTScopeApp` class.
- Retired source/cache path scans and `git diff --check` passed.
- The pre-existing `restscope/data_types/__init__.py` change remains outside
  this task's edits. No files were staged or committed.
