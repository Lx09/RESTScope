# Findings: Agent Tool Runtime Simplification

## 2026-08-04 redundant-tool audit

- `submit_parameter_patch_review` is an output envelope, not a domain tool. It
  accepts a boolean plus issues, while deterministic code already derives that
  boolean from the issues and an equivalent JSON Schema response path exists.
- Failure Dedup receives the operation's complete semantic handles from its
  run-local Catalog before asking the model to call `openapi.list_inputs` for
  another copy.
- Failure Solve already receives all current semantic handles and exposes
  exact OpenAPI schema lookups. Its broad input-list call duplicates authority
  and produced a large, incomplete first page in retained live evidence.
- The initial audit did not classify `submit_parameter_patch_proposal` as
  redundant because its strict tool carried a recursive Patch and Constraint
  contract to the provider; retained trace evidence was required first.
- Final Proposal evidence changed that provisional classification. Across the
  retained local trace artifacts, strict Proposal transport produced 101 valid
  and 119 invalid DTO outputs; the JSON fallback produced 88 valid and 15
  invalid outputs. Because compiler, sampling, Reviewer, and local DTO
  validation remain downstream, the strict submission wrapper added failure
  and fallback state without owning a durable domain boundary.
- The global `openapi.list_inputs` Capability remains intentionally available
  at the user's direction. Redundancy existed only at the two current Agent
  call sites, not necessarily for every future or external consumer.

## Current Evidence

- The default capability runtime registers HTTP and OpenAPI tools, optionally
  Resource Lookup and MCP tools, in one App-wide Registry.
- Production Agents do not use `ToolSelector`; Dedup and Solve manually choose
  specs and dispatch private tools.
- `ToolPolicy.state` is unused, and the existing policy allows the high-risk
  raw HTTP tool for every role.
- `ToolCallValidator` checks registration and policy but does not validate the
  advertised input schema. `ToolExecutor` does not validate output schemas.
- Tool registration silently replaces specs and can retain an old handler.
- Local handlers receive the complete App ToolContext even when they do not
  need IR, target address, or authentication headers.
- Solve already scopes the shared HTTP implementation to the current operation
  and projects raw responses through the run-local Test Case Catalog.
- Dedup manually restricts a broad OpenAPI lookup to the current operation.
- Dedup currently accepts one tool call; Solve accepts grouped read-only calls
  but executes them sequentially and its Parameter Memory handler mutates
  session bookkeeping during execution.
- The default App registers Resource Lookup, but no production Agent exposes it
  to a model.

## Implementation Constraints

- Preserve the user's unrelated GitLab OpenAPI and live-test changes in the
  main worktree.
- Production modules, public Interfaces, and non-trivial helpers require
  beginner-readable docstrings and intent comments.
- Historical task records remain historical evidence; update current docs
  rather than rewriting old completed records.
- No live network or model verification is authorized.

## Phase 1 discovery

- Runtime validation must consume arbitrary JSON Schema because MCP input
  contracts are not necessarily generated from RESTScope Pydantic models.
- `jsonschema` 4.26.0 is already present in `uv.lock` as a transitive
  dependency, but RESTScope does not declare it directly. Importing it from
  production code therefore requires an explicit direct dependency decision.
- `TracingRuntime` already exposes the App-owned `Redactor`, and `TraceSpan`
  can record a redacted unexpected exception event without returning that text
  to the model. Tool-specific trace inputs still need preservation because the
  scoped HTTP Probe deliberately traces operation identity rather than raw
  model request values.
- Dedup's current constructor receives the entire global `ToolExecutor`; its
  test factory must build and bind a complete capability runtime only to expose
  OpenAPI lookup. The approved scoped design can instead bind the current IR
  operation and run-local Catalog when `deduplicate` starts.
- Dedup's existing correction helper rejects any response containing more than
  one tool call. The Agent loop already appends one provider-required tool
  result per call, so the public behavior can change vertically without a
  generic reasoning-loop abstraction.
- App composition currently builds Operation Smoke before `initialize` binds
  the target OpenAPI IR. The final scoped OpenAPI tool therefore needs an
  explicit late-bound operation provider or a narrower construction-time
  change; it cannot simply capture an `OperationIR` in the current factory.
- The Catalog tool already has a strict Pydantic input schema and a bounded
  structured result, but it currently performs its own tracing, validation,
  and `ToolResult` construction. Migration should retain only Catalog query
  semantics and let `AgentToolbox` own the mechanical boundary.
- App composition currently uses `ToolExecutor` for two unrelated jobs: an
  executable global registry and the once-bound target/OpenAPI context store.
  Removing the global registry requires moving only the latter lifecycle into
  `CapabilityRuntime` or another existing App-owned seam.
- The HTTP implementation itself already owns `TargetHTTPTransport`; its only
  call-time dependency is the bound `ToolContext`. The scoped Probe can keep
  binding exactly that explicit dependency without making every tool receive
  it.
