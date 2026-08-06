# Findings & Decisions

## Requirements
- The approved follow-up requires the collapsed summary and complete detail to
  occupy the same visual origin. Opening lasts 300 ms and closing lasts 200 ms;
  Agent messages, Tool/HTTP, and Smoke Batch nodes all use the same motion.
- The existing implementation mounts a fixed-height detail only after
  `expanded` changes and performs a non-animated G6 render, so both the content
  and the graph currently jump directly to their final geometry.
- Ant Design 6.5.3 defines `motionDurationSlow` as 0.3 s,
  `motionDurationMid` as 0.2 s, and provides the approved `motionEaseOut` and
  `motionEaseInOut` curves. Card's current `styles` API remains valid.
- G6 5.1.1 can animate user-triggered layout updates, but an HTML/React node's
  abstract `size` field does not resize its visible DOM key shape. The animation
  must target the key and key-container `x/y/width/height`, each port transform,
  node position, and edge endpoints. Normal SSE draws should stay immediate.
- G6 refreshes all element data when node/edge animation options change. Those
  options must be installed before new structural data; reversing that order
  erases the meaningful size diff and leaves the expanded React content clipped
  inside the old graph frame. Restoring static options needs one static draw so
  the refresh cannot leak into the next SSE update.
- Real Chromium measurement found Ant Design Flex hides an empty footer with
  `display:none`. Messages without Tool-call metadata therefore rendered 32 px
  shorter than the canvas model. A non-visual child keeps the fixed footer in
  layout without inventing user-facing metadata.
- Replace Failure Dedup and Failure Solve with one continuous Failure Resolution Agent.
- Initial prompt contains only operation identity plus exact Failure-to-Test-Case references.
- Agent owns semantic grouping, worklist rewrites, active item, root cause, candidate selection, and finish timing.
- Worklist stores only `E*`, `TC*`, `P*`, parameter handles, and bounded semantic text.
- The reported missing E/TC display was a presentation overflow plus stale
  snapshot race, not missing schema data. A long unbroken parameter widened the
  360px sidebar and moved earlier references outside the visible region.
- E is an exact Failure-message identity scoped to one Resolution session. The
  observer can resolve it from the existing source registry without asking the
  Agent to duplicate Failure text or creating a persistence boundary.
- Harness-owned registries retain precise Test Cases, Patch candidates, current Generator/Constraint state, and other authoritative objects.
- Final worklist coverage requires every initial `(E*, TC*)` association at least once; duplication is allowed.
- Selected Patches remain staged until finalization and commit with final Attempts in one transaction.
- Resolution, Patch, and Review share one 1000-model-output limit; no other repetition or output stop remains.
- Repeated HTTP probes execute again, including mutating operations.

