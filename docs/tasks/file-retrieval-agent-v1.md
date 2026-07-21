# Investigation-Style File Retrieval Agent v1

Status: Superseded by `docs/tasks/app-tool-context-openapi-ir-retrieval.md`

This record describes the earlier v1 experiment. Its file-path request,
`restscope.file.retrieve` tool, raw-text search, and package/type names are no
longer current interfaces.

## Objective

Add an ephemeral File Retrieval Subagent that is exposed as the read-only
`restscope.file.retrieve` tool and autonomously investigates one supplied
OpenAPI file to find operations that may produce a consumer parameter value.

## Approved scope

- Create an independent `restscope.agent.file_retrieval` package.
- Accept one explicitly authorized local OpenAPI file and one
  `parameter_value_producer` query.
- Let a Thinking-model Subagent choose searches, symbol lookups, result limits,
  evidence expansion, retries, conflict handling, and when to stop.
- Bind all internal tools to the already loaded document.
- Enforce 20 tool calls, 200 KiB of tool results, and 120 seconds per run.
- Reject external `$ref` values before parsing so no other file or network
  resource can be loaded.
- Validate final operation and evidence references, with one repair attempt.
- Register the Agent explicitly as a generic local read-only tool.

## Non-goals

- Test-report, source-code, directory, multi-file, URL, or arbitrary filesystem
  investigation.
- Integration into Supervisor or OperationTestAgent.
- Persistent indexes, plans, artifacts, memory, or investigation history.
- A generic base Agent or expansion of the global SkillRegistry.
- Running or triggering GitHub CI/CD, committing, or pushing.

## Decisions

- The supplied file path itself is the read authorization.
- `consumer_method` is required. `consumer_path` accepts either the OpenAPI
  template or an actual path that matches exactly one template.
- The external tool uses a generic objective-discriminated request; v1 exposes
  only `parameter_value_producer`.
- Internal investigation tools are package-private and are never registered in
  the global capability runtime.
- Search traces expose actions and evidence, never model chain-of-thought.

## Implemented

- Added the independent `restscope.agent.file_retrieval` package with public
  request/result contracts, configured factory, LangGraph runtime, internal
  skill, single-document workspace, and explicit tool registration.
- Added bounded raw-text, OpenAPI symbol, operation, and evidence tools without
  registering them in the global capability registry.
- Added request-scoped evidence, semantic reference validation, one normal
  output repair, budget-forced tool-free summary, and no persistence imports.
- Added provider-neutral assistant tool-call history. The OpenAI-compatible
  adapter encodes dotted internal tool names at its boundary and restores the
  original names on responses.
- Added Swagger 2 compatibility fixes for inline parameter `required`, shared
  form/body parameters, and reusable local parameter references. Request
  target discovery covers JSON, form, multipart, and composed schemas.
- Added `tests/test_file_retrieval_agent_live.py` as an opt-in, real-thinking-
  model test against `assets/openapi/petstore-v3.json`. It investigates the
  `orderId` consumed by `getOrderById` and requires `placeOrder` as a supported
  producer candidate.

## Verification

- `uv run pytest -q tests/test_file_retrieval_agent.py tests/test_agent_package_boundaries.py tests/test_llm_mvp.py`
  - Passed: 43 tests.
- `uv run pytest -q`
  - Passed: 123 tests.
- `uv run python -m compileall -q restscope`
  - Passed.
- `git diff --check` plus trailing-whitespace scan for new files
  - Passed.
- GitHub CI/CD was not run or triggered. No live LLM provider call was made.
- The live test is enabled only with `RUN_FILE_RETRIEVAL_LIVE=1`; it remains
  unexecuted until explicitly invoked with valid `THINK_*` configuration.
- No commit or push was created, as required.
