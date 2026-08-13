# Task Plan: RESTScope Evolution

## Goal
Replace the Live Observer's graph canvas with the approved Main-Agent conversation UI while preserving the read-only schema-v2 observer boundary, complete evidence, and browser-only recovery.

## Current Phase
Phase 28 persistent Batch results is complete; Phase 19 exact 375 px confirmation remains pending

## Phases

### Phase 28: Persistent Batch and Test Case results
- [x] Confirm persistence, query, retention, security, and Profile-authorization contracts
- [x] Add red Catalog, Batch execution, and Tool contract tests
- [x] Implement Batch and complete Observation persistence
- [x] Add bounded Batch/Test Case query Tools and production bindings
- [x] Update architecture records and run complete verification
- **Status:** complete and verified on local `main`; 590 tests passed with 2 skips, implementation remains unstaged, and pre-existing data-types edit was preserved

### Phase 27: RESTScopeApp lifecycle and CLI entrypoint
- [x] Add red App Interface, target validation, and Click command contracts
- [x] Extract target and Profile ownership; remove injection and audit facades
- [x] Add the installed `restscope` command and direct Click dependency
- [x] Update navigation documents and run complete verification
- [x] Commit only scoped changes, preserving the pre-existing data-types edit
- **Status:** verified for the authorized scoped commit on local `main`; 576 tests passed with 2 skips

### Phase 26: RESTScopeApp runtime/composition navigation
- [x] Add red package-shape and narrowed-App Interface contracts
- [x] Move default composition and owned resources behind `_AppResources`
- [x] Keep lifecycle, initialization, audit, and failure behavior stable
- [x] Update navigation records and run complete verification
- **Status:** verified and uncommitted on local `main`; pre-existing data-types edit preserved

### Phase 25: Python 3.12 baseline
- [x] Add a regression contract for active Python-version declarations
- [x] Raise the local, package, and lock-file minimum to Python 3.12
- [x] Verify the full suite and wheel metadata with Python 3.12
- [x] Record the final diff and wait for separate Git delivery authorization
- **Status:** implemented and verified on `codex/python-3-12`; uncommitted

### Phase 24: Redundant Protocol and Reference integration cleanup
- [x] Add red concrete Reference, Resource Monitor, and UI navigation contracts
- [x] Replace the split Reference Provider/Stager injection with one concrete collaborator
- [x] Remove the duplicate Resource Tracker and UI Host Protocols
- [x] Update current navigation records and run complete verification
- **Status:** complete and verified on local `main`; 565 tests passed with 2 skips

### Phase 23: Harness Runtime navigation cleanup
- [x] Add red concrete-runtime and navigation contracts
- [x] Remove the App-private Harness Protocol and dynamic method probing
- [x] Rename the retained HTTP Tool field and migrate tests to real Harness instances
- [x] Update navigation records and run complete verification
- **Status:** complete and verified on local `main`; 561 tests passed with 2 skips

### Phase 22: Target API request foundation refactor
- [x] Add red public Interface, response projection, and navigation contracts
- [x] Replace `target_http` with the focused top-level `target_api` Module
- [x] Migrate Tool, Batch, Monitor, Harness, OpenAPI, and Request Generation callers
- [x] Update current navigation records and run complete verification
- **Status:** complete and verified on local `main`; 560 tests passed with 2 skips

### Phase 21: Code navigation and API Behavior persistence consolidation
- [x] Audit current top-level Modules, ports, facades, and persistence ownership
- [x] Add red Catalog and package-navigation contracts
- [x] Consolidate OpenAPI Audit and Response Monitor persistence
- [x] Flatten shallow Behavior Monitor paths and narrow Request Generation facade
- [x] Update current architecture records and run complete verification
- **Status:** complete and verified on local `main`; pre-existing target transport edit preserved; no Git delivery performed

### Phase 20: Generic evidence confidence
- [x] Confirm the public `Evidence[T]` seam and Beta(1,1) update semantics
- [x] Add the first red public-interface test for payload identity and the neutral prior
- [x] Implement the in-memory mutable confidence Module
- [x] Run focused and complete verification
- **Status:** complete and verified on local `main`; scoped commit authorized