- The HTTP regression suite was coupled to the deleted global Executor even
  though its real subject is the target-bound implementation. A small
  test-local Agent toolbox preserves schema/error/redaction coverage while
  asserting the new explicit dependency boundary.
- MCP annotations previously drove a central risk classifier, but no Agent used
  that selector in production. MCP can preserve its discovered input/output
  contracts and source identity while Agent composition decides whether to
  include the tool at all.

## GitLab Live Test Findings

- The sole retained live entrypoint is the tracked
  `tests/test_gitlab_projects_operations_live.py`.
- The configured local Phoenix default is `http://127.0.0.1:6006`.
- The environment selects the official DeepSeek provider for both THINK and
  FAST roles. Secret values must not be printed or copied into artifacts.
- The retained test covers five GitLab Projects operations and remains
  destructive against its disposable local target. Its current authorization,
  evidence, and ten-minute execution limit are recorded in
  `docs/tasks/gitlab-projects-live-followup.md`.
- The installed Phoenix 2.13 client exposes `projects.list()` and
  `projects.delete(project_id=...)`. Phoenix project deletion is the narrow
  supported operation for removing each project's stored traces and project
  record; deleting every resolved project satisfies the requested cleanup.
- The Phoenix service is pinned to local image version 19.0.0 and bound only
  to `127.0.0.1:6006` with a named local data volume.
- Phoenix 19.0.0 protects the `default` project from project deletion but
  exposes `DELETE /v1/traces/{trace_identifier}`. Complete cleanup therefore
  means deleting every ordinary project, deleting all traces from `default`,
  and verifying only an empty protected default project remains.
- DeepSeek THINK and FAST configuration is ready, but the live test has one
  non-optional authentication input: `RESTSCOPE_GITLAB_PRIVATE_TOKEN`. It is
  absent from both the inherited process environment and `.env`; the repository
  contains no alternate GitLab token variable or credential file.

## 2026-08-03 Five-operation live continuation

- The authoritative entrypoint is the tracked
  `tests/test_gitlab_projects_operations_live.py`, not the untracked ten-
  operation experiment. It selects exactly GET collection, POST collection,
  GET item, PUT item, and DELETE item under `/api/v4/projects`.
- The tracked test authenticates directly against the disposable
  `gitlab-test` container using its generated root password, browser session,
  and CSRF token. It does not require `RESTSCOPE_GITLAB_PRIVATE_TOKEN`, so the
  earlier token blocker does not apply.
- The current modified GitLab OpenAPI asset parses to 1,740 operations and
  contains all five required operation keys.
- Local GitLab is healthy on port 7077, Phoenix is healthy on loopback port
  6006, and both THINK and FAST model/key settings are present without their
  values being printed.
- The caller will enforce a ten-minute deadline around the single tracked
  pytest entrypoint. The test itself exports full paginated Phoenix spans and
  coverage artifacts after the App closes.
- The original live flow hit its 600-second hard stop after 311 spans, 40 Test
  Cases, 69 LLM calls, and only two completed Smoke Coordinators. Production
  Smoke fully converges one operation before Supervisor schedules the next.
- That behavior contradicts this acceptance file's stated contract: all five
  operations need one complete ten-case Batch, but none must reach the normal
  80% threshold. A test-local Coordinator adapter can request threshold zero
  while delegating to the real production Coordinator. This preserves the full
  App/Supervisor/Smoke/Batch/Case/HTTP/monitor trace tree, guarantees one Batch
  per operation, and does not change production scheduling or stopping rules.
- After deleting the partial project and verifying the protected `default`
  project was empty, the corrected acceptance completed in 37.51 seconds.
  The report contains five attempts, zero unattempted operations, one Batch per
  operation, and five passed Smoke results without Failure kinds.
- The final Phoenix project is
  `restscope-gitlab-projects-five-20260803T080551Z-49a8e0e9`. The downloaded
  `phoenix-spans.json` contains 122 unique spans and exactly matches the 122
  server-side span IDs. All spans ended with `OK`; one App root owns the entire
  trace tree, all parents are present, and every selected operation has a
  completed Smoke span and ten completed Test Case spans.

## 2026-08-03 full-flow rerun decision

- The user explicitly rejected `_OneBatchSmokeCoordinator`. Although its 122
  spans completely covered five HTTP Batches, it prevented all DeepSeek,
  Failure Solve, Parameter Patch, and Parameter Patch Review execution.
- The tracked test has been restored to the production Coordinator for all five
  operations. The authentication fallback remains because it fixes an
  independent lifecycle failure in the disposable GitLab container.

## 2026-08-03 terminal-schema delivery and fresh live run

- The sole feature worktree was committed as `1231966`, fast-forwarded into
  local `main`, then removed with its merged branch. Its three root planning
  scratch files were deliberately excluded so they did not replace the main
  worktree's active GitLab live-test records.
- Main remains ahead of origin with the already authorized GitLab OpenAPI and
  live-test edits. No push is authorized in the current request.
