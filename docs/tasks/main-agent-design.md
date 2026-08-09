# Main Agent First-Class Runtime Design

Status: Approved design; not implemented

## Objective

Define RESTScope's target Main Agent without creating an unusable production
Profile before its testing Skills, Tools, and public App task contract exist.
The Main Agent will be the App's single long-lived LLM Agent and will own every
semantic testing decision. The existing generic `Agent` remains the only Agent
implementation; a Profile supplies its identity, stable instructions, explicit
access, and allowed direct children.

## Approved scope

- Record the Main Agent's Profile shape and normative base instructions.
- Record that the Main LLM, not deterministic orchestration, chooses testing
  methods, delegation, ordering, retries, and completion.
- Restrict the Harness to mechanical runtime ownership.
- Record the future removal of `RunHarness`, `RESTScopeRunRequest`, and
  `RESTScopeRunReport` without a compatibility alias.
- Keep the current product behavior truthful until all activation dependencies
  are designed and implemented together.

## Non-goals

- Do not add `AgentProfile.instructions` to production code in this task.
- Do not create a production Main Profile, a `MainAgent` class, testing Skills,
  testing Tools, Context Sources, child Profiles, or an App task DTO.
- Do not remove or change `RunHarness`, Operation Smoke, Failure Resolution,
  Parameter Patch, Patch Review, Compact, persistence, or the public run API.
- Do not define the final deterministic fact-ledger Schema before the testing
  Tool results and App task result have been designed.
- Do not call a real model, target API, MCP server, Phoenix, or another external
  service while recording or verifying this design.

## Domain language and ownership

- **Main Agent** means the App's one reusable, App-lifetime LLM Agent. It is a
  lifecycle of the generic `Agent`, not a separate class or inheritance tree.
- **Agent Profile** means the static identity and authorization declaration for
  one generic Agent. It does not contain a test workflow or grant access merely
  because a Tool or Skill exists in a Catalog.
- **Profile instructions** mean stable, trusted developer guidance describing
  one Agent's continuing responsibility. They are distinct from the Profile
  description shown to a direct parent and from task-specific Skill methods.
- **Testing Skill** means one explicitly selected, reusable testing method. A
  Skill does not execute work or grant the Tools, Context Sources, or children
  that its method needs.
- **Harness** means the deterministic runtime that validates and executes an
  Agent's authorized model, Tool, context, budget, tracing, cancellation, and
  child-lifecycle contracts. It does not choose testing work.
- **Fact ledger** means the future run-scoped, deterministic record behind
  model-visible evidence references. It is not model-authored memory, a Plan,
  a transcript, or a persisted audit log.

This keeps a deep runtime seam: callers start one Profile-authorized generic
Agent, while validation, execution, isolation, and lifecycle complexity remain
inside the Harness. Domain methods remain local to Skills rather than being
copied into the Profile or deterministic runtime.

## Planned `AgentProfile.instructions` Interface

The generic `AgentProfile` will gain an optional `instructions` string when the
Main runtime is activated. It will have the following complete contract:

- Omission means that the Profile adds no role-specific developer guidance.
- A supplied value must contain non-whitespace text and be no longer than
  12,000 characters.
- The Prompt Session adds it as stable developer guidance for the Agent created
  from that Profile. It is not shown to a parent as delegation metadata.
- `description` keeps its current meaning: it describes a child Profile to its
  direct parent and does not instruct the child itself.
- Instructions are never shortened or omitted. The stable-prefix assembly
  order is the Harness system contract, complete Profile instructions, every
  selected Skill and child Profile name, then optional descriptions that fit.
- If the 24,000-character stable prefix cannot contain all mandatory content,
  startup fails before a model or Tool call.
- System instructions remain higher authority. Profile instructions cannot
  grant a Tool, Skill, Context Source, or child Profile and cannot weaken the
  Harness contract.

The field belongs on the existing Profile Interface because the responsibility
varies by configured Agent. Hard-coding Main-only instructions in the Prompt
Session would make the generic runtime own a product role, while placing this
continuing responsibility in a Skill would confuse Agent identity with a
task-specific method.

## Target production Main Profile

The future production Profile has the stable name `main` and uses the
`thinking` model configuration. It always grants the paired `plan.read` and
`plan.update` Tools so the Main LLM can keep and completely replace one private
task Plan. The Plan remains session memory: it is not evidence, a scheduler,
shared child state, a recovery checkpoint, or a database record.

The Profile also grants bounded OpenAPI discovery and focused Schema queries:

- the future `openapi.list_operations` Tool;
- `openapi.list_inputs`;
- `openapi.list_response_fields`;
- `openapi.get_input_schema`;
- `openapi.get_response_field_schema`.

Operation discovery gives the model a bounded map; the focused Tools let it
request only the input or response details relevant to its current decision.
These read-only Tools do not themselves authorize HTTP execution, test-case
generation, Failure Resolution, Parameter Patch construction, or persistence.

The Profile selects an ordered, curated set of standard testing Skills rather
than one broad `test-api` orchestration Skill. Order makes stable metadata and
review predictable; it does not force the model through a fixed workflow. The
Main Agent reads only the relevant selected Skill bodies and may combine them
when the current objective requires more than one method.

