# Live Run Observer UI

Status: IndexedDB history implementation and verification complete — Git delivery not authorized

## Objective

Add a loopback-only, read-only, real-time RESTScope observer that displays key
Agent turns, tool executions, complete Smoke Batches, and the latest Failure
Resolution worklist without changing testing decisions. The backend observer
remains process-local; the browser may retain five complete snapshots solely
for reopening recent UI evidence.

## Approved scope

- Keep Phoenix intact and make the observer independently useful when tracing is disabled.
- Keep one complete current-run event history in process memory with no extra total-size cap.
- Serve a React/TypeScript/Vite/Ant Design interface through optional Starlette/Uvicorn dependencies.
- Expose a snapshot GET endpoint and cursor-based SSE GET endpoint on loopback only.
- Display complete final requests and already-bounded responses using the current Redactor policy.
- Keep every viewer action read-only; no test pause, retry, approval, edit, or write endpoint.
- Version both frontend source and deterministic built assets.
- Treat caller interruption as the end of one Run, not the App lifetime: retain
  the stopped snapshot and UI until the next Run or explicit App close.
- Publish only schema-v2 `agent_turn`, `tool_call`, and `smoke_batch` timeline
  events. Flow phases, raw message/model cards, ordinary HTTP cards, and
  Worklist revision cards remain internal aggregation evidence.
- Preserve every Agent turn's incremental input and current assistant output,
  merge HTTP Probe evidence into its tool card, and retain all generated Smoke
  Test Cases inside their owning Batch card.
- Keep overlapping diagnoses unchanged while making the latest successful
  Worklist revision monotonic, resolving each E reference to its exact
  session-local Failure text, and assigning stable `WI-001` identities.
- Aggregate Agent turns by runtime session in a read-only AntV G6 canvas, keep
  Tool and Smoke Batch executions as separate nodes, and connect each Tool to
  the Assistant message that requested it.
- Expand only the selected Agent message and its own Tool-call metadata inside
  one continuous message surface. Tool/HTTP and Batch details likewise stretch
  their original card instead of adding another panel, page, modal, or Drawer.
- Animate that fused surface from the summary's original content position:
  opening uses 300 ms, closing uses 200 ms, and the G6 node, message ports,
  connected edges, and surrounding layout move with the same transition.
- Persist the latest five complete schema-v2 snapshots in same-origin browser
  IndexedDB. Restore the newest snapshot when no live Run is available, keep
  SSE updating in the background while history is viewed, and provide only
  browser-local delete and clear actions.

## Explicit non-goals

- Backend/SQLite observer persistence, App or workflow recovery, cross-origin
  history, authentication, or remote binding.
- Replacing Phoenix, changing Agent decisions, or adding a general event platform.
- Supporting concurrent `RESTScopeApp.run` calls in one observer.

## Decisions and risks

- `UI_ENABLED` defaults to false; `UI_PORT` defaults to 8765; host is fixed to `127.0.0.1`.
- Observer and server failures are fail-open and must not change testing results.
- Target Authorization, Cookie, and business fields remain visible, matching the approved trace boundary.
- Browser history stores the exact already-redacted UI payload without further
  encryption or field removal. It therefore includes visible Authorization,
  Cookie, Prompt, Tool, HTTP, Batch, and Worklist details until the record ages
  out of the latest five or the user clears site data.
- Details are not evicted. Large runs may exhaust server/browser memory; the user explicitly accepted this risk.
- The observer module owns event normalization, storage, and subscription behind a small sink Interface. Workflows never import UI DTOs.
- `KeyboardInterrupt` is the current process-local stop seam. It re-raises to
  the caller after the observer records `stopped`; no UI write endpoint or
  unsafe cross-thread cancellation Interface is introduced.

## Verification record

IndexedDB browser-history follow-up:

- Added same-origin browser persistence for the latest five complete schema-v2
  snapshots. Same-Run updates coalesce over 100 ms, while a `run.reset` in the
  same window still preserves both Runs. Saving returns only the changed summary
  and pruned IDs, so frequent writes do not reread five large Prompt/HTTP bodies.
