# Findings & Decisions

## Code navigation and API Behavior persistence consolidation (2026-08-12)

- The former `restscope.openapi_audit` package has only 164 production lines and
  no lifecycle independent of API Behavior Monitor. App currently constructs
  two Catalog/UoW chains over one nine-table database and exposes both as
  attributes, which makes one evidence lifecycle look like two peer domains.
- The approved replacement is one `APIBehaviorCatalog` and one SQLAlchemy UoW.
  Repository and transaction Protocols remain private at the consuming Catalog
  seam; no global `ports` package or compatibility aliases will be added.
- `request_generation.__init__` exports 63 names while production code outside
  that package consumes only four integration entries. Tests will import model,
  constraint, generation, and Patch details from their owning modules instead.
- `operation_references`, `target_http`, and `data_types` remain top-level
  Modules because each has several independent consumers. Agent ports and the
  shared Request Generation value-provider port also remain with their demand
  owners.
- The pre-existing `restscope/target_http/transport.py` edit is unrelated user
  work and must remain unmodified by this refactor.

## Generic evidence confidence (2026-08-11)

- The approved public seam is `restscope.evidence.Evidence[T]`; existing
  `AgentFinding.confidence`, API Behavior Monitor persistence, Tools, and the
  database remain unchanged.
- Every instance starts from the fixed Beta(1,1) prior. A supporting update
  increments alpha, an opposing update increments beta, and the only public
  estimate is `alpha / (alpha + beta)`.
- The wrapper retains any Python payload by identity without interpreting,
  copying, or serializing it. Confidence updates mutate the wrapper in place
  and must be atomic across threads.
- The established root-package contract permits only App composition and
  configuration files. The complete implementation therefore lives in
  `restscope/evidence/__init__.py` while preserving the approved
  `restscope.evidence.Evidence` import.
- The user explicitly requested implementation in the current local `main`
  checkout instead of a dedicated feature worktree and later authorized the
  scoped Evidence commit. Push and other external Git actions remain
  unauthorized.

## Final conversation and Todo decisions (2026-08-09)

- The conversation is not a general run timeline. It contains incremental LLM
  prompt prose, Provider Reasoning, ordinary model responses, and collapsed
  Tool/Subagent calls only. Smoke Batch and unrelated notifications stay in the
  complete schema-v2 snapshot but outside the document.
- System, Developer, User, and ordinary Assistant content is rendered without
  message-type annotations. Assistant Tool Call messages and Tool Result
  messages are removed from prose because the matching Tool event owns the
  exact input/output detail.
- Reasoning is expanded by default, muted, synthetic-oblique for Chinese text,
  complete, and toggled directly from its content. It has no title, bulb,
  chevron, duplicated copy block, or copy button. Ordinary Tool rows are
  collapsed by default; Subagent lifecycle events aggregate into one named
  child entry that opens the child conversation Drawer. All content shares one
  left edge.
- The page-level floating state is Todo. Only successful `plan.update` output
  owned by the explicit Main Agent can replace it. Failure Resolution's private
  Worklist remains ordinary Tool evidence and cannot overwrite Todo. Todo does
  not repeat its in-progress item in a separate “当前” line.
- The profile/Main Agent/linear-conversation heading was removed and the
  conversation surface uses the full page content width.
- Browser-control policy blocked automatic reload of the loopback fixture at
  `127.0.0.1:4174`, but the user refreshed it and the built asset then
  passed desktop browser inspection. The conversation, Reasoning, Tool, Todo,
  and Subagent surfaces have no horizontal overflow and share the intended
  alignment. The browser capability rescaled a requested 375 px viewport to an
  effective 169 px viewport, so an exact 375 px visual confirmation remains
  outstanding even though the more constrained view had no horizontal
  overflow.

## Codex-style conversation observer (2026-08-09)

> The requirements below record the initial approved plan. The later user
> decisions in “Final conversation and Todo decisions” above supersede its
> default-collapsed Reasoning, Subagent Drawer, and floating Worklist details.

### Approved requirements
- Remove the G6 canvas entirely and render only the explicit generic Main Agent as a linear conversation.
- Show Provider-returned raw Reasoning as an independent, default-collapsed item. Browser IndexedDB may retain this already-redacted human-observability evidence; Phoenix and Agent Context must not gain it.
- Show Subagents as stable activity items and open their independent conversations in an accessible right Drawer.
- Move the latest Worklist into a floating progress entry and right Drawer rather than the conversation or a fixed sidebar.
- Keep schema-v2's complete-event snapshot/SSE reducer and add only the identity, task, phase, and Reasoning facts needed by the new renderer.
- Delete existing canvas-era IndexedDB history during the storage-version upgrade and retain at most five new conversation snapshots.

