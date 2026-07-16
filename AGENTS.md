# AGENTS.md

RESTScope is an exploratory project. Its final product boundary, user workflow,
and overall architecture are not settled. Existing code and design documents
show what has been tried or implemented; they do not automatically define the
final system.

The goal of agent work is to help the user learn what RESTScope should become
without quietly turning early prototypes into permanent architecture.

## Required reading

For every task, read this file first. Then read only the rules relevant to the
work:

- `docs/agent-rules/source-and-decisions.md`: authority, evidence, assumptions,
  and decision status.
- `docs/agent-rules/exploration-workflow.md`: investigation, approval gates,
  implementation scope, and task records.
- `docs/agent-rules/code-and-verification.md`: implementation quality and fresh
  verification.
- `docs/agent-rules/git-and-worktrees.md`: working-tree safety, worktrees, and
  Git authorization.

Also inspect the relevant code, tests, README sections, module design documents,
and task records. Do not load unrelated large documents by default.

## Minimum operating rules

- Inspect Git status before editing. Preserve all unrelated and pre-existing
  user changes.
- Separate facts, hypotheses, proposals, and user-approved decisions when the
  distinction affects the work.
- Treat current code and tests as executable evidence, not proof that the
  current architecture is the desired final architecture.
- Investigate and run local, non-destructive diagnostics without unnecessary
  approval, but keep them within the user's requested scope.
- Obtain user approval before implementing a new module, lasting abstraction,
  public interface change, persistence boundary, broad refactor, significant
  dependency choice, compatibility break, or live external action.
- Approval covers only the presented scope. Stop and ask again when evidence
  materially changes the problem or expands the proposed solution.
- Prefer the smallest reversible change that answers the current question. Do
  not add speculative frameworks or unrelated cleanup.
- Maintain a `docs/tasks/` record for approved work that is multi-step, spans
  sessions, or crosses architectural areas. Small edits and read-only
  investigations do not require one.
- Run fresh verification proportional to the change before claiming success.
  Report the command, result, and anything that remains unverified.
- Request explicit user authorization before creating a Git commit. Commit
  permission does not imply permission to push, merge, create a pull request,
  rewrite history, or delete branches or worktrees.
- Never discard user work or use destructive Git operations unless the user
  explicitly requests the exact operation after reviewing its impact.

## Project posture

There is no mandatory project-wide governance package at this stage. Planning
and architecture documents may be introduced later only when the user decides
they would clarify rather than constrain the exploration.

Module design documents under `docs/` remain useful context. When they conflict
with current code, tests, or a newer approved decision, expose the conflict and
ask which direction to preserve if the answer would affect implementation.

## Agent package boundary

This is a hard project constraint for code under `restscope/agent/`:

- Every Agent, including orchestration Agents, must live in its own named
  Python package such as `restscope/agent/planner/`.
- An Agent package owns its runtime, schemas, state, prompts, and directly
  supporting services. Do not add `<name>_agent.py`, `<name>_schemas.py`, or
  other implementation modules at the root of `restscope/agent/`.
- `restscope/agent/__init__.py` is only a stable public import facade.
- Cross-Agent imports must use the target package's public exports. Do not
  reach into another Agent's private implementation modules.
- Extract a shared package only when multiple real consumers have identical
  semantics and lifecycle requirements. Do not create speculative common base
  Agents or catch-all schema modules.
- Keep `tests/test_agent_package_boundaries.py` passing when adding or moving an
  Agent.
