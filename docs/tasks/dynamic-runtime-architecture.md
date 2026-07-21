# Dynamic Runtime Architecture Decision

Status: Completed

## Objective

Record the user's decision to formally accept RESTScope's current lightweight,
runtime-driven architecture and make continued exploration without plan or
orchestration persistence a project rule.

## Approved scope

- Make dynamic runtime discovery and scheduling the active architecture.
- Keep test plans, inferred dependencies, queues, and Agent intermediate state
  ephemeral.
- Clarify that current schema-source storage is narrow input persistence rather
  than approval for a database-centered Agent architecture.
- Require new evidence and explicit user approval before durable planning,
  orchestration state, or Agent memory is introduced.

## Non-goals

- Removing the existing schema-source table.
- Changing runtime code, public APIs, database migrations, or tests.
- Declaring the current MVP to be the final product architecture.
- Preventing future architecture changes that the user explicitly approves.

## Decision

RESTScope will evolve through small runtime-driven experiments. Operations,
dependencies, scheduling decisions, and next actions are derived from current
inputs and execution evidence. Planning and orchestration state are not durable
system-of-record data.

Historical Planner and database-backed catalog work remains useful evidence,
but it does not govern the active architecture.

## Verification

Observed on 2026-07-20:

- A targeted content scan found the decision in `AGENTS.md` and both relevant
  rule files.
- `git diff --check` exited successfully.
- No runtime code, public API, database migration, or test was changed.
