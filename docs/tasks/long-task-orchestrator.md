# Long-task Orchestrator

Status: Implemented and verified on local `main`; uncommitted

## Objective

Replace RESTScope's single taskless, long-lived Main Agent with an App-lifetime
in-memory Orchestration Module. The Module keeps an immutable Goal Contract and
revisioned Task Ledger, uses a registered no-Tool Orchestrator System Agent to
roll the plan forward, and starts a fresh registered Task Executor System Agent for
each bounded task.

## Approved scope

- Add `OrchestrationRuntime.run(focus)` as the only long-task Interface.
- Reuse the existing registered System Agent lifecycle for Orchestrator and Task Executor.
- Keep Goal immutable, Plan Revision reasons retained, and Attempts append-only.
- Preserve the existing Parameter Patch child behind the Task Executor Profile.
- Remove the production taskless Main startup path.
- Keep the Ledger in memory for one App lifetime only.
- Update current architecture documentation and package-boundary tests.

## Non-goals

- Cross-process recovery or a database-backed Planner.
- An independent Verifier or durable Agent memory.
- Live Observer redesign.
- A global outer round or token limit.
- Direct evidence-Catalog verification of Task-Executor-authored evidence references.

## Decisions and risks

- The Orchestrator judges progress from the bounded Task Execution Result and Ledger.
  Local code validates structure and state transitions, but not evidence-ref truth.
- Rolling planning materializes only a small future milestone set and one active Task.
- A Replan cannot modify the Goal or Attempt history and cannot be a no-op.
- New evidence may reopen completed work through a new revision while preserving
  the previous conclusion as superseded history.
- Each Orchestrator prompt retains the latest causal Revision and Attempt,
  joins an Attempt to its Task and Milestone, and omits whole older records to
  remain within 18,000 characters.
- Replan, Task, and Task Execution Result text is limited to 4,000, 4,000, and
  6,000 aggregate characters respectively before Ledger mutation.

## Verification

- `uv run ruff check restscope tests` — passed.
- `uv run python -m compileall -q restscope tests` — passed.
- `uv run pytest -q` — 633 passed, 2 skipped.
- `git diff --check` — passed.
- The skipped tests are existing opt-in live service/provider checks.
- `mypy` is not installed in the locked workspace environment, so no mypy
  result is claimed.