## Research Findings
- Current flow records all stable Failures before creating one isolated Solve Agent per todo.
- Current `FailureSolveSession` already owns a session-local `candidate_ref -> PatchCandidate` registry that can be deepened into the new Resolution module.
- Current Patch application atomically writes one applied Attempt, Generator/Constraint changes, and a change event, but stable Failure upsert happens earlier in a separate transaction.
- Current no-Patch and conflict Attempts also require an already-persisted `failure_id`; finalization therefore needs a deeper round-level persistence operation.
- Current one-Fingerprint Dedup bypass persists `suspected_input_node_ids=None`; the approved replacement removes this state and always uses a list.
- Existing focused Dedup/Solve/Coordinator/package-boundary baseline passed: 52 tests.
- The current repository has broad direct imports of the two old packages across workflow tests, tracing tests, model-role tests, package-boundary tests, and two separate evaluation suites; removal must be a coordinated replacement rather than a compatibility layer.
- `SmokeMemory.record_failures` and `record_solve_attempt` open separate transactions, while `SmokePatchApplication.apply` assumes the Failure already exists. A round-final commit therefore requires a new deep persistence operation that upserts decided Failures and writes every Attempt/Patch in one Unit of Work.
- The existing SQL repository owns stable Failure-key construction and operation-local input validation, so finalization should deepen that repository Interface instead of duplicating identity logic in the Agent.
- Current Solve exposes precise `PatchCandidate` objects only inside its session and already renders bounded candidate summaries; those helpers can move behind a `P*` registry read tool.
- `TestCaseCatalog` already provides immutable `TC*` lookup plus the exact valid Parameter-handle set needed for mechanical worklist reference validation; no new Test Case DTO projection is needed.
- `AgentToolbox` validates complete JSON Schema input before invoking a tool and converts expected `ToolFailure` values into safe results, so revision and forged-reference failures belong inside the worklist tool implementation.
- The reference-only worklist can remain independent of `PatchCandidate` by depending on a set of issued `P*` references; the candidate registry itself stays private to the future Agent session.
- Successful `AgentToolbox` implementations must return their model-facing DTO under `structured`, and the toolbox validates that value against each tool's declared output schema. Worklist tools can therefore expose their Pydantic JSON Schemas directly.
- Whole-list writes mutate shared session state and must never enter the toolbox's concurrent `execute_many` path; the Resolution Agent loop will reject a write grouped with any other call before execution.
- The OpenAPI capability already exports a bounded `list_inputs` tool, so parameter discovery can remain on demand instead of leaking parameter handles into the initial prompt.
- Parameter Patch and Review currently own separate output budgets and repeated-output fingerprints. Both must consume the shared Operation-level guard and let repeated calls continue.
- The old Solve session's candidate renderer is useful, but the precise candidate DTO and its output history must move behind a dedicated registry so `read_candidate` cannot return executable Patch content.
- Candidate recovery can expose root cause, affected semantic handles, change counts, and sample coverage without returning Patch updates or generated values; this is enough for the Agent to compare issued P refs after context clipping.
- Phoenix can evaluate the merged flow as one Resolution suite while still exercising the real nested Parameter Patch and Review Agents; a storage-free finalizer isolates Agent decisions from database and target-API effects.
- DeepSeek requires every tool in one request to share strictness. Because the
  current-operation HTTP Probe is intentionally non-strict, the model-facing
  Resolution tool projection must be uniformly non-strict while the local
  toolbox continues enforcing every exact schema before execution.
- Exact Failure messages are session-registry evidence and may exceed prompt
  budgets. Their authoritative representation must remain intact; only the
  `CompactTextWriter` prompt projection should clip them.
- A target HTTP Probe before `active_item_id` exists has no investigation owner
  and avoids per-item round feedback. Requiring an active item is a mechanical
  safety boundary, not a semantic judgment about whether the probe is useful.
- DeepSeek can intermittently return a thinking-mode tool call without the
  continuation-critical `reasoning_content`. Such a response can be retried
  safely only because normalization rejects it before any Agent tool executes;
  the field must never be synthesized.
- The final bounded GitLab run showed continuous real progress but could not
  finish five operations in ten minutes. This is runtime evidence about the
  scenario's duration, not evidence of a deadlock or permission to add a new
  semantic stopping rule.
- Resolution's initial prompt already contains the authoritative exact Failure
  messages, so registering a Test Case Failure-message lookup adds interface
  surface without adding evidence. An unclear HTTP message can instead follow
  the existing OpenAPI response-field list to one exact retained Test Case
  field value; no new discovery tool or Harness clarity rule is needed.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| External test seam is the new Resolution Interface | Old shallow Agent tests should be replaced, not layered beneath compatibility wrappers. |
