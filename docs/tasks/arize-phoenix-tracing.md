# Arize Phoenix Trace Monitoring

Status: Implemented and verified (uncommitted)

## Objective

Add optional, local Arize Phoenix OSS monitoring for RESTScope App, Agent, LLM,
and tool traces without changing business behavior when tracing is disabled or
unavailable.

## Original approved scope

This section records the initial tracing design. Its OpenAI SDK instrumentation
decision was superseded by the follow-up decision below.

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

## Follow-up decision: manual LLM spans only

On 2026-07-22, the user approved removing OpenAI SDK auto-instrumentation. New
traces contain only RESTScope's manual `LLMClient.invoke` span for each model
call; they do not contain `ChatCompletion` children. The manual span remains
responsible for normalized request and response content, provider and model
attributes, token counts, latency, recursive redaction, and reasoning-content
suppression.

The Phoenix tracer provider remains process-wide and reference-counted across
compatible runtime instances. Conflicting active configurations still fail
open. Existing Phoenix projects and their historical SDK spans are retained.

## Follow-up decision: unified exact-value redaction

On 2026-07-23, the user superseded recursive key-name/token-pattern masking and
reasoning suppression. `restscope.redaction.Redactor` is now the only
redaction implementation. One App-owned instance is shared by tracing and
capability output boundaries and replaces only exact configured THINK, FAST,
and Phoenix API key values.

New traces intentionally retain ordinary and sensitive-named parameters,
ToolContext Authorization/Cookie values, generator configurations, HTTP
authentication response headers, and complete DeepSeek `reasoning_content`.
The independent 65,536-byte input/output limit remains. Phoenix is still
loopback-only and unauthenticated, so local UI access exposes those values.
Historical traces are not rewritten or deleted.

The unified-redaction implementation was verified offline with the full core
and tracing-extra suites (`247 passed, 2 skipped` for each), plus compileall.
No new Phoenix contract trace or live model request was produced.

## Follow-up decision: task-focused LLM projection

`task-focused-main-flow-prompts.md` later narrowed each manual
`LLMClient.invoke` input to the complete messages only. Provider, model,
temperature, max tokens, response mode, reasoning settings, tool names, and
tool choice are span attributes. Output contains only content, parsed JSON,
tool calls, and finish reason; token counts and latency remain attributes.
This keeps request configuration and full tool schemas out of the prompt-like
trace payload while preserving model-visible content.

## Follow-up decision: readable semantic projection

`phoenix-trace-readability.md` supersedes the generic JSON-blob presentation
for new traces. LLM spans now use indexed OpenInference message attributes,
while their generic input/output values contain only readable summaries.
CHAIN, AGENT, and TOOL values use indented JSON; AGENT and TOOL spans also
record their semantic names. App and Supervisor roots no longer duplicate the
complete run report.

Oversized values retain the existing byte boundary but now store a structured
preview rather than an escaped JSON prefix. Provider-private tool-call context
is not projected; this is a collection boundary and does not change the shared
exact-value redaction policy. Historical Phoenix traces remain unchanged.

## Decisions

- Manual span kinds are `CHAIN`, `AGENT`, `LLM`, and `TOOL`; model calls emit
  only the manual `LLMClient.invoke` span.
- Trace inputs and outputs are limited independently to 65,536 UTF-8 bytes and
  retain original-size and truncation attributes.
- Trace content encoding delegates exact-value replacement to the shared
  Redactor; it owns only JSON serialization and the size limit.
- Phoenix uses image `arizephoenix/phoenix:19.0.0`, port 6006 on loopback, and
  a named volume for SQLite data.
- Batch export is flushed during App close with a five-second upper bound.
- One process-wide Phoenix backend is reference-counted across compatible
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

## Initial implementation verification

The repeatable test suites below used stubbed LLM behavior or an
`httpx.MockTransport`. The separately authorized live verification is recorded
in the next section.

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

The three purpose-separated feature commits were fast-forwarded to local
`main` without fetching or pushing:

- `5057788 feat: add optional Arize Phoenix tracing`
- `d3c450e test: cover Phoenix tracing workflows`
- `d244e7e docs: document local Phoenix tracing`

The merged tree repeated the tracing-extra suite (`164 passed, 2 skipped`),
the Phoenix contract (`1 passed`), `compileall`, and `git diff --check`.

## Manual-only LLM span verification

- The new SDK behavior test first failed against the old implementation because
  the exporter contained `ChatCompletion` and `LLMClient.invoke`; after the
  change, the same test passed with only `LLMClient.invoke`.
- `uv lock` resolved 86 packages and removed
  `openinference-instrumentation-openai` plus its now-unused
  `opentelemetry-instrumentation` dependency.
- `uv run --extra tracing pytest -q tests/test_observability.py tests/test_observability_integration.py`
  passed 18 tests.
- `uv sync && uv run pytest -q` passed with 153 tests and 10 skips using only
  core dependencies.
- `uv sync --extra tracing && uv run --extra tracing pytest -q` installed 15
  tracing packages and passed with 164 tests and 2 skips.
- `uv run --extra tracing python -m compileall -q restscope` passed, and an
  import-spec check confirmed `openinference.instrumentation.openai` is absent.
- `git diff --check` passed. No Phoenix contract or live DeepSeek request was
  run, and existing Phoenix trace data was unchanged.

## Historical live DeepSeek/Phoenix evidence before removal

The OpenAPI Retrieval Agent used by this historical run was later deleted by
`task-focused-main-flow-prompts.md`. Existing Phoenix traces and the evidence
below were intentionally retained; they no longer describe an active runtime
capability.

- Model slot: FAST (`deepseek/deepseek-v4-flash`, reasoning disabled).
- Phoenix project: `restscope-openapi-retrieval-live-20260722-131740`.
- The one allowed live test made three successful HTTP 200 model calls and
  consumed 8,037 prompt tokens plus 518 completion tokens (8,555 total). The
  three manual LLM spans recorded 5,755 ms combined provider latency.
- Phoenix stored nine spans: one `OpenAPIRetrievalAgent.retrieve` AGENT, three
  manual `LLMClient.invoke` spans, three masked `ChatCompletion` SDK children,
  and two TOOL spans (`openapi.inspect` and `openapi.search_symbols`).
- Manual LLM and TOOL inputs/outputs were present. SDK raw content was hidden;
  both configured API keys and `reasoning_content` were absent from the REST
  payload.
- The live pytest result was `1 failed in 6.24s`. The model proposed
  `POST /store/order` (`placeOrder`) but cited response-field evidence owned by
  `GET /store/order/{orderId}`. The trusted validator rejected the cross-
  operation and operation-unbound evidence, so no valid retrieval result or
  `investigation_summary.tool_calls` value was produced.
- Per the approved retry policy, this semantic failure was not retried and did
  not trigger a production-code change.

The Phoenix service and named SQLite volume remain running for UI inspection.
The feature branch and worktree remain available because cleanup was not
authorized. The ignored `.env` was not modified, staged, or copied.
