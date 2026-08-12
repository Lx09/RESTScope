# App Lifecycle and CLI Entrypoint

Status: Verified; included in the authorized scoped commit

## Objective

Keep `RESTScopeApp` focused on one production lifecycle and add one installed
command that owns standalone process startup.

## Approved decisions

- Work directly on local `main` and commit the verified scoped change.
- Keep only `RESTScopeApp(config)`, `from_environment()`, initialize, start,
  close, and `ui_url`.
- Move target validation and App Agent Profiles to private focused modules.
- Add `restscope/main.py` and the installed `restscope` Click command.
- Keep database OpenAPI audit facts while removing App audit query methods.
- Do not add compatibility aliases, public injection seams, `__main__.py`, or a
  generic dependency container.

## Preserved user work

The pre-existing `restscope/data_types/__init__.py` modification remains
untouched and must not be staged with this task.

## Verification

- Focused App, CLI, Catalog, UI, tracing, and package-boundary suite:
  `68 passed`.
- Complete suite: `576 passed, 2 skipped`.
- Precise `typing.Any` guard: `1 passed`.
- Python compilation, lock consistency, and `git diff --check`: passed.
- A clean wheel contains `restscope/main.py` and the five-file App package,
  registers `restscope = restscope.main:main`, and runs `restscope --help` from
  an isolated Python 3.12 environment.
- Generated build directories were moved outside the worktree. The pre-existing
  `restscope/data_types/__init__.py` edit remains unstaged.