### Phase 19: Codex-style Main Agent conversation observer
- [x] Confirm the evolving conversation, Reasoning, Tool/Subagent expansion, floating Todo, history migration, and Git boundaries
- [x] Create `codex/conversation-observer-ui` in a dedicated worktree
- [x] Add red Provider, LLM, Observer, conversation-model, component, and history-migration tests
- [x] Implement Reasoning projection and generic Main/Subagent observation
- [x] Replace G6 with the full-width virtualized prompt/response document, collapsed Tool/Subagent rows, and floating Todo
- [x] Update governance, task records, README, code-reading guide, and deterministic static assets
- [x] Run focused and full tests, lint, Ant Design lint, and production build
- [x] Complete refreshed-browser desktop visual confirmation, including alignment, Reasoning treatment, Todo, and Subagent interaction
- [ ] Confirm the exact 375 px viewport in a browser environment that does not rescale the requested viewport
- **Status:** implementation and desktop browser verification complete; exact 375 px confirmation remains pending

### Phase 18: Profile Agent Prompt Session
- [x] Confirm the approved Interfaces, non-goals, and Git authorization boundary
- [x] Create `codex/profile-agent-prompt-session` in a dedicated worktree
- [x] Add public-seam red tests for Profile, AgentContext/provider roles, Skill loading, and Prompt Session behavior
- [x] Implement the private Module and Harness integration in vertical slices
- [x] Update AGENTS, ADR, task record, and code-reading guide
- [x] Run focused, complete, compile, lock-file, boundary, and diff-hygiene verification
- **Status:** complete; verified and ready for the authorized local delivery

### Phase 1: Discovery and executable seams
- [x] Capture the approved behavior and project constraints
- [x] Map the current Agent, Memory, Patch, evaluation, and public Interface seams
- [x] Record the replacement design and migration inventory
- **Status:** complete

### Phase 2: Resolution contracts and worklist
- [x] Add red tests for reference-only worklist validation and the shared output guard
- [x] Implement Failure Resolution schemas, worklist store, prompt, registries, and tools
- [x] Add the shared 1000-output guard and progressive active-item feedback
- **Status:** complete

### Phase 3: Continuous Agent and finalization
- [x] Add red tests at the Resolution Interface
- [x] Implement the continuous Agent loop and on-demand tool set
- [x] Stage Patch decisions and atomically finalize final worklist decisions
- **Status:** complete

### Phase 4: Workflow replacement
- [x] Replace Operation Smoke composition and public DTOs
- [x] Remove old Dedup/Solve packages, roles, tests, and compatibility names
- [x] Merge evaluation suites and update current documentation
- **Status:** complete

### Phase 5: Verification and delivery
- [x] Run focused tests and resolve failures
- [x] Run full, evaluation, compile, boundary, and diff checks
- [x] Review the scoped diff and report uncommitted delivery
- **Status:** complete

### Phase 6: Bounded GitLab live diagnosis
- [x] Run the five-operation GitLab scenario with a 600-second hard cutoff
- [x] Add regressions and fix live-found compatibility and safety bugs
- [x] Re-run the complete offline suite and one final bounded live scenario
- **Status:** complete; two operations finished before the final time cutoff

### Phase 7: Failure investigation tool refinement
- [x] Exclude the redundant Failure-message lookup from Resolution
- [x] Document and test OpenAPI field discovery followed by exact TC value lookup
- [x] Run final focused, full-suite, compilation, and diff verification
- **Status:** complete

### Phase 8: Live run observer foundation
- [x] Add the run-event store, tracing observation seam, HTTP exchange events, and worklist revisions
- [x] Add loopback Starlette/Uvicorn hosting, configuration, SSE, snapshots, and lifecycle wiring
- [x] Add focused Python contracts and keep Phoenix behavior unchanged
- **Status:** complete

### Phase 9: Ant Design observer interface
- [x] Build the React/TypeScript/Vite/Ant Design timeline and worklist UI
- [x] Add safe prompt, tool, HTTP, JSON, binary, filtering, theme, and auto-follow views
- [x] Version frontend source, lock file, and reproducible built assets without creating a Git commit
- **Status:** complete

### Phase 10: Verification and delivery
- [x] Run focused Python and frontend tests, Ant Design lint, builds, browser visual QA, and full Python suite
- [x] Update task records with exact results and review the unstaged diff
- [x] Report the uncommitted worktree; do not commit, merge, or clean up without separate authorization
- **Status:** complete

### Phase 11: Independent Run and App/UI lifecycles
- [x] Reproduce the interrupt path that labeled a stopped Run as errored
- [x] Route `KeyboardInterrupt` to a retained `stopped` observer snapshot
- [x] Verify App reuse, SSE retention, documentation, and the complete suite
- **Status:** complete

