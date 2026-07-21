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
- Test plans, inferred operation relationships, scheduler state, and Agent
  intermediate decisions are ephemeral and are not database records or durable
  artifacts.
- The database is not the architectural center of the Agent workflow. A stored
  schema source is a narrow input-storage capability, not a precedent for
  persisting parsed catalogs, plans, operation graphs, or Agent memory.
- Earlier database-backed Planner and catalog documents remain historical
  evidence only where later task records mark them as superseded.

Treat a proposal to add durable planning, inferred dependency storage, queue
recovery, Agent memory, or a database-first orchestration model as a change to
this decision. It requires fresh evidence and explicit user approval before
implementation.
