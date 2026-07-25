# LLM Safe Slimming

Status: Completed

Historical note: references below to the OpenAPI Retrieval Agent describe the
tree at the time of this task. That Agent and its tests were later deleted by
`task-focused-main-flow-prompts.md`.

## Objective

Remove LLM-layer code that has no production consumer or cannot be reached by
the current provider implementations, while preserving the active OpenAI-
compatible, DeepSeek, model-selection, and Agent contracts.

## Approved scope

- Remove `LLMRequestFactory` and the production `FakeProvider`.
- Remove the unreachable client retry branch and its specialized exceptions.
- Remove unused aliases, validation exceptions, async placeholders, validator
  arguments, and raw provider tool-call storage identified in the approved
  implementation plan.
- Update focused tests and current-facing documentation.

## Non-goals

- Do not change Agent APIs or runtime behavior.
- Do not move tool contracts or the redactor out of `restscope.llm`.
- Do not remove thinking/fast configuration, model roles, request metadata, or
  response metrics.
- Do not rewrite the historical LLM design document.
- Do not call real models, access external services, or run GitHub CI/CD.

The earlier “do not move the redactor out of `restscope.llm`” non-goal was
superseded by the user-approved unified-redaction decision on 2026-07-23.
`Redactor` now lives only at `restscope.redaction`; the former LLM export and
module have no compatibility alias.

## Safety constraints

The pre-existing changes under `restscope/agent/openapi_retrieval/` and the
untracked `.idea/` directory are user work and must remain untouched. This task
will remain uncommitted unless the user separately authorizes a commit.

## Verification

Observed on 2026-07-22:

- `uv run pytest -q tests/test_llm_mvp.py tests/test_llm_deepseek.py
  tests/test_restart_cleanup.py`: 30 passed.
- `uv run pytest -q tests/test_openapi_retrieval_agent.py
  tests/test_app_tool_context.py tests/test_agent_package_boundaries.py`: 35
  passed.
- `uv run pytest -q`: 143 passed and 1 skipped. The environment-configuration
  test now loads an isolated temporary env file, so the result is independent
  of the repository's ignored `.env`.
- `uv run python -m compileall -q restscope`: passed.
- `git diff --check`: passed.
- A production-tree `rg` search found no remaining references to the removed
  symbols or helper names.

No real model, external network, GitHub CI/CD, commit, or push was used.

## Follow-up

The `disable_retry` metadata key in the currently modified OpenAPI Retrieval
Agent is intentionally left in place to avoid overlapping user work. It can be
removed separately after those changes are preserved.