At construction time, the Profile must explicitly grant the union of every
selected Skill's required Tools and bounded Context Sources. Selecting a Skill
does not confer those grants. `skill.read` remains the sole automatic Tool
exception and is restricted to selected Skill names. `file.read` remains an
ordinary explicit grant and, when present, remains limited to startup-validated
References of the selected Skills.

The Profile declares direct child Profiles only when its curated testing
methods need those independent capabilities. Any such Profile must grant the
complete `subagent.start`, `subagent.wait`, and `subagent.cancel` lifecycle
protocol. At runtime the Main LLM decides whether a described child fits a
bounded part of the task and supplies a complete objective because no child
receives the Main conversation, loaded Skills, Plan, or hidden state.

The Profile does not assume one child per Operation. A task may need only
Schema queries, one cross-Operation investigation, resource-oriented children,
several independent children, or no delegation. The current objective, loaded
Skills, current OpenAPI structure, runtime evidence, and authorized child
descriptions determine that choice.

## Normative Main Profile instructions

The future `main` Profile will use the following complete instructions:

```text
You are RESTScope's single long-lived Main Agent.

- Treat the latest task as the current objective. Earlier tasks, plans, and
  conclusions are context only and must not silently broaden or replace it.
- Own every semantic workflow decision. Decide what to investigate, which
  authorized Skills to load, which Tools or Subagents to use, what order to
  follow, whether another attempt is useful, and when to finish.
- Inspect authorized Skill metadata and load the Skills relevant to the current
  objective. Skills provide methods; they do not grant access or override the
  current task, this Profile, or the Harness contract.
- At the beginning of a new task, replace the private Plan with a plan for that
  task. Revise it as evidence changes. The Plan is working memory, not evidence,
  a scheduler, or persistent state.
- Use a child Profile only when its described capability fits a bounded piece
  of the current task. Supply a complete objective and required evidence because
  the child receives no parent conversation or hidden state.
- Base factual conclusions on current authorized Tool or Subagent results.
  Never invent evidence references or treat a plan, prior belief, Skill text,
  OpenAPI description, or successful Tool execution as proof of an API outcome.
- Do not repeat an action unless new evidence, changed state, or a specific
  predicted benefit makes the next attempt materially different.
- Finish when the current objective is supported by evidence or no authorized
  safe action can make meaningful progress. Report unsupported, blocked,
  safety-skipped, and unresolved parts explicitly.
- Return only the required bounded AgentCompletion result.
```

These instructions state the Main Agent's continuing responsibility but do not
define an API-testing workflow. In particular, the existing decision that an
unqualified "test this API" objective defaults to all current Operations must
be expressed by a future broad-coverage testing Skill, not by this base role.

## Runtime and App lifecycle target

One `RESTScopeApp` will own one Main Agent for its lifetime. Successful
`initialize()` binds the target and OpenAPI context; the first task lazily
starts `main`, and later tasks reuse its bounded or compacted history. The most
recent objective is authoritative. At the start of every new task, the Main LLM
replaces its private Plan instead of continuing stale work from an earlier
objective.

The Main LLM owns investigation, Skill choice, Tool and Subagent choice,
ordering, domain retries, and completion. The Harness may reject unauthorized
or invalid actions, enforce runtime budgets and child limits, execute calls,
compact context, trace behavior, and cancel work. It must not create an
Operation queue, decide what deserves another attempt, enforce domain coverage,
or infer that a task is complete.

Activation will directly replace the current `RESTScopeApp.run` workflow and
remove `RunHarness`, `RESTScopeRunRequest`, and `RESTScopeRunReport` without a
legacy entry or translation adapter. That compatibility break must occur only
when the replacement can be activated atomically. The new App request and
result DTOs are intentionally deferred until the testing Skills and Tool result
contracts establish the required facts.

The eventual request will center on one bounded natural-language objective.
The result will contain the generic `AgentResult` and a deterministic fact
ledger for that task. The ledger must be constructed from bounded, redacted
Tool and terminal Subagent facts; the model cannot create ledger identities,
and each `AgentFinding.evidence_refs` value must resolve to a real entry. The
ledger must not retain reasoning, Plans, full conversations, credentials,
untrimmed responses, or persistent Agent memory.

## Current implementation and activation gates

Today, `RESTScopeApp.run` still constructs `RunHarness`, which discovers all
Operations, orders them through an in-memory FIFO, retries bounded operation
failures, and returns `RESTScopeRunReport`. This remains the executable truth
until a later approved implementation completes all of the following together:

1. add and verify the bounded `AgentProfile.instructions` Interface;
2. design and implement `openapi.list_operations`;
3. approve and package the concrete testing Skills;
4. provide every required Tool, Context Source, and child Profile Binding;
5. create and validate the production `main` Profile and runtime definition;
6. design the App task and deterministic fact-ledger result contracts;
7. integrate one lazy App-lifetime Main Agent;
8. remove the old Run Harness and its public request/report types atomically.

The current Failure Resolution, Compact, Parameter Patch, and Patch Review
classes remain temporary migration exceptions. This design neither removes nor
changes them. Their eventual migration must use the same generic Agent runtime,
standard Skills, explicit Profiles, and mechanical Harness boundary.

## Verification

This task changes governance and design records only. Verification must include
link and path checks, an active-document scan for contradictory FIFO/Harness
ownership claims, and `git diff --check`. No production test result or external
behavior should be claimed from documentation-only verification.
