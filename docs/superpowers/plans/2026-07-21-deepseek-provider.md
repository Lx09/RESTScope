# DeepSeek Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit official DeepSeek provider that every Agent can select without provider-specific code.

**Architecture:** `DeepSeekProvider` subclasses the existing OpenAI-compatible adapter and owns DeepSeek request/response differences. Opaque continuation data on `ToolCall` preserves `reasoning_content` through existing Agent tool loops, while the provider translates RESTScope's logical JSON Schema request to DeepSeek JSON mode plus a schema instruction.

**Tech Stack:** Python 3.11+, Pydantic v2, OpenAI Python SDK, pytest.

---

### Task 1: Provider-neutral reasoning and continuation contracts

**Files:**
- Modify: `restscope/llm/schemas.py`
- Modify: `restscope/llm/__init__.py`
- Test: `tests/test_llm_mvp.py`

- [ ] Add a failing serialization test showing `LLMReasoningConfig` on a request and opaque `provider_context` on a `ToolCall` survive Pydantic dump/validation.
- [ ] Run `uv run pytest -q tests/test_llm_mvp.py -k 'reasoning_config or provider_context'` and confirm failure because the fields do not exist.
- [ ] Add `LLMReasoningConfig`, `LLMRequest.reasoning`, `LLMModelConfig.reasoning`, and `ToolCall.provider_context`; export the reasoning type.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: DeepSeek wire adapter

**Files:**
- Create: `restscope/llm/providers/deepseek.py`
- Modify: `restscope/llm/providers/__init__.py`
- Modify: `restscope/llm/exceptions.py`
- Modify: `restscope/llm/__init__.py`
- Test: `tests/test_llm_deepseek.py`

- [ ] Add failing tests for official default URL, explicit thinking parameters, omitted temperature, `json_schema` to `json_object` conversion, and schema instruction injection.
- [ ] Run `uv run pytest -q tests/test_llm_deepseek.py` and confirm collection fails because `DeepSeekProvider` does not exist.
- [ ] Implement the smallest subclass and provider compatibility exception needed to satisfy request-conversion tests.
- [ ] Add failing tests proving thinking `auto` omits `tool_choice`, `none` omits tools, and forced tool choice fails locally with `deepseek_tool_choice_unsupported`.
- [ ] Implement those tool-choice rules and re-run the focused file.
- [ ] Add failing tests proving response reasoning is stored in every returned tool call, replayed as assistant `reasoning_content`, and missing response/history reasoning fails with `deepseek_reasoning_content_missing`.
- [ ] Implement response normalization and history serialization, then re-run the focused file.

### Task 3: Configuration and registry

**Files:**
- Modify: `restscope/restscope_config.py`
- Modify: `restscope/llm/model_selector.py`
- Modify: `restscope/llm/config.py`
- Modify: `README.md`
- Test: `tests/test_llm_mvp.py`

- [ ] Add failing tests for parsing `THINK_REASONING_MODE`, `THINK_REASONING_EFFORT`, the THINK enabled default, the FAST disabled default, and registration under `deepseek`.
- [ ] Run the selected tests and confirm the missing configuration or provider registration failures.
- [ ] Implement configuration parsing, model selection propagation, and explicit DeepSeek provider registration using the official default URL.
- [ ] Document the official configuration and re-run the selected tests.

### Task 4: Regression and live-test readiness

**Files:**
- Modify: `tests/test_file_retrieval_agent_live.py`
- Modify: `docs/tasks/deepseek-provider.md`

- [ ] Add a configuration assertion that a live DeepSeek run requires a DeepSeek API key while leaving the Agent strategy and runtime untouched.
- [ ] Run `RUN_FILE_RETRIEVAL_LIVE=0 uv run pytest -q -rs tests/test_file_retrieval_agent_live.py` and confirm the live test remains opt-in.
- [ ] Run `uv run pytest -q tests/test_llm_deepseek.py tests/test_llm_mvp.py tests/test_file_retrieval_agent.py`.
- [ ] Run `uv run pytest -q`.
- [ ] Run `uv run python -m compileall -q restscope` and `git diff --check`.
- [ ] Record exact results and explicitly note that CI/CD and the live DeepSeek API were not run.

No commit step is included because repository rules require separate explicit
authorization and the user has not authorized a commit.