### Executable evidence
- Generic `Agent.run` already traces stable `session_id`, `profile`, `depth`, `lifecycle`, and optional `parent_session_id`, but the current observer recognizes only legacy `kind="AGENT"` scopes.
- DeepSeek currently preserves `reasoning_content` only inside ToolCall `provider_context`; final-response Reasoning is discarded by normalization and LLM tracing deliberately removes Provider context.
- The current browser reducer already receives full authoritative `timeline.upsert` replacements keyed by `event_id` and ordered by the observer cursor, so a second delta protocol is unnecessary.
- IndexedDB currently uses database/record version 1 and stores complete schema-v2 snapshots in the `runs` store.
- The existing Main App does not yet launch the generic Main Agent. The approved UI must show an explicit empty state rather than reinterpret legacy Agents.
- The existing Observer-only detail outlet can carry complete redacted Reasoning without adding it to Phoenix span attributes. Generic `Agent.run` output arrives after final schema validation, so it is the correct authority for promoting only the last successful task turn to `final_answer`.

### UI constraints
- Use Ant Design semantic tokens, visible keyboard focus, 44px interaction targets, reduced-motion behavior, and no color-only status.
- Use `@tanstack/react-virtual` for dynamic-height conversation virtualization and remove both G6 dependencies.
- The project remains locked to Ant Design v6.5.3. Its Drawer supports `focusable={{ trap, focusTriggerAfterClose }}`, FloatButton supports content plus Badge state, and Collapse uses the current `items` API.
- The implementation-time Ant Design CLI was updated from 6.5.3 to 6.5.4 after its own update notice; this does not change the project's locked runtime dependency.

## Profile Agent Prompt Session (2026-08-08)

### Approved requirements
- Add a private `AgentPromptSession` for generic Agents created from `AgentProfile`; do not migrate Failure Resolution, Patch, Review, or Compact Agents.
- Add an optional bounded Profile description and require it for every directly referenced child Profile.
- Preserve stable `system` and optional `developer` instructions through compaction; OpenAI-compatible providers keep `developer`, while DeepSeek folds it into `system` in order.
- Auto-append the Harness-owned `skill.read` Tool for Profiles selecting Skills. A successful read keeps the legal assistant/tool exchange and then appends bounded Skill instructions as a user message.
- Send Context Sources fully on first use, changed-only thereafter, explicitly represent empty changes, and re-anchor all current sources after compaction.
- Reserve Tool and output-schema serialization size, protect the Harness contract and all Skill/child names, and return `context_budget_exceeded` before any model or Tool action when essentials cannot fit.

### Pre-refactor executable baseline
- `restscope.agent.runtime.Agent` rendered system/task text and constructed both normal and compaction requests itself.
- `AgentContext` had only stable system plus user/history state, and its fitting algorithm could clip system content.
- Harness Profile resolution already validated Skill-required Tools and Context Sources before building each Agent.
- `AgentToolbox` already preserved Tool definition order and locally validated arguments and successful results.
- DeepSeek inherited the OpenAI-compatible message conversion without special handling for a developer role.

### Current executable evidence
- The private `AgentPromptSession` owns fixed model settings, role assembly, incremental Context, Skill instruction injection, Tool/output protocol reservation, and compaction requests.
- `Agent` owns only the model/Tool execution loop and receives no duplicate model configuration.
- Harness Context readers own source type checking, redaction, and length validation; Prompt Session consumes their bounded Markdown results.
- `AgentContext` preserves stable system and optional developer messages across ordinary projection and compaction.
- OpenAI-compatible requests preserve `developer`, while DeepSeek folds it into `system` in original order.

### Design decision
`AgentPromptSession` is a deep private Module whose callers ask it to prepare tasks, build requests, record protocol groups and feedback, and replace compacted history. It reuses `AgentContext` rather than copying its history projection algorithm. Tests target the public Harness, Context, and provider seams instead of importing the private class.

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
# 2026-08-12: Target API request foundation navigation

- `target_http` is correctly a top-level shared Module: the HTTP Tool and Batch
  execution both consume it independently, while Harness only assembles or
  invokes those consumers.
