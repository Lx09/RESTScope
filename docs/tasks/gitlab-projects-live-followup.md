# GitLab Projects Live Follow-up

## Status

Repairs implemented and focused verification complete. The current five-
operation live acceptance remains blocked by a test/workflow scope decision.

## Objective and approved scope

Run the current five-operation GitLab Projects live acceptance test against the
disposable local container, call the configured DeepSeek models, inspect the
new Phoenix traces, and repair evidence-backed defects that stay within the
existing Operation Smoke and LLM provider boundaries.

The run may create, update, and delete GitLab Projects.  It has a ten-minute
hard deadline.  Existing GitLab live-test Phoenix projects and exported trace
JSON were removed before the run; evaluation datasets, contract projects, and
historical `artifacts/phoenix-exports` were deliberately preserved.

## Live evidence

The first run was interrupted after exceeding the deadline.  It scheduled only
three of the five operations, completed 30 Batch cases, and emitted 150 LLM
spans.  Of the resulting Solve corrections, 119 rejected an assistant output
solely because it contained more than one tool call.  The rejected calls were
predominantly independent Test Case Catalog and Parameter Memory reads, so the
Agent repeatedly asked for the same evidence without making progress.

One separate DeepSeek response contained tool calls but omitted the required
`reasoning_content`.  DeepSeek's thinking-mode contract requires that content
to be replayed verbatim; RESTScope cannot safely invent it.

## Repairs

- Failure Solve may execute a bounded group of read-only Catalog and Parameter
  Memory queries from one assistant output.  Patch and HTTP calls remain
  single-call outputs, and a mixed or invalid group is rejected before any call
  executes.
- The DeepSeek provider retries one tool-calling response that omits
  `reasoning_content`.  The retry happens before any tool executes.  A second
  incomplete response still fails, and successful retries are counted on the
  LLM trace.
- Parameter Patch applies its existing three-strike guard to repeated
  executable-boundary failures as well as malformed DTOs. A candidate that
  repeatedly changes an unauthorized input or violates the same sampling rule
  no longer consumes the full 20-output budget.
- Failure Solve sends the authoritative `FailureSolveDecision` schema to the
  provider. Tool selection remains available; terminal JSON receives the same
  schema guidance already used by Parameter Patch.

## Verification

- Each new regression was observed red before its repair and green afterward.
- Parameter Patch, Failure Solve, Operation Smoke coordination, Agent Context,
  and DeepSeek provider focused suites: 66 passed.
- The repaired live run enforced its hard stop at 600.49 seconds. It completed
  two Batch rounds for `GET /api/v4/projects`, which finished successfully,
  then one Batch for `POST /api/v4/projects` before the deadline interrupted
  its third Solve item. The remaining three operations were not scheduled.
- Compared with the original trace, grouped Catalog/Memory calls now execute
  and repeated invalid Patch candidates stop at three outputs. The final trace
  contained no independent technical error before the deliberate timeout.

## Decision still required

The live assertion asks all five operations to complete at least one Batch in
one ten-minute App run. Production scheduling instead completes the full
multi-round Smoke workflow for one operation before starting the next. A
failure-rich `POST /projects` operation can therefore consume the entire test
deadline even after the observed Agent loops are removed.

Choose whether the acceptance should exercise one direct Batch per operation,
or continue exercising full Smoke convergence with a larger/per-operation time
budget. Changing production Smoke to stop after one Batch is not included in
these repairs.

## Non-goals

- No new Catalog query DTO or persistence.
- No change to Patch or HTTP side-effect boundaries.
- No fabricated DeepSeek reasoning content or unbounded provider retries.
- No full-suite run unless later evidence requires one.