- Startup restoration, server authority, explicit frozen-history selection,
  background live resets, local deletion/clear, incompatible records, quota
  failure, exact sensitive-field retention, and React StrictMode are covered by
  `fake-indexeddb` and component tests. The dependency is development-only.
- `npm test -- --run` passed 10 files and 70 tests. `npm run lint` and Ant
  Design 6.5.3 lint passed with zero issues; TypeScript and the Vite production
  build passed.
- `uv run --all-extras pytest -q` passed 696 tests with 6 skips. Two consecutive
  production builds produced identical hashes for all five static assets;
  compileall and `git diff --check` passed.
- Real browser acceptance used the production assets at 1440×900. A local
  schema-v2 Run was received and saved, then the snapshot endpoint was removed
  and the page reloaded. The same Agent Run, event, operation, and three-second
  duration restored from IndexedDB while the connection warning remained
  honest. Dark and light themes had equal document/client dimensions with no
  overflow, local clear immediately removed the Run, and browser logs contained
  no warnings or errors. The temporary server and QA tab were closed afterward.
- The backend observer, GET/SSE schema, Phoenix, SQLite, testing decisions, and
  filters/canvas preferences remain unchanged. No IndexedDB history is available
  across browser profiles, origins, or UI ports. No feature files have been
  staged or committed.

Collapsed Tool summary follow-up:

- Ordinary Tool nodes now omit their Input/Output projection entirely while
  collapsed, including the previously reserved empty summary height. Their
  full Input and Output tabs remain available after expansion.
- HTTP Tools keep only their method, final URL, and response status summary;
  Smoke Batch nodes keep their success-count summary.
- Focused component/model checks passed 24 tests, ESLint and Ant Design lint
  passed with zero issues, and the production bundle rebuilt successfully.
- The real retained Run showed two collapsed Worklist Tool nodes with a 0 px
  reveal and no summary text. Expanding one restored both Input and Output,
  preserved zero page overflow, and produced no browser warnings or errors.

Scroll-like detail motion follow-up:

- Agent messages, Tool/HTTP Tool nodes, and Smoke Batch nodes now use one
  shared same-origin reveal. The compact summary cross-fades into the complete
  content while the original Card stretches; opening uses 300 ms and closing
  uses 200 ms with reduced-motion immediate completion.
- The Web Animations implementation measures its current visible height before
  every change, keeps closing detail mounted until completion, and cancels
  stale work on reversal, Run reset, StrictMode cleanup, or node destruction.
- G6 motion is installed before structural data changes, then restored and
  flushed in static mode. Its HTML key rectangle, bounds, message ports, node
  position, layout, and edge endpoints use the same timing. This ordering fixes
  a real-page defect where changing animation options after data erased the
  node-size diff and clipped expanded content inside the old frame.
- Frontend ESLint, all seven Vitest files (42 tests), TypeScript/Vite build, and
  Ant Design 6.5.3 lint passed. The Ant Design report contains zero deprecated,
  accessibility, usage, performance, or skipped-file issues.
- Focused observer/UI/Smoke checks passed 56 tests. The complete optional suite
  passed 694 tests with 6 skips; compileall and `git diff --check` passed.
- Two consecutive production builds produced identical SHA-256 hashes for all
  five HTML/CSS/JavaScript assets.
- Real 1440×900 acceptance passed in light and dark themes. The Agent frame
  grew from about 735 px to 1114 px with no internal overflow; the selected
  message detail measured about 415 px at the current canvas zoom. A Tool frame
  grew to about 595 px with transparent, radius-free detail and no nested Card.
  Message ports and edges remained attached to the fixed message header,
  Worklist remained 360 px, horizontal overflow stayed zero, and the browser
  warning/error log was empty. The retained Run has no Batch node, whose shared
  motion path is covered by the component regression.

Worklist real-time/readability follow-up:

- Added deterministic reducer coverage for reversed StrictMode snapshots, old
  Worklist revisions with newer cursors, and duplicate SSE replay.
- Added cancellation coverage proving a cleaned-up snapshot request cannot
  dispatch state or create an EventSource after it eventually resolves.
- Added Worklist-store coverage for the `WI-001` format, contiguous issuance,
  stable identity, deletion without reuse, and atomic rejection.
