---
status: accepted
---

# Use Profile-authorized System Agents for Monitor model decisions

[ADR 0007](0007-orchestrator-ledger-long-tasks.md) later removes
`start_main_agent` and uses this registered System Agent lifecycle for both the
outer Orchestrator and every Main Worker. The isolation, authorization,
validation, correction, cancellation, and cleanup rules below remain active.

## Decision

RESTScope keeps `start_main_agent` for the one App-lifetime Main Agent and adds
`run_system_agent(profile_name, task)` for deterministic code that needs a
bounded synchronous model decision. A System Agent is a fresh root lifecycle of
the same generic Agent. It is neither a Subagent nor a specialized domain Agent.

Only Profiles registered by name in `AgentRuntimeDefinition.system_agents` may
use this entry point. A `SystemAgentDefinition` binds the expected Pydantic
result, task-local JSON Schema, local validator, and result name. It does not
change `AgentProfile` or authorize anything. The Profile continues to be the
only source of model, Tool, Skill, Context Source, instruction, and direct-child
Profile grants, so a future System Profile may use any capability explicitly
listed there.

Every call creates an isolated prompt session, cancellation root, private Plan
when granted, and Agent tree. It closes the tree after returning; App shutdown
cancels all still-active System roots and their descendants. System accounting
records cached and uncached input, output, Tool calls, and child starts, but has
no weighted-token ceiling or budget reminders.

The Harness validates every proposed final value against both the generated
Schema and the registered local validator. Invalid JSON, missing or extra
fields, aliases outside the current candidates, and duplicate response sources
produce bounded, specific correction feedback in the same session. There is no
attempt or time-count limit. Cancellation, shutdown, Provider failure, or a
failed safe compaction may still terminate the call.

The API Behavior Monitor registers two no-Tool Profiles using the directly
named `fast` model configuration: `resource-identifier-selector` and
`response-source-selector`. Stable judgment guidance lives in Profile
instructions; bounded request evidence and temporary `I*` or `S*` aliases live
in each task. Monitor state changes only after Harness Schema validation and
local candidate validation succeed. Trackers depend only on a narrow System
Agent runner, using a private bind-once adapter to avoid the App composition
cycle with HTTP transport.

## Consequences

- Production model invocation is centralized in the generic Agent runtime;
  Trackers no longer know about `LLMClient`, model configuration, Agent context,
  or output validators.
- `ModelSelector` and its compatibility export are removed. Model configuration
  uses the explicit `LLMModelConfig.name` and a direct configuration builder;
  Provider registries, clients, Schemas, context limits, and validation remain.
- Unlimited invalid output can keep the triggering HTTP Tool running until the
  model corrects itself, a user cancels, or a terminal runtime failure occurs.
  Monitor failure remains a warning and does not turn a successful target HTTP
  exchange into a failed request.
- A System root keeps no parent session because it is not a child Agent. The
  Observer records the triggering Tool as `parent_event_id`, allowing schema-v3
  viewers to present one or more complete System conversations beneath the HTTP
  Tool without duplicating events or changing browser persistence format.
- No System task, conversation, reasoning, result, tree, or budget state is
  added to backend persistence.

## Rejected alternatives

- Adding Tool eligibility or result fields to `AgentProfile` would mix
  authorization with caller-specific result contracts.
- Keeping direct Tracker calls or a Profile-external model selector would
  preserve a second model-runtime path and bypass common lifecycle behavior.
- A fixed correction count would let the Harness reject a recoverable model
  response for policy reasons unrelated to validity.
