# Long-task Orchestrator

Status: Implemented and verified in a dedicated feature worktree; uncommitted

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
- Cross-Task REST API exploration strategy belongs to the Orchestrator's stable
  instructions because every planning decision needs it. Task Executors follow
  one assigned Operation and testing purpose, load Failure Resolution only when
  execution evidence requires it, and never choose the next Operation or phase.
- The retired exploration Skill is not retained as a compatibility path. Its
  execution-level safety rules live in Task Executor instructions.

## Verification

- Focused Profile, Skill, Orchestration, Ledger, Failure Resolution, and Patch
  tests — 58 passed.
- `uv run ruff check restscope tests` — passed.
- `uv run python -m compileall -q restscope tests` — passed.
- `uv run pytest -q` — 630 passed, 13 skipped.
- `uv build` — source distribution and wheel built successfully; both contain
  only the two retained production Skills.
- Changed Python scope contains no `typing.Any`; old exploration Skill names and
  ownership descriptions are absent from production code, tests, and current
  documentation; `git diff --check` passed.
- The skipped tests are existing opt-in live service/provider checks. No real
  model, target API, MCP server, or other external service was called.