- Added the session-local E-to-Failure projection and separate Failure, Test
  Case, suspected-parameter, and Patch-candidate sidebar sections.
- The latest Worklist projection now carries and displays the operation key
  from its successful `failure_resolution.write_worklist` event.
- Focused Python Worklist/Resolution/Observer checks passed 54 tests with one
  skip. Frontend lint passed and all four Vitest files passed 18 tests.
- Ant Design 6.5.3 lint reported zero deprecated, accessibility, usage,
  performance, or skipped-file issues; TypeScript/Vite production build passed.
- `uv run --all-extras pytest -q` — 694 passed, 6 skipped.
- Two consecutive production builds produced identical SHA-256 hashes for the
  HTML, CSS, and JavaScript assets.
- Browser acceptance at 1440x900 passed in dark and light themes. The Worklist
  measured 360px wide with equal client/scroll widths, long Failure and
  parameter values wrapped fully, Revision 6 and active `WI-006` agreed, E/TC/P
  and unavailable-Failure states were readable, and browser logs were empty.
- `python3 -m compileall -q restscope tests`, `git diff --check`, and final port
  cleanup passed. No file was staged, committed, merged, or pushed.

Agent-session canvas follow-up:

- Added deterministic session aggregation, stable message IDs, exact
  `tool_call_id` port resolution, parallel Tool edges, fallback ports, nested
  Agent edges, search context, collapse, and SSE revision coverage.
- Added inline vertical detail expansion for every Agent message, Tool/HTTP,
  and Smoke Batch node. The expansion preserves the fixed Worklist sidebar and
  never creates a modal, Drawer, or separate detail view.
- Agent message cards now show only role, Turn, timestamp, and content summary;
  the Agent detail uses Prompt/response labels. Input/Output remains reserved
  for Tool execution details, where it describes the actual call boundary.
- Frontend ESLint, all 6 Vitest files (33 tests), TypeScript, Vite build, and
  Ant Design 6.5.3 lint passed; the Ant Design report contains zero deprecated,
  accessibility, usage, performance, or skipped-file issues.
- Focused observer/UI/Smoke/Worklist regression checks passed 80 tests;
  `uv run --all-extras pytest -q` passed 694 tests with 6 skips.
- Two consecutive builds produced identical static-asset hashes.
  `compileall` and `git diff --check` passed.
- Real-page browser acceptance at 1440×900 passed in dark and light themes:
  complete Agent prompt and Tool details expanded inside their nodes, the
  Agent subtree collapsed/restored, Tool edges stayed attached to message
  ports, no dialog appeared, Worklist remained 360 px with no horizontal
  overflow, and the current production bundle logged no browser errors.

Fused single-message expansion follow-up:

- Replaced turn-level Prompt/response expansion with one-message expansion for
  System, User/Harness, Assistant, and Tool-result cards. Collapsed text is
  bounded to 160 Unicode code points; expanded text replaces the summary and
  renders once with safe Markdown or formatted JSON.
- Assistant Tool calls and Tool-result name/call ID remain attached to their
  owning message. Finish reason, structured turn output, raw turn JSON, and
  unrelated messages are not included.
- Agent message, Tool/HTTP, and Smoke Batch details now share their original
  outer border, background, and radius. Only a light internal divider separates
  the fixed scroll region, so expansion visually stretches the node.
- Frontend ESLint, all six Vitest files (38 tests), TypeScript/Vite build, and
  Ant Design 6.5.3 lint passed; the Ant Design report contains zero deprecated,
  accessibility, usage, performance, or skipped-file issues.
- `uv run --all-extras pytest -q` passed 694 tests with 6 skips. Two consecutive
  production builds produced identical HTML/CSS/JavaScript SHA-256 hashes;
  compileall and `git diff --check` passed.
- Real-page browser acceptance at 1440×900 passed in light and dark themes on
  the retained 8766 Run. The selected Assistant body appeared once, the summary
  disappeared, Agent detail measured 440px, Tool detail measured 520px, and
  neither detail owned a background or radius. The expanded message measured
  exactly 544px, the Agent had no internal overflow, Tool Input/Output remained
  intact, Worklist stayed 360px without horizontal overflow, and browser logs
  contained no warnings or errors. The retained Run had no Smoke Batch node, so
  its fused boundary is covered by the component test rather than claimed as a
  live-page observation. The App/UI remains running and no Git operation ran.

