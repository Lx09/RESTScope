# Source and Decision Rules

## Authority order

Use the following order when deciding what governs the current task:

1. The user's current explicit instruction.
2. User-approved decisions, scopes, and plans recorded in the repository.
3. Executable evidence from current tests, code behavior, schemas, and
   migrations.
4. Current project and module documentation.
5. Clearly labeled working assumptions.

Higher-authority sources override lower-authority sources for the current task.
Do not silently resolve a meaningful conflict. Report the conflicting evidence,
its likely impact, and the smallest decision the user needs to make.

## Evidence labels

Use these labels when uncertainty or authority matters:

- **Fact:** directly supported by repository content or a fresh command result.
- **Hypothesis:** a testable explanation or possible direction.
- **Proposal:** a recommended change that has not been approved.
- **Decision:** a choice explicitly approved by the user.

Ordinary status updates do not need labels on every sentence. Use them where a
reader might otherwise mistake an assumption or proposal for settled project
direction.

## Documentation status

Existing design documents record valuable intent and analysis, but RESTScope
does not yet have a final project-wide architecture. Before treating a document
as binding, check whether its relevant decisions were approved, implemented,
and preserved in the current repository.

If documentation and implementation differ:

1. verify the difference against the current tree;
2. determine whether it blocks the requested work;
3. report it before making an architecture-affecting choice;
4. update documentation only as part of an approved scope.

Do not invent missing product requirements. Make a narrow, reversible
assumption only when it does not materially change scope, and state the
assumption in the result. Otherwise ask the user.

## Recording decisions

Record a decision when it will matter after the current conversation. Use the
smallest suitable location:

- the active `docs/tasks/` file for task-specific decisions;
- a relevant module design document for an approved module-level contract;
- a dedicated design or decision document for a cross-cutting choice.

Record what was decided, why, the approved scope, and important alternatives or
risks. Do not describe a proposal as a decision or a partially verified change
as completed.

## Current architecture decision

The user has explicitly accepted the current lightweight, dynamic architecture
as the active project direction.

- OpenAPI operations are discovered from the supplied source at runtime.
- Testing evidence may change dependency analysis and scheduling while a run is
  in progress.
- The approved target Main Agent owns semantic testing decisions, including
  Skill and Tool choice, delegation, ordering, domain retries, and completion.
  The Harness enforces and executes authorized runtime contracts but does not
  choose testing work. `RESTScopeApp.start()` now blocks on one taskless Main
  loop; the former FIFO Run Harness and its request/report DTOs are retired.
  The initial Main Profile intentionally has only its private Plan Tools until
  separate testing Skills and Tools are approved and connected.
- Deterministic runtime code may synchronously start a registered System Agent
  through `run_system_agent(profile_name, task)`. It is another lifecycle of the
  same generic Agent, not a domain-specific Agent class. Every invocation is an
  independent root session and tree. Its unchanged `AgentProfile` remains the
  sole source of model, Tool, Skill, Context Source, and child-Profile grants;
  registration binds only its bounded task adapter and result contract. System
  usage is counted without a token budget limit. The Harness validates every
  final output and supplies bounded specific feedback indefinitely until the
  output is valid or cancellation, shutdown, Provider failure, or safe
  compaction failure terminates the run.
- Request Generation owns Generator and Constraint semantics, compilation,
  solving, schema snapshots, serialization, validation, and App-lifetime
  revisioned configuration. `request_generation.validate_patch` is read only;
  `parameter_patch.apply` is the sole Tool that atomically changes that state.
  Exact producer operation/status/media/selector bindings participate in state
  identity. Apply records those operation input-source propositions and rolls
  back an in-memory publication if the database commit fails. Values are not
  materialized into shared pools: Batch execution freezes values parsed from
  current observations or complete resource instances with the selected
  revision. It persists or reuses one immutable abstract state snapshot before
  sending the first request, and successful observations reference it.
  The Harness owns deterministic operation execution and mechanical Tool
  bindings. `test_case.run_batch` returns bounded inline evidence without a Test
  Case registry. Neither Module owns semantic test selection or retry decisions.
- Test plans, inferred operation relationships, scheduler state, and Agent
  intermediate decisions are ephemeral and are not database records or durable
  artifacts.
- The database is not the architectural center of the Agent workflow. A stored
  schema source is a narrow input-storage capability, not a precedent for
  persisting parsed catalogs, plans, operation graphs, or Agent memory.
- The user has separately approved a narrow API Behavior Monitor evidence
  catalog: normalized operations; resource names and immutable direct identity
  fields; operation-resource role propositions; recursively merged current
  resource instances with logical deletion; exact RESOURCE and VALUE_REUSE
  consumer input-source propositions; and immutable abstract Batch state.
  It retains the latest 100 complete valid 2xx JSON observations per operation,
  including original response JSON text and an actual request envelope with
  Authorization, Cookie, token, API-key, and similarly sensitive headers
  removed. This local raw evidence requires the same database protection as
  other target evidence. It does not authorize LLM reasoning, extraction rules,
  evolved-IR recovery snapshots, plans, queues, or general Agent memory.
  Unknown resource identity fields use one registered no-Tool `fast` System
  Agent Profile. Its dynamic `I*` aliases are restricted by a per-invocation
  Schema and local validation before Monitor state changes. Identity fields may
  be composite; generation selects all components from one complete current
  resource instance. Response values are discovered and parsed directly from
  observations, never copied into a shared producer-value table.
- Operation Smoke, its persistent Failure/Attempt/Generator tables, specialized
  Failure/Patch/Review Agents, candidate registry, Finalizer, and evaluation
  package are retired. Standard `resolve-operation-failures` and
  `apply-parameter-patch` Skills describe the reusable methods. The latter must
  validate and value-review a complete Patch before invoking the sole mutation
  Tool. Successful application proves only an in-memory generation-state
  change, not target API success.
- The user has also approved a browser-only Live Observer recovery boundary.
  The loopback React page may keep the latest five complete schema-v3 snapshots
  in same-origin IndexedDB. Those snapshots contain exactly the already-redacted
  UI payload, including raw Provider Reasoning, visible target credentials,
  Agent prompts, Tool results, HTTP exchanges, Subagent relationships, System
  Agent roots associated to their triggering HTTP Tool by `parent_event_id`,
  and the latest Main Agent Plan projected as Todo. Batch and Patch activity appears as
  ordinary Tool detail; pre-v3 browser history is ignored. This is local testing history, not
  backend evidence or App recovery: no workflow reads it, no API, Phoenix span,
  or SQLite schema exposes the Reasoning, and clearing browser site data deletes
  the complete history.
- Earlier database-backed Planner and catalog documents remain historical
  evidence only where later task records mark them as superseded.

Treat a proposal to add durable planning, inferred dependency storage, queue
recovery, Agent memory, or a database-first orchestration model as a change to
this decision. It requires fresh evidence and explicit user approval before
implementation.
