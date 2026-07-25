# DeepSeek Provider

Status: Completed and verified

Historical note: the OpenAPI Retrieval tests listed below were valid at the
time of verification. The Retrieval Agent and those tests were later deleted
by `task-focused-main-flow-prompts.md`; the DeepSeek provider remains active.

## Objective

Implement the approved first-class official DeepSeek provider entirely inside
the LLM boundary so all Agents can use it without provider-specific behavior.

## Approved scope

- Add `DeepSeekProvider` as an explicit `OpenAICompatibleProvider` subclass.
- Add provider-neutral reasoning configuration and opaque tool-call provider
  continuation context.
- Translate structured output, thinking, tool-choice, and assistant tool-call
  history to the official DeepSeek API contract.
- Register `provider=deepseek` from the existing `THINK_*` and `FAST_*`
  configuration surface.
- Add provider-level tests and preserve existing Agent behavior.

## Non-goals

- Agent-specific DeepSeek logic.
- Third-party DeepSeek gateways.
- A general provider capability framework.
- Persistent reasoning or intermediate Agent state.
- CI/CD changes, live API execution, or pushes.

## Decisions

- DeepSeek-specific continuation data travels opaquely on returned `ToolCall`
  objects because current tool-loop Agents already preserve those objects.
- `json_schema` is a RESTScope semantic contract. DeepSeek receives
  `json_object` plus a schema instruction and local validation stays decisive.
- Official DeepSeek configuration is explicit and is never inferred from URL.

## Verification

- `uv run pytest -q tests/test_llm_deepseek.py tests/test_llm_mvp.py tests/test_openapi_retrieval_agent.py`
  - Passed: 54 tests.
- `RUN_OPENAPI_RETRIEVAL_LIVE=0 uv run pytest -q -rs tests/test_openapi_retrieval_agent_live.py`
  - Skipped: 1 opt-in real-model test, as expected.
- `uv run pytest -q`
  - Passed: 138 tests; skipped: 1 opt-in real-model test.
- `uv run python -m compileall -q restscope`
  - Passed.
- `git diff --check` and a trailing-whitespace scan of untracked files
  - Passed.
- Independent code review found one forced-tool-choice edge case. A red-green
  regression now verifies rejection even when no tools are supplied, and the
  reviewer confirmed no remaining issues.
- GitHub CI/CD and the live DeepSeek API were not run. The implementation is
  preserved in a purpose-specific commit under the user's later authorization;
  it was not pushed.
