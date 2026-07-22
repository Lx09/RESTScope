# Arize Phoenix Trace Monitoring

Status: In progress

## Objective

Add optional, local Arize Phoenix OSS monitoring for RESTScope App, Agent, LLM,
and tool traces without changing business behavior when tracing is disabled or
unavailable.

## Approved scope

- Provide an App-owned `TracingRuntime` with explicit dependency injection.
- Export manual OpenInference spans over OTLP/HTTP to a loopback-only Phoenix
  Docker service.
- Auto-instrument only the OpenAI-compatible SDK, with its raw request and
  response content hidden.
- Store recursively redacted and size-bounded content on RESTScope's manual
  spans; never store DeepSeek `reasoning_content` bodies.
- Keep the dependency set behind a `tracing` optional extra and default tracing
  to disabled and fail-open.
- Verify ingestion through Phoenix's local REST API without a real LLM call.

## Non-goals

- Do not use Phoenix Cloud or Arize AX.
- Do not persist tracing objects in ToolContext, LangGraph state, reports, or
  the RESTScope database.
- Do not instrument each LangGraph node.
- Do not change the Schemathesis MCP service contract.
- Do not copy or commit the ignored `.env`, call DeepSeek, push, or commit this
  feature work without separate authorization.

## Decisions

- Manual span kinds are `CHAIN`, `AGENT`, `LLM`, and `TOOL`; OpenAI SDK spans
  are children of the manual `LLMClient.invoke` span.
- Trace inputs and outputs are limited independently to 65,536 UTF-8 bytes and
  retain original-size and truncation attributes.
- Phoenix uses image `arizephoenix/phoenix:19.0.0`, port 6006 on loopback, and
  a named volume for SQLite data.
- Batch export is flushed during App close with a five-second upper bound.
- One process-wide OpenAI instrumentor is reference-counted across compatible
  runtime instances; conflicting active configurations fall back to no-op.
- A loopback Phoenix endpoint temporarily adds `localhost`, `127.0.0.1`, and
  `::1` to both process `NO_PROXY` spellings while the shared backend is active.
  The final runtime restores the prior values after shutdown. This avoids local
  OTLP traffic being sent through a developer machine's HTTP proxy.

## Main-branch preparation

The approved cleanup was committed on local `main` before this worktree was
created:

- `93b40b4 test: isolate config tests from local env`
- `4237e00 refactor: slim unused LLM abstractions`
- `81ec4dd docs: explain OpenAPI retrieval internals`
- `9143940 chore: ignore local IDE metadata`

The ignored `.env` was neither staged nor copied into this worktree.

## Merge and live verification

The user approved purpose-separated commits, a local fast-forward merge into
`main`, and one real OpenAPI Retrieval Agent run with the complete FAST model
slot (`deepseek-v4-flash`, reasoning disabled). The opt-in live test selects
the model slot without changing the production role-to-model routing and owns
the tracing runtime long enough to flush all spans before exit.

## Verification

All LLM behavior in these checks was stubbed or used an `httpx.MockTransport`;
no DeepSeek request was made.

- `uv sync` completed and removed the 17 tracing-only packages.
- `uv run pytest -q` passed with 153 tests and 10 skips using only core
  dependencies.
- `uv sync --extra tracing` installed the 17 optional tracing packages.
- `uv run --extra tracing pytest -q` passed with 164 tests and 2 skips.
- `uv run --extra tracing pytest -q tests/test_phoenix_tracing_contract.py -m phoenix_contract`
  passed 1 test against Phoenix `19.0.0` over `127.0.0.1:6006`.
- The OpenAI SDK instrumentation test used its real Python client with a mock
  HTTP transport and verified a masked child span under `LLMClient.invoke`.
- `uv run --extra tracing python -m compileall -q restscope` passed.
- `docker compose -f compose.phoenix.yaml config --quiet` passed.
- `git diff --check` passed.

The Phoenix service and named SQLite volume remain running for UI inspection.
Feature implementation is verified on `codex/arize-phoenix-tracing`; local
merge and the real DeepSeek/Phoenix verification are pending.
