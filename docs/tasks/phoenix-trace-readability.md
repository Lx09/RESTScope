# Phoenix Trace Readability

Status: Implemented and verified

## Objective

Make every RESTScope CHAIN, AGENT, LLM, and TOOL span readable in Phoenix
without changing Phoenix itself or changing RESTScope business results.

## Decisions

- Generic span inputs and outputs use indented UTF-8 JSON.
- Oversized content remains bounded independently per direction. Its trace value
  contains a structured `preview` plus `truncated=true`, never a second
  JSON-encoded preview string.
- AGENT spans record `agent.name`; TOOL spans record `tool.name`.
- Manual `LLMClient.invoke` spans use standard indexed OpenInference input and
  output message attributes. No OpenAI SDK instrumentation or `ChatCompletion`
  child span is reintroduced.
- LLM `input.value` contains message count and roles. LLM `output.value`
  contains parsed JSON, finish reason, and tool-call ID/name summaries. Raw
  model content is recorded once as the assistant message.
- Tool-call arguments remain visible in the semantic message attributes after
  exact API-key replacement. Provider-private tool-call context is not
  projected.
- `RESTScopeApp.run` and `RESTScopeMainGraph.run` trace bounded summaries rather
  than duplicate the complete run report. The returned `RESTScopeRunReport`
  remains unchanged.
- Existing Phoenix projects are historical evidence and are not rewritten.

## Privacy boundary

The App-owned `Redactor` remains the only redaction implementation. It replaces
only exact configured THINK, FAST, and Phoenix API key values. Target
Authorization/Cookie values, generated request values, failure evidence, and
ordinary sensitive-named fields remain visible.

Omitting provider-private tool-call context is a trace projection boundary, not
a new redaction heuristic. Model-visible content continues to be recorded.

## Verification

The implementation uses stub providers and local/in-memory exporters. It does
not call DeepSeek or a target API.

- `uv run pytest -q` passed with `367 passed, 16 skipped`.
- `uv run --extra tracing pytest -q` passed with `387 passed, 4 skipped`.
- `uv run --extra tracing pytest -q tests/test_phoenix_tracing_contract.py
  -m phoenix_contract` passed with `1 passed`.
- The retained Phoenix contract project is
  `restscope-contract-40a5a467fa36434db75d8e18069ad64b`
  (`UHJvamVjdDoyMA==`). Phoenix 19.0.0 displayed separate Input Messages and
  Output Messages panels for `LLMClient.invoke`, readable JSON summaries for
  generic input/output, and a structured object preview for truncated content.
- `uv run --extra tracing python -m compileall -q restscope` and
  `git diff --check` completed successfully.