| Worklist writes use optimistic revision | A whole-list write either replaces the expected version or leaves state unchanged. |
| Candidate detail is recovered through a read-only `P*` lookup tool | Context clipping never requires copying the precise Patch DTO into the worklist. |
| Finalization dereferences registries | Agent text and references are never treated as precise executable objects. |
| Replace the two old package facades in one coordinated migration | Direct compatibility imports would preserve the split Interface and defeat the requested module depth. |
| One `resolution` Phoenix suite owns merge, split, and Patch scenarios | Evaluation follows the production continuous-session boundary instead of preserving three retired orchestration stages. |
| Resolution registers four Test Case investigation tools, not Failure-message lookup | Initial E evidence already supplies messages; OpenAPI field discovery plus exact TC value lookup provides deeper evidence on demand. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
- `restscope/operation_smoke/coordinator.py`
- `restscope/operation_smoke/failure_dedup/`
- `restscope/operation_smoke/failure_solver/`
- `restscope/operation_smoke/memory/`
- `restscope/operation_smoke/parameter_patch/`
- `tests/test_failure_dedup_agent.py`
- `tests/test_failure_solver_agent.py`

## Visual/Browser Findings
- The existing repository has no frontend or web server; the observer is a new
  vertical slice rather than a restyle of an existing page.
- Phoenix already records CHAIN/AGENT/LLM/TOOL spans and structured model
  messages, but ordinary smoke Test Case spans contain only HTTP summaries.
  A dedicated run observer is required for complete request/response display.
- Successful smoke responses are already bounded to 1 MiB and failures/HTTP
  Probe responses to 10 MiB. The observer can reuse those bytes without adding
  a second target request or reading an unbounded stream.
- Worklist writes return the complete validated `FailureWorklist`; a successful
  `failure_resolution.write_worklist` tool result is the authoritative revision
  event and failed writes must not change the sidebar.
- `@ant-design/cli` 6.5.3 is installed globally for implementation-time API
  queries. The installed `ui-ux-pro-max` skill lacks its documented search
  script, so its written accessibility and visual rules are the fallback source.
- At 1440×900, both dark and light builds keep the event view and 360 px
  Worklist sidebar readable together. Long prompt JSON scrolls inside its own
  detail region instead of widening the page or hiding Worklist state.
- Treating an Agent `restscope.http.request` tool plus its child exchange as one
  compound entry preserves start-order semantics without rendering the same
  request twice. Standalone generated Test Case exchanges remain independent.
- Ant Design CLI caught six v6 prop renames that TypeScript accepts for
  compatibility. The final lint result is zero across deprecated,
  accessibility, usage, and performance categories.
- The final production page produced no browser warning/error while rendering
  long prompt, HTTP 422 JSON, and a three-item Worklist in both themes.
## Independent Run and App/UI lifecycle finding

- A normal Python `KeyboardInterrupt` already crosses synchronous provider
  calls because provider adapters catch `Exception`, not `BaseException`.
  Therefore a separate process or unsafe thread cancellation mechanism is not
  required for the approved local lifecycle.
- The reliable seam is `RESTScopeApp.run()`: classify `KeyboardInterrupt` as a
  stopped Run, publish one terminal observer update, then re-raise it. The
  caller retains control and can keep the App/UI alive, begin another Run, or
  explicitly close.
- `SIGKILL` or process termination cannot preserve process-local observer data.
  Runners that need a retained UI must catch the normal interrupt inside the
  App lifetime instead of terminating the process.

## Schema-v2 semantic timeline findings

- The existing observer already sees complete, redacted LLM input/output at the
  `LLMClient.invoke` seam while its active context knows the owning technical
  Agent name. The observer can therefore name Agent-turn cards correctly
  without changing Agent code or adding a registry.
- Full later prompts repeat prior history. A per-Agent multiset of messages can
  project every newly added tool result and harness feedback in order, while
  recording the current assistant output into the same turn before marking it
  seen for the next prompt.
- Generated Test Case execution spans already carry run ID, TC ID, and case
  index around the final prepared target request. Extending only the observer's
  private scope is sufficient to append exact request/response evidence to the
  owning Smoke Batch card.
- The HTTP Probe's prepared target request occurs inside the visible
  `restscope.http.request` tool span. Its request and response can be merged
  directly into that tool card; no child timeline event or transport change is
  required.
- Worklist projection is already triggered only after a successful
  `failure_resolution.write_worklist` tool closes. Removing its extra revision
  card does not change `worklist.replace` SSE or sidebar correctness.
