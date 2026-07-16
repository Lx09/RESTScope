# Merge schemathesis-mcp into RESTScope

Status: Completed

## Objective

Import `schemathesis-mcp` as `services/schemathesis-mcp/` with its full reachable Git history while preserving its independent package, dependency, process, Docker, and test boundaries.

## Approved scope

- Rewrite a temporary clone of the source repository so every tracked path is below `services/schemathesis-mcp/`.
- Merge the rewritten history without squashing.
- Keep RESTScope-to-service communication MCP-only.
- Replace absolute sibling-repository paths with repository-relative development commands.
- Promote the service CI workflow to the RESTScope workflow root.
- Add a real stdio contract test between RESTScope's MCP Host and the in-repository service.
- Delete `/Users/lixin/Workplace/schemathesis-mcp` after the approved non-container verification gate passes.

## Non-goals

- Create a uv workspace or share dependency locks.
- Change MCP tool names, schemas, package name, CLI entrypoint, or Docker image name.
- Publish, push, or add a service release workflow.
- Require Docker e2e when the local Docker daemon is unavailable.

## Source evidence

- Source repository: `/Users/lixin/Workplace/schemathesis-mcp`
- Source branch: `main`
- Source HEAD: `a30587bca3605a7f6f062c0e597c39e3438799d8`
- Reachable commits: 10
- Configured remotes: none
- Baseline: 49 non-container tests passed, 1 Docker test deselected; Ruff and package build passed.
- Docker limitation: Docker CLI is installed, but the daemon is not reachable at `/Users/lixin/.docker/run/docker.sock`.

## Verification

- Rewritten service history tip: `fa2cf4a8454d6d4ca9c5955cff141d1dd2dff714`.
- History merge commit: `0d8ed61`.
- Imported commit count: 10.
- Source and imported metadata digest:
  `241616c2da7546f798cf971017664027e8ef866b55c18d843016f92ec4b0c684`.
- Source and normalized imported tree digest:
  `4ccdea6333bee92b546336fd61ba89bc95e7840554d1478db7468fec03dda282`.
- `uv run pytest -q`: 79 passed.
- `uv run python -m compileall -q restscope`: passed.
- `uv run pytest -q -k 'not test_docker_stdio_mcp_host_runs_api_test'`
  from `services/schemathesis-mcp/`: 49 passed, 1 deselected.
- `uv run ruff check .` from the service project: passed.
- `uv build` from the service project: sdist and wheel built successfully.
- `uv run pytest -q tests/test_schemathesis_mcp_contract.py`: passed against
  the real in-repository stdio server.
- `git diff --check`: passed.
- Docker e2e remains unverified because `docker info` cannot connect to the
  local daemon; this is an approved non-blocking limitation.

- Integration commit `d2a9945` was fast-forwarded into local `main`.
- The original `/Users/lixin/Workplace/schemathesis-mcp` repository was deleted
  only after its clean HEAD and both history digests were rechecked.
- After deletion, the imported history remains reachable from `main` and the
  real in-repository stdio contract test passed again.
- No push was performed.