- Its package facade exports 15 names, including low-level URL/header helpers
  and `ClientFactory`; production consumers instead need one Client, one pure
  prepare function, stable errors, and response-processing records.
- `normalize_media_type()` and `is_json_media_type()` have 14 cross-domain
  production consumers, so `request.py` is a misleading owner. A focused
  `media_type.py` gives them one discoverable implementation.
- Batch currently reads `has_response_processor` and `run_observer` to decide
  body buffering. This leaks Client configuration. The new Client must produce
  independent Monitor, Observer, and caller projections internally.
- The existing uncommitted `transport.py` change only makes `prepare()` static;
  the approved pure `prepare_target_request()` function subsumes that intent.

# 2026-08-12: Harness Runtime navigation

- `HarnessRuntime` still exists as the public concrete class returned by both
  Harness builders; `_AppHarnessRuntime` is a second, App-private description
  of the same production object.
- The private Protocol promises six required capabilities, while App cleanup
  and context access use `getattr` as though those capabilities were optional.
  Tests exploit that mismatch with a partial `SimpleNamespace`.
- There is only one real Harness implementation. Tests can obtain the same
  concrete Module through `build_harness()` and vary Agent behavior through
  `AgentRuntimeDefinition`, so the extra Protocol does not buy a real seam.
- `target_http_tool` is the one Harness field left with the retired transport
  vocabulary; `http_request_tool` describes its current Tool ownership.

# 2026-08-12: Redundant Protocol and Reference integration

- `_ReferenceBindingStager` has one production implementation and duplicates
  the same `BehaviorMonitorReferenceValues` object already injected for reads.
  Its context-manager return annotation also differs from the decorated
  implementation and triggers an IDE structural-typing warning.
- Parameter Patch additionally uses `hasattr(resolve_response_source)`, so its
  current collaborator is neither a complete Interface nor a consistently
  narrow read port. A concrete `BehaviorMonitorReferences` integration removes
  both the split injection and the capability probe.
- The Coordinator declares `ResourceResponseTracker(Protocol)` beside a
  concrete class with the identical name and only one production Adapter.
- App `_UIServiceHost` likewise describes the sole concrete `UIService`; tests
  can return a real instance while intercepting only its close call.
- Database Repository/UoW, Agent ports, Batch/OpenAPI/Target Client seams,
  `SystemAgentRunner`, `ReferenceValueProvider`, Traversable, and ASGI/Uvicorn
  Protocols have distinct adapters or real cross-Module/third-party isolation
  value and must remain.
# 2026-08-12: Python 3.12 baseline

- The only active Python-version declarations are `.python-version`, the
  `requires-python` field in `pyproject.toml`, and the generated `uv.lock`
  requirement. The repository currently has no Python CI matrix or Python
  container image.
- Python 3.12.12 is already available through the local `uv` installation, so
  the upgraded baseline can be tested with the real target interpreter.
- Historical implementation plans mention Python 3.11 as their period-specific
  technology stack. They are not active runtime configuration and should not be
  rewritten.
- Regenerating the lock at `>=3.12` kept every package name and version stable.
  Its larger-looking diff removes wheels and resolution branches that exist only
  for the retired Python 3.11 baseline.
- The built wheel declares `Requires-Python: >=3.12`, so package installers will
  enforce the same minimum as local development.
# 2026-08-12: RESTScopeApp runtime/composition navigation

- `RESTScopeApp` has a small useful public Interface, but the 696-line
  `restscope/app.py` combines target initialization, production object graph,
  Agent Profile declarations, UI/tracing lifecycle, database rollback, Main
  execution, and audit delegation.
- The Harness, Generation Store, Patch Runtime, Monitor Coordinator, Target API
  Client, and Catalog attributes are read only by tests. Production callers do
  not need them on the App Interface.
- Existing public behavior can cover those tests: `app.tool_context`, caller-
  retained injected Harnesses, database/audit results, and the real response
  processing seam.
- Profile composition remains App-specific and should move to private App
  composition rather than into the reusable Harness package.
- `_AppResources` is enough to hide the default-versus-injected Harness split;
  no second public runtime, Builder, Protocol, or generic container is needed.
- A clean packaging check exposed stale ignored setuptools output that still
  contained retired modules. After moving those exact generated directories,
  the wheel contained only the three new App package files.
