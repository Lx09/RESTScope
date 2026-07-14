# Exploration Workflow

## Start with evidence

Before proposing or modifying behavior:

1. inspect Git status and identify pre-existing changes;
2. read the smallest relevant set of code, tests, and documentation;
3. check recent task records or commits when they affect the request;
4. state important uncertainty, conflict, or working assumptions;
5. separate investigation findings from recommendations.

Prefer inexpensive local evidence over speculation. Do not create a large plan
or document hierarchy until the size and persistence of the work justify it.

## Work allowed without additional approval

Within the user's requested scope, an agent may:

- inspect files, history, branches, worktrees, configuration, and local runtime
  state;
- search and compare existing implementations;
- run local, non-destructive tests and diagnostics;
- summarize evidence and present alternatives with trade-offs;
- implement a small change whose exact behavior and scope the user has already
  approved.

Read-only authority does not include live calls that can mutate an external
service, test a real target, expose secrets, incur material cost, or contact
other people.

## Changes requiring approval

Stop and obtain approval before implementing a newly proposed:

- product capability or module;
- long-term architecture or cross-module abstraction;
- public API or externally visible behavior;
- database schema, migration, or persistence boundary;
- dependency, service, or infrastructure choice with lasting impact;
- broad refactor, compatibility break, or significant scope expansion;
- live action against an external service or real target system.

Present the evidence, 2–3 viable approaches when alternatives meaningfully
differ, a recommendation, explicit non-goals, and the verification strategy.
Keep the decision small enough for the user to evaluate.

## Implement the approved scope

Once approved:

1. implement the smallest coherent change;
2. preserve established boundaries where they remain useful;
3. avoid speculative extensibility and unrelated cleanup;
4. pause when new evidence materially changes the problem or solution;
5. run fresh verification and report remaining uncertainty.

Do not stretch approval to cover adjacent capabilities. A terminal request such
as “finish” means persist toward the approved outcome; it does not broaden the
authorized scope.

## Task records

Create or update a file under `docs/tasks/` when approved work is multi-step,
spans sessions, or crosses architectural areas. Include:

- objective and user-approved scope;
- explicit non-goals;
- current status;
- material decisions and assumptions;
- verification commands and observed results;
- remaining risks or follow-up work.

Use truthful states such as `Proposed`, `Approved`, `In progress`, `Blocked`, or
`Completed`. Mark work `Completed` only when the approved scope is implemented,
freshly verified, and preserved as required by the user. Read-only
investigations, short documentation corrections, and small localized edits do
not require a task file.
