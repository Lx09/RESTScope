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
- Request Generation owns Generator and Constraint semantics, compilation,
  solving, schema snapshots, serialization, and current generation
  configuration. The Harness owns deterministic operation execution,
  run-local Test Cases, Probe evidence, and mechanical Tool bindings; neither
  one owns semantic test selection or retry decisions.
- Test plans, inferred operation relationships, scheduler state, and Agent
  intermediate decisions are ephemeral and are not database records or durable
  artifacts.
- The database is not the architectural center of the Agent workflow. A stored
  schema source is a narrow input-storage capability, not a precedent for
  persisting parsed catalogs, plans, operation graphs, or Agent memory.
- The user has separately approved a narrow API Behavior Monitor evidence
  catalog: resource names and aliases, learned identifier selectors, typed
  identifier values, latest per-operation read/write usage, response-value
  monitor registrations and selectors, deduplicated typed response values, and
  latest monitor errors. The catalog also retains the latest 100 valid,
  non-truncated 2xx JSON observations per operation as flattened, typed,
  non-null scalar evidence so a later Response Value registration can backfill
  its pool. This deliberately includes sensitive-looking fields and therefore
  requires the same database protection as other target evidence. Full
  response bodies are never retained. Response-contract checks and evolved
  OpenAPI IR stay in memory for the current App lifetime. This exception does
  not authorize raw-response, LLM-reasoning, evolved-IR snapshot, plan, queue,
  or general Agent-memory persistence.
- The user has also approved a browser-only Live Observer recovery boundary.
  The loopback React page may keep the latest five complete schema-v2 snapshots
  in same-origin IndexedDB. Those snapshots contain exactly the already-redacted
  UI payload, including raw Provider Reasoning, visible target credentials,
  Agent prompts, Tool results, HTTP exchanges, Smoke Batches, Subagent
  relationships, and the latest Main Agent Plan projected as Todo. Failure
  Resolution's private Worklist is retained only inside ordinary Tool detail,
  not as page-level state. This is local testing history, not
  backend evidence or App recovery: no workflow reads it, no API, Phoenix span,
  or SQLite schema exposes the Reasoning, and clearing browser site data deletes
  the complete history.
- Earlier database-backed Planner and catalog documents remain historical
  evidence only where later task records mark them as superseded.

Treat a proposal to add durable planning, inferred dependency storage, queue
recovery, Agent memory, or a database-first orchestration model as a change to
this decision. It requires fresh evidence and explicit user approval before
implementation.
