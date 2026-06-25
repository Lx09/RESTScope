# LLM Module MVP

## Status

Completed.

## Goal

Implement the `restscope.llm` and `restscope.capabilities` MVP based on
`docs/llm_design.md`, using the existing context package as the upstream input
and keeping tool execution read-only and policy-gated.

## Scope

- Add provider-neutral LLM schemas, request factory, client, provider registry,
  model selector, output validator, and redactor.
- Add `FakeProvider` and OpenAI-compatible provider.
- Add the safe capability/tool runtime shell plus MCP and Skill scaffolds.
- Update short LLM config examples with optional provider keys.

## Out Of Scope

- Anthropic provider.
- Real MCP server calls.
- Skill script execution.
- LangGraph runtime nodes.
- DB writes from the LLM layer.

## Verification

- `uv sync`
- `uv run pytest -q`
- `uv run python -c "from restscope.llm import LLMClient"`
- `uv run python -c "from restscope.capabilities import ToolRegistry"`
