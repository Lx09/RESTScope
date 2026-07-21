# DeepSeek Provider Design

Status: Approved

## Objective

Add first-class support for the official DeepSeek API so every RESTScope Agent
can select `provider="deepseek"` without containing DeepSeek-specific request,
message-history, or structured-output logic.

## Boundary

`DeepSeekProvider` is an explicit subclass of `OpenAICompatibleProvider`.
It reuses the OpenAI SDK transport, authentication, timeout handling, tool-name
encoding, and common response normalization while owning every DeepSeek wire
protocol difference.

Agents continue to use only `LLMRequest`, `LLMResponse`, `LLMMessage`, and
`ToolCall`. They must not inspect the provider name, `reasoning_content`, the
DeepSeek thinking switch, or DeepSeek response-format restrictions.

## Provider-neutral continuation

DeepSeek requires the `reasoning_content` associated with an assistant tool
call to be sent back in subsequent requests. A returned `ToolCall` therefore
carries an opaque `provider_context` mapping. `DeepSeekProvider` places the
assistant's reasoning content in that mapping when normalizing a response and
recovers it when serializing the assistant tool-call history.

Agents already preserve returned `ToolCall` objects when constructing the next
assistant message, so no Agent-specific change is required. The opaque context
is ephemeral: it is not logged, returned as an Agent result, or persisted.

## Reasoning configuration

The provider-neutral request and model configuration include a reasoning
configuration with:

- mode: `default`, `enabled`, or `disabled`;
- effort: `high`, `max`, or absent.

The `THINK_*` model slot defaults to enabled reasoning and the `FAST_*` slot
defaults to disabled reasoning. Providers that do not use these settings may
ignore them.

DeepSeek requests explicitly send the selected thinking mode and optional
reasoning effort. In thinking mode, temperature is omitted because DeepSeek
does not use it.

## Structured output

RESTScope keeps `response_format="json_schema"` as a provider-neutral semantic
request. DeepSeekProvider translates it to DeepSeek's `json_object` wire
format and injects the requested JSON Schema into a system instruction. The
existing local JSON/Pydantic and semantic validation remains authoritative;
RESTScope does not describe this fallback as server-side schema enforcement.

## Tool choice

In DeepSeek thinking mode:

- `auto` sends tools but omits the `tool_choice` field;
- `none` omits tools;
- `required` or a named forced tool fails locally with the stable code
  `deepseek_tool_choice_unsupported`.

If a DeepSeek thinking response contains tool calls without reasoning content,
normalization fails with `deepseek_reasoning_content_missing`. If existing
assistant tool-call history lacks its continuation context, request conversion
fails with the same code before a network request.

## Configuration

The official provider is selected explicitly:

```env
THINK_PROVIDER=deepseek
THINK_MODEL=deepseek-v4-pro
THINK_API_KEY=...
THINK_REASONING_MODE=enabled
THINK_REASONING_EFFORT=high

FAST_PROVIDER=deepseek
FAST_MODEL=deepseek-v4-flash
FAST_REASONING_MODE=disabled
```

The default base URL is `https://api.deepseek.com`. A configured alternative
is passed through for local testing, but third-party DeepSeek gateways are not
supported or claimed compatible.

## Non-goals

- No provider capability/profile framework.
- No DeepSeek detection based on URL or model name.
- No changes to Agent prompts, state machines, or tool loops.
- No reasoning logging or persistence.
- No Anthropic adapter work.
- No CI/CD changes or live API call as part of normal verification.

## Verification

Unit tests use an in-memory SDK-shaped client to verify the exact DeepSeek
request and response mapping. Existing OpenAI-compatible tests remain green.
The opt-in File Retrieval live test remains the only real strategy test and is
not run without explicit authorization and credentials.