Schema-v2 final verification:

- Focused observer/UI/Phoenix/Smoke/Resolution group — 76 passed.
- `npm run lint`, `npm test -- --run`, and `npm run build` — passed; 4
  Vitest files and 13 tests passed.
- `npx --no-install antd lint ./src --format json` against Ant Design 6.5.3 —
  zero deprecated, accessibility, usage, performance, or skipped-file issues.
- `uv run --all-extras pytest -q` — 691 passed, 6 skipped.
- Two consecutive production builds produced identical SHA-256 hashes for the
  schema-v2 HTML, CSS, and JavaScript assets.
- `python3 -m compileall -q restscope tests` and `git diff --check` — passed.
- Browser acceptance at 1440x900 passed in dark and light themes with two
  Agent turns, three completed Tool cards, one stopped Tool warning, a 20-case
  Batch, a three-item Worklist, search, JSON truncation, timeout, and binary
  Base64 evidence. The stopped snapshot stayed online with SSE live and the
  browser warning/error log was empty.

Earlier foundation and lifecycle verification:

- `uv run --extra ui pytest -q tests/test_live_ui_app.py tests/test_live_ui_server.py tests/test_live_run_observer.py tests/test_observability.py tests/test_observability_integration.py` — 25 passed, 11 skipped.
- Failure Resolution, finalizer, Smoke, package-boundary, testing, transport, App bootstrap, and ToolContext regression group — 100 passed, 1 skipped.
- `npm run lint` — passed with zero ESLint warnings.
- `npm test` — 4 files and 13 tests passed.
- `npm run build` — passed; Vite output refreshed under `restscope/ui/static/`.
- `antd --version 6.5.3 --format json lint ui/src` — zero deprecated, accessibility, usage, or performance issues after replacing the six v6-deprecated props reported by the first pass.
- `uv run --extra ui pytest -q` — 663 passed, 18 skipped.
- `uv run --all-extras pytest -q` — 685 passed, 6 skipped.
- Run/App lifecycle focused group — 16 passed; interrupted Run snapshot,
  `run.update` SSE delivery, App/UI retention, App reuse, and explicit close.
- Observability/App/tracing regression group after the lifecycle change — 88
  passed, 1 explicitly selected Phoenix scenario skipped.
- `uv run --all-extras pytest -q` after the lifecycle change — 687 passed, 6
  skipped.
- `python -m compileall -q restscope tests` and `git diff --check` — passed.
- Two consecutive production builds produced identical SHA-256 hashes for the HTML, CSS, and JavaScript assets.
- The built wheel contains `restscope/ui/server.py`, `static/index.html`, and both hashed assets.
- Browser QA at 1440×900 passed in dark and light themes with a long exact prompt, compound HTTP 422 exchange, and three-item Worklist; browser warning/error log was empty.

A user-authorized local GitLab run exercised the live page with the configured
model and Phoenix. Before the lifecycle correction it reached 276 events and
entered `POST /api/v4/projects`; force-stopping that old runner also destroyed
its process-local snapshot and exposed the missing stopped-Run state. The
follow-up lifecycle behavior is covered offline rather than repeating target
mutations. No remote viewer was used. No files were staged, committed, merged,
pushed, or cleaned up.

## 2026-08-06 ten-minute live verification

The user explicitly authorized another destructive five-operation GitLab run
and requested backend termination after ten minutes. The measured child ran
for 600.71 seconds; the supervisor sent SIGINT at its 600-second deadline and
graceful App/UI cleanup finished after 615.97 total seconds without SIGTERM or
SIGKILL escalation.

- The schema-v2 UI remained responsive until shutdown and reached 221 semantic
  cards immediately before the deadline. GET Projects produced 1/10 and then
  10/10 Batches. POST Projects produced two 0/10 Batches and remained in
  Resolution round 2, so the three item GET/PUT/DELETE operations were not
  scheduled within the bounded window.