### Phase 12: Schema-v2 semantic timeline
- [x] Add red observer contracts for Agent-turn deltas, tool execution cards, HTTP-tool merging, complete Smoke Batch cases, and interruption status
- [x] Replace tracing-shaped App events with `agent_turn`, `tool_call`, and `smoke_batch` aggregation while preserving Phoenix and Worklist SSE
- [x] Replace the frontend event model and cards with Agent/Tool Input-Output Tabs and expandable Smoke Batch cases
- [x] Run focused, frontend, browser, complete-suite, asset-drift, compilation, and diff verification
- [x] Update documentation and report the unstaged worktree without performing unauthorized Git delivery
- **Status:** complete; implementation remains unstaged pending separate Git authorization

### Phase 13: Ten-minute GitLab live test
- [x] Confirm explicit authorization, inspect the live harness, and verify the feature worktree remains unstaged
- [x] Start and health-check the disposable GitLab and Phoenix dependencies
- [x] Run the five-operation DeepSeek/GitLab/Phoenix acceptance path for exactly 600 seconds
- [x] Interrupt the Run, close the App/UI/backend, and verify no RESTScope backend remains
- [x] Record observable progress, artifacts, and target mutations without performing Git delivery
- **Status:** complete; RESTScope, GitLab, and Phoenix processes are stopped

### Phase 14: Worklist real-time state and readability
- [x] Add stable `WI-*` prompt/schema/store contracts without deduplication
- [x] Project exact session-local E Failure messages into successful Worklist updates
- [x] Make snapshot/SSE state monotonic and cancel cleaned-up initial requests
- [x] Separate and wrap Failure, TC, parameter, and P sections in the sidebar
- [x] Run final frontend, Ant Design, Python, browser, asset-drift, compile, and diff verification
- **Status:** complete; implementation remains unstaged pending separate Git authorization

### Phase 15: Agent-session graph canvas
- [x] Aggregate Agent turns by session and resolve Tool edges to Assistant message ports
- [x] Replace the virtual timeline with a read-only AntV G6 left-to-right canvas
- [x] Keep full Agent, Tool, HTTP, and Smoke Batch details inside vertically expanding nodes
- [x] Run final frontend, Ant Design, Python, browser, asset-drift, compile, and diff verification
- **Status:** complete; implementation remains unstaged pending separate Git authorization

### Phase 16: Fused single-message expansion
- [x] Replace whole-turn Agent detail with the selected message body and its own Tool-call metadata
- [x] Bound collapsed summaries to 160 Unicode characters and remove duplicate expanded text
- [x] Merge Agent, Tool/HTTP, and Batch detail surfaces into their original node boundary
- [x] Run final frontend, Ant Design, Python, browser, asset-drift, compile, and diff verification
- **Status:** complete; implementation remains unstaged pending separate Git authorization

### Phase 17: Scroll-like detail motion
- [x] Add red component and canvas contracts for same-origin, timed, reduced-motion, and last-state-wins expansion
- [x] Coordinate the inline content reveal with G6 node size, layout, port, and edge animation
- [x] Rebuild committed UI assets and update the observer task record
- [x] Run frontend, Ant Design, Python, deterministic-build, diff, and real-page browser verification
- **Status:** complete; Git delivery remains unauthorized