- Parameter Patch and the current-operation HTTP Probe bypass `AgentToolbox`
  deliberately, so they had no ordinary Tool span. A UI-only Tool scope around
  those two executions supplies actual execution cards while leaving Phoenix
  unchanged; normal tools continue using their established spans.
- Generated successes previously retained a 1 MiB body only when the Behavior
  Monitor was active. Enabling the same already-approved 1 MiB read when the
  run observer is active supplies complete Smoke Batch success evidence without
  persisting it or changing the Test Case Catalog.
- Ant Design 6.5.3 supports the required small Table, `expandable` row renderer,
  function `rowKey`, pagination disablement, and horizontal scroll directly.
  Since each Batch is capped at 20 cases, nesting another virtualizer would add
  complexity without useful leverage; the session canvas lays out one Batch
  node and lets its complete table scroll inside the expanded detail region.
- Browser acceptance with the production build confirmed that a stopped Run
  remains available with seven semantic cards and an active SSE connection.
  The header reports zero business failures when the only unfinished card is a
  stopped warning; both theme variants preserve the fixed Worklist alongside
  long details at 1440x900.
- The 20-case expanded Batch preserved every row and exposed full Request and
  Response tabs. The acceptance data exercised HTTP 422 JSON with an explicit
  12 MiB reported size and retained-byte truncation note, a transport timeout,
  and HTTP 500 binary evidence rendered as Base64.
- The second Agent card showed exactly two incremental inputs—a Tool result and
  harness feedback—while its Output retained structured content and the exact
  `failure_resolution.write_worklist` Tool-call request. Raw HTML embedded in
  the first long prompt did not execute, and the browser recorded no warnings
  or errors.

## 2026-08-06 bounded GitLab schema-v2 run

- The schema-v2 observer remained responsive throughout a real 600-second
  DeepSeek/GitLab/Phoenix run and grew from 30 cards after one minute to 221
  cards immediately before interruption. No standalone phase, model, message,
  ordinary HTTP, or Worklist-revision cards appeared in sampled snapshots.
- GET Projects converged from a 1/10 first Batch to 10/10 on its next Batch.
  POST Projects completed two 0/10 Batches and was still in Resolution round 2
  at the deadline, so the remaining item GET/PUT/DELETE operations were not
  scheduled within ten minutes.
- The target state changed: three valid projects were created by authorized
  POST probes during Resolution. Generated Smoke POST cases themselves had no
  successful responses in the two observed Batches.
- A normal process-group SIGINT enforced the hard cutoff and the App process
  exited without SIGTERM escalation. Since observer state is process-local,
  backend shutdown deliberately removed the live semantic snapshot while
  Phoenix and approved database evidence remained inspectable.

## Agent-session canvas findings

- Schema-v2 already contains the stable session, parent-event, and Tool call
  identities needed for a session graph; the backend observer contract does
  not need another event type or compatibility layer.
- G6 React nodes can keep Ant Design message/detail content in one DOM tree.
  Expanding a detail changes only viewer state and node height, so the edge port
  can be recomputed without mutating or duplicating the semantic event.
- A fixed internal detail viewport keeps graph layout deterministic while the
  complete prompt, Tool result, HTTP exchange, or Batch table remains scrollable
  inside the expanded node. This also leaves the fixed Worklist rail visible.
- Browser acceptance confirmed Agent collapse must override the unfiltered
  all-events view; only an active search/filter may temporarily force a matching
  session open. Keeping those states separate also hides and restores the Tool
  subtree without changing the underlying semantic-event count.

## Fused single-message expansion findings

- The apparent duplicate message came from rendering the whole parent turn
  under one already-visible message summary. The viewer already stores each
  exact message record, so no backend or schema change is needed to render only
  the selected content.
- A second border, background, radius, and layout gap made inline detail read as
  a separate panel even though it was inside the graph node. Keeping one outer
  surface and one internal divider makes the height change read as the original
  node stretching.
- Unicode preview length must count code points rather than JavaScript UTF-16
  units; otherwise an emoji can be split into an invalid half-character at the
  160-character boundary.
