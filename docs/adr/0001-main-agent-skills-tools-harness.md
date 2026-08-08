---
status: accepted
---

# Use one Agent runtime with global Tools, explicit Skills, and a Harness

RESTScope will converge on one configurable Agent runtime: one long-lived Main
Agent may request independent task-scoped Subagents, while Agent Profiles name
the exact model, Skills, global Tools, and bounded context sources each instance
may receive. Every owned Tool lives in a subject-specific Module under
`restscope.tools`; deterministic lifecycle, state injection, validation,
tracing, and logs belong to the Harness. This replaces workflow-owned Tool
contracts and role-driven Skill selection because those designs hid the actual
model Interface and allowed Tool Schema behavior to drift between Agents.

## Consequences

Global Catalog membership means authoritative discovery, not automatic access.
Built-in and external MCP Tool Catalogs remain separate, raw logs never become
Agent context, and Schema rules are enforced locally even when a provider does
not support strict calls. Existing named Agent classes remain only as explicit
migration exceptions until their behavior becomes Skills and Subagent Profiles.
The ephemeral operation FIFO and retry loop also belongs to the deterministic
Harness; it does not use a graph framework because RESTScope neither persists
nor resumes scheduler state.

## Profile-authorized launch

`AgentProfile` is the complete authorization declaration. It names one model
configuration, ordered Tool names, Skill names, bounded Context Source names,
and child Profile names. Every Profile used as a child supplies a bounded
plain-language description for its direct parent's developer message. Harness
construction rejects unknown or duplicate names, disabled models, missing
providers or Bindings, insufficient Skill grants, built-in/external Catalog
collisions, child cycles, and paths deeper than three. The public boundary is
intentionally one deep operation:
`HarnessRuntime.start_main_agent(profile_name)`. There is no public
resolve-then-assemble result that could be changed or broadened by a caller.

Main Agent and Subagent are lifecycles of the same `Agent` class. The Main
Agent may accept later tasks while retaining bounded in-memory history. A
Subagent accepts exactly its creation objective, has its own Profile and
Context, and receives no parent transcript or hidden state. It may start only
Profiles explicitly listed by its own Profile.

Selecting one or more Skills is the sole narrow exception to exact Profile Tool
names. The Harness automatically appends its global `skill.read` contract after
the Profile's ordered Tools. Stable system context lists selected Skill names,
descriptions, and optional versions but not instruction bodies. A successful
read acknowledges one selected name, records the normal assistant/tool
protocol, and then adds that Skill's bounded instructions as an untrusted user
message. Profiles cannot name `skill.read` directly and caller Binding factories
cannot replace it. Skill-required Tools and Context Sources are still checked
before launch whether or not the model loads the body.

Every Profile-started Main Agent or Subagent owns a private
`AgentPromptSession`. It assembles the stable system/developer prefix, tasks,
incremental Context replacements, Skill instruction messages, immutable Tool
and output schemas, and Tool-free compaction request. Context fingerprints and
messages are session-memory only and isolated from parents and siblings. The
Module remains private rather than becoming a public Prompt DTO or Registry.

An Agent Profile may grant both `plan.read` and `plan.update` to give that
session one private generic task Plan. The Harness constructs this state with
the Agent and never shares it across the Agent tree. Complete replacement keeps
the Interface small for its single writer; the Plan has no revision, lock,
persistence, recovery, scheduling, or Live Observer role. It remains separate
from Failure Resolution's reference and decision Worklist.

## Asynchronous tree control

The three global Subagent Tools form a fixed asynchronous protocol. Start
atomically reserves an open slot before submission. Wait sees only direct
children, does not cancel on timeout, and releases a terminal child's open slot
when its result is first collected. Cancel is cooperative because an in-flight
provider or Tool call keeps its own timeout. Closing a parent cancels every
uncollected descendant.

One App-memory tree control owns a default four-open-Agent registry, a
four-active-operation limiter, and a 1,000,000 weighted-token rollout budget.
Output tokens have weight 1.0 and non-cached input tokens weight 0.1. Bounded,
one-shot reminders fire when 50%, 25%, and 10% of the configured budget remain;
a response that exceeds the budget is charged but its Tool action is not
executed. No tree, queue, transcript, checkpoint, budget, or compacted summary
is persisted.

At 80% of the selected model's usable input window, the same model receives a
Tool-free compaction request. A valid Markdown summary atomically replaces the
dynamic history while system/developer rules and the original task remain.
Current Context Sources are fully re-anchored, while Skill bodies remain
reloadable rather than pinned. Two invalid summaries end with
`context_compaction_failed`; history is never silently dropped.