- Phoenix retained 348 spans: 341 OK and 7 ERROR. The trace includes 86 real
  LLM calls, 40 real Test Case executions, four Smoke Batches, and two
  Operation Smoke sessions. Six errors are the deliberate interruption
  cascade; one Tool error is a rejected TC2/Failure-source Worklist reference.
- The run-local database contains six Failures, six terminal Resolution
  Attempts, five Generator change events, and three Constraints.
- Authorized POST probes created three persistent projects in the disposable
  target: `cep-probe-flat-1`, `cep-probe-tc12-1`, and
  `cep-probe-tc11-1`. They were reported and not rolled back.
- Artifact directory:
  `artifacts/gitlab-projects-five-live/gitlab-projects-five-20260805T234838Z-7c86f0a5`.
  The interruption occurred before report/coverage export, so only
  `run-metadata.json` and `evidence.sqlite` were written.
- Final checks confirmed no RESTScope process and no listeners on 8765, 7077,
  or 6006. GitLab and the temporary Phoenix service were stopped; data volumes
  and artifacts remain. No files were staged, committed, merged, or pushed.

## 2026-08-06 Agent call-chain and stable-column follow-up

- App-owned Agent identity now carries the optional parent session for nested
  Agents. A Tool-started child keeps both its visible Tool parent event and its
  Agent-session ancestry; direct nested Agents can therefore fall back to the
  parent Agent header without changing Phoenix spans or schema-v2 event kinds.
- The canvas resolves the full causal chain before filtering: an Assistant
  message points to its Tool, a Tool output points to the child Agent it starts,
  and a direct nested Agent points to the exact Assistant message when possible.
  Tool-mediated calls do not receive a duplicate Agent-to-Agent shortcut, and
  Tool-result messages still create no return edge.
- Parallel calls from one Assistant message share one stable call-group column.
  Later message groups and nested calls advance to the right. Columns are
  computed from the complete event set, so filtering, collapse, search, and
  ordinary event revisions do not move previously assigned groups.
- G6's built-in layout adapter discards custom node data before Dagre runs. The
  canvas now calls G6's exported AntV Dagre layout directly, preserving the
  stable `layer`, then renders the resulting positions. This keeps the intended
  columns visible in the real canvas rather than only in the view model.
- Tool nodes expose a fixed Output port for child-Agent edges. Nested edges use
  the lightweight “启动 Agent” label, with “调用消息不可用” added only for a
  session-header fallback.
- Every role's string body is checked after leading whitespace is removed. A
  body beginning with `{` or `[` is formatted as JSON only when parsing yields
  an object or array; invalid JSON and primitives remain safe Markdown. The
  observer payload and searchable source text are unchanged.
- Focused observer/UI tests passed 51 tests. Frontend ESLint, 53 Vitest tests,
  TypeScript/Vite build, and Ant Design 6.5.3 lint passed. The complete optional
  suite passed 696 tests with 6 skips; compileall and `git diff --check` passed.
  Two consecutive builds produced identical hashes for all five static assets.
- Real-page 1440×900 acceptance passed in dark and light themes with no page
  overflow. It verified columns 0/1/2/3, parallel Tools sharing column 1,
  Tool-to-child and direct nested-Agent edges, and formatted expanded JSON.
  The temporary 8767 QA server was stopped afterward; the existing 8766 UI was
  not changed. No files were staged, committed, merged, pushed, or cleaned up.
- A later real GitLab run exposed two canvas follow-ups. AntV Dagre applies the
  configured 120px rank separation both as node expansion and layer spacing,
  producing a measured 240px empty gap; sequential Agent call groups also add
  columns, so long-session parent/child edges can span much farther. High-rate
  SSE revisions can cancel an in-flight layout, which explains transient
  geometry during updates but still needs an exact visual regression before a
  layout fix is approved.
- Trackpad navigation now separates wheel gestures using the browser's pinch
  modifier: ordinary two-finger movement runs G6 `scroll-canvas`, while pinch
  runs `zoom-canvas`. The focused component file passed 20 tests; ESLint,
  TypeScript/Vite build, Ant Design 6.5.3 lint, and `git diff --check` passed.