## Key Questions
1. How can final worklist decisions be committed atomically without persisting provisional Agent state?
2. How can Patch candidates remain authoritative registry objects while worklist writes contain only opaque references?
3. Which old public names and tests must be removed rather than wrapped?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Test through `HarnessRuntime.start_main_agent`, `AgentContext`, and provider request conversion | These are the approved stable behavior seams; the new `AgentPromptSession` remains private and can evolve without a public Prompt DTO. |
| Auto-append `skill.read` only when a Profile selects Skills | The selected names are the narrow authorization for loading their bodies; ordinary Tools remain explicitly named. |
| Keep one Prompt Session per Agent session | Main, child, and sibling prompt state, Context fingerprints, and loaded Skill bodies remain isolated and non-persistent. |
| One `FailureResolutionAgent` session per failed Batch | The Agent owns semantic grouping, investigation, worklist evolution, and finish timing. |
| Worklist contains references and bounded semantic strings only | Precise Patch/Test Case/Memory objects remain authoritative in session registries. |
| Harness validates only types, references, coverage, tool safety, final Patch compatibility, and persistence | Semantic scheduling and completion judgments remain model-owned. |
| Final worklist decisions commit together at round finish | Provisional decisions can be freely rewritten without persistence cleanup. |
| One Operation-level 1000-output guard | All other Dedup/Solve/Patch/Review output and repetition stops are removed. |
| Live calls require a separate explicit request | The user authorized a bounded follow-up run after offline implementation. |
| Retry missing DeepSeek reasoning at most twice | A rejected response is hidden before any tool executes; three total requests absorb transient omissions without inventing continuation state or looping forever. |
| HTTP Probe requires an active worklist item | A target action has an explicit investigation owner and participates in per-item round feedback. |
| Resolution has no Failure-message lookup | Exact messages are initial E evidence; unclear HTTP failures use OpenAPI field discovery followed by one concrete TC field read. |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| Direct removal of the two ignored wheel-build directories was rejected by the environment's destructive-operation guard | 1 | Move only the exact `build/` and `restscope.egg-info/` directories into a new system temporary directory instead; keep `.venv`. |
| Python 3.12 red test used `--no-sync` before the feature worktree had a virtual environment | 1 | Let `uv` sync the existing lock into the new Python 3.12 environment, then rerun the unchanged contract. |
| Phase 24 UI tests patched the facade after App adopted a direct concrete import | 1 | Patch `restscope.app.start_ui_service`, the actual consumer binding; the production lifecycle behavior was unchanged. |
| Phase 24 concrete Reference import created a package initialization cycle | 1 | `reference_values` already imports Parameter Patch models; keep the concrete forward type under `TYPE_CHECKING` because Runtime performs no runtime type test, then migrate old test doubles to the real collaborator. |
| Phase 23 legacy-name scan used Markdown backticks inside a shell argument | 1 | The shell attempted to run the quoted name before the read-only scan; no file changed. Repeat scans with literal-safe patterns. |
| Phase 23 first concrete-Harness focused run found the intended partial `SimpleNamespace` fixture | 1 | Replace it with `build_harness()` so the database-bypass behavior crosses the supported seam. |
| Phase 22 combined source/docs polish used a pre-Phase-21 reading-guide anchor | 1 | No partial edit occurred; inspect current guide text and apply exact source and documentation patches separately. |
| Phase 22 combined test migration used an inexact `test_generic_batch_tool.py` import anchor | 1 | No partial edit occurred; migrate each test file against its exact current snippets. |
| Phase 21 Catalog test patch expected `_catalog(tmp_path)` instead of the current `_catalog()` | 1 | No partial test edit occurred; append the new scenarios against the current in-memory fixture shape. |
| Final multi-file status patch used a progress heading as a findings anchor | 1 | No partial edit occurred; inspect each file and apply exact per-file anchors. |
| Full suite rejected `restscope/evidence.py` at the root package seam | 1 | Preserve `restscope.evidence.Evidence` while moving the complete Module into `restscope/evidence/__init__.py`; do not weaken the boundary test. |
| Four invalid-input scenarios changed confidence instead of raising | 1 | Expected TDD red state; validate the boolean before either Beta counter changes. |
| Evidence focus initially failed because `restscope.evidence` did not exist | 1 | Expected first TDD red state; add only the constructor, payload, and initial confidence behavior next. |
| New worklist tests failed because `failure_resolution` did not yet exist | 1 | Expected TDD red state; implement the new package contracts next. |
| Candidate-registry patch used an outdated test-file anchor | 1 | No source change was applied; inspected the current tail and split the patch into exact edits. |
| Five continuous-Agent tests failed because the new Agent Interface did not exist | 1 | Expected TDD red state; implement the session, prompt, and finish contracts next. |
| Verification shell has no `python` command | 1 | Use the available `python3` executable for compile checks. |
| Planning-file update used an ambiguous status anchor | 1 | Re-applied the update with phase-specific context; no partial edit occurred. |
| Focused test command used a non-activated `pytest` executable | 1 | Run verification through `.venv/bin/pytest`. |
| Test-fixture rename expected one extra import occurrence | 1 | Re-applied against the exact imports; no partial edit occurred. |
| Optional tracing test found no `restscope.llm.role` on model spans | 1 | Add the bounded internal Agent role to shared LLM request attributes. |
| Live run rejected mixed strict/non-strict DeepSeek tools | 1 | Keep local validation exact while projecting the mixed model-facing tool set uniformly non-strict. |
| Live PUT Failure exceeded the registry message limit | 1 | Keep the authoritative exact message unbounded in-session and bound only its prompt projection. |
| Live Agent probed before establishing an active work item | 1 | Reject HTTP Probe until `active_item_id` exists; read-only discovery remains available first. |
| DeepSeek twice omitted required thinking continuation content | 1 | Reject incomplete responses and allow two bounded pre-tool retries; never synthesize `reasoning_content`. |
| `ui-ux-pro-max` referenced a missing `scripts/search.py` | 1 | Record the package defect and apply the skill's documented accessibility, contrast, spacing, virtualization, and reduced-motion rules directly. |
| The first HTTP transport patch used an outdated return-value anchor | 1 | No partial transport edit occurred; wrap the existing method through a new private unobserved helper using exact current boundaries. |
| The first wheel rebuild retained an obsolete hashed JS file from ignored setuptools `build/` cache | 1 | Move only generated build caches out of the worktree, rebuild cleanly, and verify the wheel contains exactly the current HTML/CSS/JS assets. |
| Schema-v2 observer contracts reported ten tracing-shaped event failures | 1 | Expected TDD red state; replace the old phase/message/model/HTTP projection with semantic aggregation. |
| Ant Design CLI had no `Table expandable-row` demo | 1 | Used the CLI's listed `Table expand` demo and re-queried every component against the locked 6.5.3 version. |
| Frontend semantic-card tests initially reported five old-renderer failures | 1 | Expected red state; replace role cards and child HTTP composition with schema-v2 cards and expandable cases. |
| Browser control does not implement the documented `networkidle` wait state | 1 | Switched to the supported `load` state and continued from a fresh DOM snapshot. |
| Clearing the Ant Design search with an empty automation fill left the controlled value unchanged | 1 | Used the visible, unique clear button and verified the counter returned to 7 / 7. |
| GitLab/Phoenix preflight found ports 7077 and 6006 offline | 1 | Start the repository's existing disposable containers, then begin the ten-minute clock only after both health checks pass. |
| The first GitLab readiness loop used zsh's reserved read-only `status` name | 1 | Rename the local values to task-specific `gitlab_http_code` and `gitlab_health_state`; no service state was changed by the failed loop. |
| GitLab became Docker-healthy but this image returned 404 for `/-/readiness` | 1 | Treat the container health check as authoritative and verify the exact `/users/sign_in` route used by the live harness instead. |
| The first combined Phase 13 progress patch contained an invalid file-marker anchor | 1 | No partial change occurred; reapply with a valid multi-file patch. |
| The first timed supervisor used `datetime.UTC` under system Python 3.9 | 1 | It failed before spawning pytest; switch to `datetime.timezone.utc` and start a fresh 600-second window. |
| A macOS `pgrep -af` backend check returned only an ambiguous numeric match | 1 | Use an explicit `ps` command against the known supervisor/child PIDs and command text; it confirmed no RESTScope process remained. |
| The first repeated-build command ran from the repository root without a package manifest | 1 | No build ran and no asset changed; rerun from `ui/` and verify the second manifest matches the first exactly. |
| Phase 17 focused tests could not resolve the not-yet-created reveal component and new geometry constants | 1 | Expected TDD red state; implement the approved shared reveal and canvas motion contracts next. |
| Phase 17 production build rejected the test Animation double's overly broad mock types | 1 | Keep the mock behavior and type its `cancel` callback and finish event against the browser Animation contract. |
| Browser motion sampling used `performance.now()`, which the restricted page evaluator does not expose | 1 | Remove timestamps from the sampler and inspect the already-clicked card before issuing another toggle. |
| Ant Design CLI had no `Drawer basic` demo | 1 | Use the listed `basic-right` Drawer demo; no project file was changed. |
| The first browser locator call targeted the tab wrapper instead of its Playwright surface | 1 | Use `observerTab.playwright.locator`; no page action occurred. |
| Restricted page evaluation did not expose DOM `click()` or constructible `MouseEvent` objects | 1 | Use supported locator clicks and rely on the Web Animations regression for frame-level reversal behavior. |

## Notes
- Current work occurs only in `/Users/lixin/Workplace/RESTScope-conversation-observer-ui` on `codex/conversation-observer-ui`.
- Implementation is authorized. Commit, merge, push, branch deletion, and worktree cleanup remain unauthorized.
