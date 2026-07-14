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
