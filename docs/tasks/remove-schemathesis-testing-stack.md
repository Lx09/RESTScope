# Remove Schemathesis Testing Stack

Status: Implemented and locally verified; uncommitted

## Objective

Retire the legacy Schemathesis-backed operation-testing path now that
Operation Smoke and `restscope.testing.run_operation` are the only supported
Supervisor execution flow.

## Approved scope

- Delete the in-repository `schemathesis-mcp` service, bundled server configs,
  disabled workflow, contract test, legacy OperationTestAgent package, runner,
  dependency analyzer, and their dedicated tests.
- Make `OperationSmokeAgent` the only Supervisor execution dependency.
- Move `OperationReference` to the neutral `restscope.operations` module.
- Store `OperationSmokeResult` directly on each Supervisor attempt and remove
  legacy dependency, blocked, finding, execution, artifact, and run-summary
  fields.
- Preserve the generic MCP Host, stdio adapter, root MCP dependency, and
  caller-owned MCP server configuration support.
- Remove the Schemathesis-only preset layer and register explicitly supplied
  generic MCP sources instead.
- Update active README and AGENTS guidance. Preserve historical task and design
  documents unchanged.

## Non-goals

- Rewriting Git history.
- Removing the generic MCP infrastructure or root MCP dependency.
- Keeping compatibility shims for OperationTestAgent or its report contracts.
- Changing Operation Smoke, Generator, API Behavior Monitor, Response Value,
  Resource Identifier, or OpenAPI Retrieval behavior beyond shared imports.
- Running a real target, external model, GitHub CI/CD, push, or pull request.

## Implementation notes

- The verified Resource Identifier branch was committed as `e68f86c`,
  fast-forwarded into local `main`, reverified there, and its worktree/branch
  were removed before this worktree was created.
- This worktree started from `main` at `e68f86c`.
- Baseline verification: `uv run pytest -q` reported
  `407 passed, 14 skipped`.

## Verification

Completed:

```bash
uv run pytest -q \
  tests/test_agent_package_boundaries.py \
  tests/test_supervisor_operation_smoke.py \
  tests/test_app_tool_context.py \
  tests/test_openapi_retrieval_agent.py \
  tests/test_mcp_host.py \
  tests/test_mcp_adapter.py
uv run pytest -q
uv run python -m compileall -q restscope
uv lock --check
git diff --check
```

- Focused Agent/Supervisor/App/Retrieval/MCP suite:
  `63 passed`.
- Full root suite: `380 passed, 14 skipped`.
- `python -m compileall -q restscope`: passed.
- `uv lock --check`: passed (`Resolved 86 packages`).
- `git diff --check`: passed.
- Active production code, current tests, README, AGENTS, `.env.example`,
  workflows, root project metadata, and root lock contain no Schemathesis
  references.
- All 36 tracked files in the retired service, Agent, bundled configuration,
  workflow, contract test, and dedicated legacy tests are marked deleted.
- Historical task/design documents are intentionally unchanged.

No real target, external model, GitHub CI/CD, push, or pull request was run.
The feature branch remains uncommitted pending separate authorization.