- GitLab reports healthy. Phoenix currently contains one ordinary project,
  `restscope-gitlab-projects-five-20260803T081443Z-1ff55514`, plus the protected
  `default` project. Cleanup must delete the ordinary project and verify the
  protected project's trace list separately.
- Phoenix cleanup completed: the ordinary project was deleted and fresh API
  reads show only protected `default` with zero spans.
- The first restored full-workflow attempt reached its 600-second deadline
  after four operation attempts. All 497 available spans were preserved at
  `artifacts/gitlab-projects-five-live/gitlab-projects-five-20260803T093527Z-b600eca8/phoenix-spans-partial.json`.
- Two `FailureSolveAgent.solve` spans failed because required JSON feedback
  exceeded the configured Context character budget. The trace contains two
  `ParameterPatchReviewAgent.run` spans; the remaining Patch failures are
  locally rejected proposal/schema errors rather than strict-transport errors.
- Both crashes followed a grouped read where `openapi.list_inputs` returned a
  valid default page of 100 handles. Its pretty-printed tool payload was about
  11KB and the generic renderer treated it as indivisible JSON inside an 8KB
  feedback budget.
- The repair adds a dedicated OpenAPI result projection. Page metadata remains
  mandatory, handles are compact optional records, and omitted records carry a
  deterministic instruction to retry the same offset with a smaller limit or
  prefix. The new regression and related OpenAPI/Solve tests pass (10 tests).
- The second attempt proved that repair in the live path, but the first GET
  operation then exhausted all 50 Solve outputs. Strict Beta routing stayed
  active without fallback; DeepSeek repeatedly submitted old Patch keys such
  as `generators` or omitted the required `action`/`patch` root.
- The second attempt's 232 spans are preserved in its run directory. The Patch
  prompt and rejection feedback now show the sole wire path explicitly, forbid
  the three observed old keys, and include one generic implication Constraint
  shape. No compatibility alias was added; 45 Patch/Solve tests pass.
- The third attempt confirmed the wire guidance worked: its first Patch needed
  one proposal and one Review, the GET operation passed at 100%, and no Context
  error occurred. The run still reached 600 seconds while POST was processing
  its fifth distinct Failure; 432 spans were preserved.
- The unfinished POST Solve made ten model calls without proposing a Patch. It
  widened exact TC7 reads to TC1–TC11, queried six nested request Schemas, made
  HTTP probes, and then queried five response Schemas. These are not exact
  duplicate tool calls, but they are redundant broadening after the failure
  message and request fields were already available.
- Separately, POST response monitoring made roughly fifty short
  `ResponseValueTracker.select_sources` calls. That adds about one minute but
  was not the dominant stalled Solve.
- The fourth attempt completed GET faster, then POST hit
  `lookup_parameter_history: history_too_large` twice. The queried handles had
  long enum-backed current Generators; those current snapshots alone could
  produce 42KB even with no compatibility history.
- Current Generator snapshots are now optional in the bounded Memory text.
  Applied/conflict records and their Generator/Constraint change events remain
  required, so the safety rule is preserved while current enum bulk can be
  omitted. The RED regression and all 46 Patch/Solve tests pass.
- The fifth combined attempt again reached 600 seconds after GET passed and
  POST had completed four Batches and seven Solves. It preserved 307 spans.
  This confirms that repeated all-five serial runs cannot satisfy the external
  deadline even after the observed technical bugs are fixed.
- The existing test function reads `LIVE_OPERATION_KEYS` at runtime for IR
  filtering, coverage, and assertions. It can therefore be invoked unchanged
  in smaller dependency-aware groups while retaining the real App, the 80%
  threshold, all Agents, and the 600-second process-group watchdog.
- The first POST-only run showed that retry prose alone was insufficient: the
  model repeated grouped Memory reads six times. The private Solve Memory tool
  now accepts exactly one handle per call; multiple independent one-handle
  calls remain legal in the same output and execute concurrently. This makes
  the 8KB compatibility boundary deterministic rather than model-cooperative.
- The next POST-only run proved Memory was fixed but exposed 125
  `ResponseValueTracker.select_sources` calls. Operation Smoke eagerly asked
  for response-reference options for every configurable POST input before each
  Failure Solve, even though the eventual Patch affected only a few inputs.
- Reference discovery is now lazy: Solve starts without global aliases, and
  the exact Patch task's `affected_inputs` trigger one cached source lookup.
  Selected sources still reach Patch compilation and registration unchanged.
  Focused Failure Solve and Operation Smoke verification passes (67 tests).
- The post-fix POST-only run produced 282 spans, 20 Test Cases, 6 completed
  Solves, 13 Patch proposals, 6 Reviews, and zero response-source selection
  calls before the 600-second watchdog fired. This isolates the remaining
  duration to genuine full-convergence work and provider latency, not another
  identified implementation loop.
- A complete all-five trace cannot satisfy both the unchanged production 80%
  threshold and the 600-second deadline on the observed target. Changing the
  threshold/test stop condition would reverse the user's earlier rejection of
  a narrowed adapter and therefore requires a new explicit decision.
