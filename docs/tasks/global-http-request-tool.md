# Global HTTP Request Tool

Status: Implemented and verified (uncommitted)

## Objective

Provide every RESTScope `CapabilityRuntime` with one target-bound HTTP request
tool that future Agents can opt into without depending on Schemathesis MCP.

## Approved scope

- Register `restscope.http.request` even when MCP presets are disabled.
- Allow every Agent role to select and execute the tool without a separate
  live-testing gate.
- Resolve relative paths against the App-bound `ToolContext.base_url` and
  inherit its headers without exposing them as model arguments.
- Support query parameters and mutually exclusive JSON, text, or URL-encoded
  form bodies.
- Return complete JSON or text responses up to 10 MiB.
- Reject absolute or escaping paths, credential and transport header overrides,
  redirects, binary responses, and oversized responses.
- Use mocked transports for verification; do not contact a real target.

## Decisions and risks

- The tool is intentionally marked high-risk and non-read-only, but ToolPolicy
  explicitly allows it for every role without a live-testing gate. Any Agent
  that later receives the shared capability can therefore issue POST, PUT,
  PATCH, or DELETE requests to the bound target without further approval.
- Registration does not add a tool loop to existing Agents. This change makes
  the capability globally available; each Agent remains responsible for
  explicitly selecting and executing it.
- One synchronous `httpx.Client` is created per call. Redirects are not
  followed and cookies are not retained between calls.
- The target base path is preserved, arbitrary relative paths are allowed, and
  paths do not have to match OpenAPI IR operations.
- Successful non-2xx responses remain HTTP evidence rather than tool failures.
  Network errors, timeouts, invalid inputs, unsupported media, and size limits
  are tool failures with stable codes.
- ToolResult contains the complete response. Tracing retains its
  independent 64 KiB content limit, so trace output may be truncated without
  changing the ToolResult.

## Follow-up redaction decision

The 2026-07-23 unified-redaction decision supersedes this task's original
recursive key-name and token-pattern masking. The raw HTTP result now preserves
query values, JSON/text values, and every response header, including
Authorization, Set-Cookie, and WWW-Authenticate. ToolExecutor applies the
App-owned `restscope.redaction.Redactor` once at the result boundary and only
replaces exact configured THINK, FAST, and Phoenix API key values. Target
Authorization/Cookie values are intentionally visible.

## Non-goals

- No Schemathesis MCP protocol changes.
- No multipart, file upload, binary response, cross-origin URL, or redirect
  following support.
- No persistence, response artifact storage, database change, real target
  request, or Agent tool-loop refactor.
- No commit, merge, push, or worktree cleanup without separate authorization.

## Verification

- TDD first observed the missing global tool, then the missing HTTP handler,
  form encoding, timeout code, sensitive-header, response-key, URL-query,
  binary detection, and JSON-serialization failures before their fixes.
- `uv lock` resolved 88 packages with `httpx` declared directly.
- The focused HTTP, tracing, and capability regression suite passed 60 tests.
- `uv sync && uv run pytest -q` passed with 184 tests and 10 skips using only
  core dependencies.
- `uv sync --extra tracing && uv run --extra tracing pytest -q` passed with
  196 tests and 2 skips.
- `uv run --extra tracing python -m compileall -q restscope` and
  `git diff --check` passed.
- No real target, DeepSeek, or Phoenix contract request was executed. Changes
  remain unstaged and uncommitted in the isolated worktree.

## Later reuse

The later lightweight OpenAPI testing work extracted the target-bound URL,
header, client lifecycle, redirect, timeout, and transport-error behavior into
a shared internal transport. The raw tool's public ToolSpec and bounded
response-body behavior remain unchanged.
