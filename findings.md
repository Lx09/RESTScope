# Findings & Decisions

## Requirements
- Replace Failure Dedup and Failure Solve with one continuous Failure Resolution Agent.
- Initial prompt contains only operation identity plus exact Failure-to-Test-Case references.
- Agent owns semantic grouping, worklist rewrites, active item, root cause, candidate selection, and finish timing.
- Worklist stores only `E*`, `TC*`, `P*`, parameter handles, and bounded semantic text.
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
- None; implementation uses local repository evidence only.
