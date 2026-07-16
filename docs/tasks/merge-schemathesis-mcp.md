# Merge schemathesis-mcp into RESTScope

Status: In progress

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

Pending implementation and fresh final results.
