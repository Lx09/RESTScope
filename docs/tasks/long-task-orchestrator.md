# Long-task Orchestrator

Status: Implemented and verified; Git delivery pending authorization

## Objective

Replace RESTScope's single taskless, long-lived Main Agent with an App-lifetime
in-memory Orchestration Module. The Module keeps an immutable Goal Contract and
revisioned Task Ledger, uses a registered no-Tool Orchestrator System Agent to
roll the plan forward, and starts a fresh registered Worker System Agent for
each bounded task.

## Approved scope

- Add `OrchestrationRuntime.run(focus)` as the only long-task Interface.
- Reuse the existing registered System Agent lifecycle for Orchestrator and Worker.
- Keep Goal immutable, Replans revisioned, and Attempts append-only.
- Preserve the existing Parameter Patch child behind the Worker Profile.
- Remove the production taskless Main startup path.
- Keep the Ledger in memory for one App lifetime only.
- Update current architecture documentation and package-boundary tests.

## Non-goals

- Cross-process recovery or a database-backed Planner.
- An independent Verifier or durable Agent memory.
- Live Observer redesign.
- A global outer round or token limit.
- Direct evidence-Catalog verification of Worker-authored evidence references.

## Decisions and risks

- The Orchestrator judges progress from the bounded Worker result and Ledger.
  Local code validates structure and state transitions, but not evidence-ref truth.
- Rolling planning materializes only a small future milestone set and one active Task.
- A Replan cannot modify the Goal or Attempt history and cannot be a no-op.
- New evidence may reopen completed work through a new revision while preserving
  the previous conclusion as superseded history.

## Verification

- `uv run ruff check restscope tests` — passed.
- `uv run pytest -q` — 610 passed, 13 skipped.
- The skipped tests are existing opt-in live service/provider checks.
- `mypy` is not installed in the locked workspace environment, so no mypy
  result is claimed.
