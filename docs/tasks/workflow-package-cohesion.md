# Workflow Package Cohesion

> Superseded architecture note (2026-08-08): ADR 0001 replaces workflow-owned
> Agent/Tool placement with one generic Agent, Profile-selected global Tools,
> reusable Skills, and Harness-owned lifecycle. This record remains historical.

Status: Implemented and verified; intentionally uncommitted

## Objective

Organize RESTScope by runtime workflow and reserve the Agent name for modules
that directly call an LLM whose decision is the module's core domain behavior.

## Approved scope

- Move Operation Smoke, API Behavior Monitor, and Supervisor into top-level
  workflow packages.
- Rename the two deterministic outer coordinators and every current caller,
  trace, test double, and active-document reference.
- Nest the four Operation Smoke LLM Agent packages under their owning workflow.
- Remove the `restscope.agent` package and compatibility imports.
- Reduce workflow facades and the top-level `restscope` facade to their current
  callers' required interfaces.
- Reclassify deterministic coordinator, graph, and tracker spans as `CHAIN`.
- Update the project package rule and current code-reading documentation.

## Non-goals

- No prompt, schema, persistence, database, scheduling, or target behavior
  changes.
- No internal algorithm rewrite or large-file decomposition.
- No real LLM, target API, or Phoenix call.
- No commit, merge, worktree removal, or branch deletion without separate user
  authorization.

## Decisions

- A class named Agent must call an LLM directly, and the LLM must own that
  class's core domain decision. Tool use and multi-turn interaction are not
  required.
- Package placement follows workflow cohesion rather than component category.
- Workflow facades are external seams; child Agent packages are internal seams.
- Historical task, plan, and specification records retain their original
  terminology. This record supersedes their former package-boundary guidance.

## Verification

- Focused workflow-boundary, App assembly, Operation Smoke, Supervisor, API
  Behavior Monitor, and tracing tests:
  `123 passed, 1 skipped`.
- `uv run pytest -q`:
  `465 passed, 3 skipped, 2 failed`. Both failures were reproduced unchanged
  on the unmodified local `main` branch:
  `test_object_cardinality_requires_a_generator_set_that_always_conforms` and
  `test_smoke_execution_applies_constraints_and_traces_only_the_count`.
- `uv run --extra tracing pytest -q`:
  `465 passed, 3 skipped, 2 failed`, with the same two baseline failures.
- `uv run python -m compileall -q restscope tests`: passed.
- `git diff --check`: passed.
- Residual searches found no retired class, builder, property, or import names
  in production code, tests, or current project documentation. This task
  record names `restscope.agent` only to document that the package was removed;
  the boundary test constructs that retired path while asserting it cannot be
  imported.
- No real LLM, target API, or Phoenix service was called.
