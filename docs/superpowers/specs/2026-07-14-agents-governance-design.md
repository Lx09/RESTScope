# Exploratory Agent Governance Design

## Status

Approved design. Implemented and verified in the current `main` worktree and
included in the user-authorized governance checkpoint.

## Context

RESTScope is an exploratory project. It already contains working MVP layers,
module-level design documents, and several completed task records, but the user
has not committed to a final product boundary or overall architecture.

The existing `AGENTS.md` assumes that `PRD.md`, `ARCHITECTURE_SPEC.md`, an
active version plan, and a large `docs/agent-rules/` hierarchy already exist.
Those files do not exist in the current repository. Requiring them makes the
instructions impossible to follow and incorrectly treats exploratory design
material as settled architecture.

## Goals

- Make agent behavior appropriate for an exploratory codebase.
- Keep the user in control of architecture and product decisions.
- Distinguish observed facts, working hypotheses, proposals, and approved
  decisions.
- Permit useful investigation without requiring approval for every read-only
  action.
- Require approval before changes that could prematurely establish a lasting
  architecture.
- Preserve user changes and require fresh verification before completion
  claims.
- Keep the rule set small enough to evolve with the project.

## Non-goals

- Define RESTScope's final product scope or architecture.
- Introduce a mandatory PRD, roadmap, release process, or version-plan system.
- Require a task file for every investigation or small edit.
- Allow agents to commit, push, or create pull requests automatically.
- Replace module design documents with a single speculative master design.

## Chosen Structure

The repository will use a concise root `AGENTS.md` plus four focused rule files:

```text
AGENTS.md
docs/agent-rules/
  source-and-decisions.md
  exploration-workflow.md
  code-and-verification.md
  git-and-worktrees.md
```

The root file will explain the project state, define the minimum operating
rules, and route agents to the relevant detailed rule. The four detailed files
will be self-contained and will not reference nonexistent governance documents.

More rule files should be added only after repeated work demonstrates a stable
need. The repository must not recreate the previous large governance hierarchy
preemptively.

## Source and Decision Model

Agents will use this authority order:

1. The user's current explicit instruction.
2. User-approved decisions, scopes, and plans recorded in the repository.
3. Executable evidence: tests, current code behavior, schemas, and migrations.
4. Current project and module documentation.
5. Clearly labeled working assumptions.

When sources disagree, agents must expose the conflict instead of silently
choosing the most convenient source. Existing design documents are evidence of
intent, but they are not final architecture unless the user has explicitly
accepted them as such.

Material statements should be classified when ambiguity matters:

- **Fact:** directly supported by the repository or a fresh command result.
- **Hypothesis:** a testable explanation or possible direction.
- **Proposal:** a recommended change awaiting approval.
- **Decision:** a choice explicitly approved by the user.

## Exploration and Approval Workflow

Agents may perform the following without additional approval when they are
within the user's requested scope:

- inspect files, history, worktrees, configuration, and local runtime state;
- search the codebase and compare existing implementations;
- run local, non-destructive tests and diagnostics;
- summarize evidence, identify uncertainty, and propose alternatives;
- implement a small change whose exact behavior and scope the user already
  approved.

Agents must stop and request approval before implementing any newly introduced:

- product capability or module;
- long-term architecture or cross-module abstraction;
- public API or externally visible behavior;
- database schema, migration, or persistence boundary;
- dependency or infrastructure choice with lasting impact;
- broad refactor, compatibility break, or significant scope expansion;
- live call that can affect an external service or real target system.

Approval applies only to the presented scope. If implementation reveals a
materially different problem, the agent must report the new evidence and ask
again instead of stretching the earlier approval.

## Task Records

Persistent task files under `docs/tasks/` are required for approved work that
is multi-step, spans sessions, or changes multiple architectural areas. A task
file should record objective, approved scope, non-goals, current status,
decisions, verification, and remaining risks.

Read-only investigations, short documentation corrections, and small localized
edits do not require a task file. Task records must describe actual state; they
must not say `Completed` while required verification or preservation work is
still outstanding.

## Code and Verification Rules

Implementation must favor the smallest change that can answer the current
question or validate the current hypothesis. Agents should reuse established
project boundaries where they are still suitable, but may challenge them with
evidence rather than treating them as permanent.

For Python changes:

- keep modules focused and public interfaces explicit;
- add type hints and concise docstrings where they clarify contracts;
- comment design constraints and non-obvious reasoning, not line-by-line
  mechanics;
- avoid speculative abstraction and compatibility layers without a current
  consumer;
- do not mix unrelated cleanup into an approved change.

Verification must be proportional to risk and freshly executed. Localized
changes require focused tests; cross-cutting changes require the relevant
focused tests plus the full suite when practical. Agents must report commands
and actual outcomes, distinguish untested external behavior, and never use an
old task record as proof that the current working tree passes.

## Git and Worktree Rules

Agents must inspect Git status before editing and preserve unrelated or
pre-existing user changes. They must not discard, overwrite, stage, or reformat
unrelated work.

A separate worktree is preferred when work is risky, experimental, likely to
overlap existing uncommitted changes, or intended to proceed in parallel. A
worktree is not required for every small approved edit.

Agents may edit and verify files after scope approval, but may create a commit
only after the user explicitly authorizes the commit. Commit authorization does
not imply permission to push, create a pull request, merge, rewrite history, or
delete a branch or worktree; those actions require their own explicit scope.

Destructive Git operations are prohibited unless the user explicitly requests
the exact operation after being informed of the affected changes.

## Migration from the Current Rules

Implementation will:

1. Replace the root `AGENTS.md` with an exploratory-project entry file.
2. Add the four chosen rule files under `docs/agent-rules/`.
3. Remove references to missing PRD, architecture, roadmap, release, audit, and
   version-plan rules.
4. Preserve existing module design and task documents without promoting them to
   project-wide authority.
5. Validate that every link and required file referenced by `AGENTS.md` exists.

The migration will not modify runtime code, dependencies, existing task status,
or current uncommitted feature work.

## Success Criteria

- A new agent can follow `AGENTS.md` without encountering a missing required
  file.
- The instructions explicitly state that RESTScope is exploratory and that its
  overall architecture is unsettled.
- Read-only investigation can proceed autonomously.
- Architecture-affecting implementation pauses for user approval.
- Task-file requirements scale with task complexity.
- Fresh verification is required before completion claims.
- Existing user changes are protected.
- Commits require explicit user authorization.
- The root entry file and detailed rules contain no contradictory authority or
  workflow requirements.
