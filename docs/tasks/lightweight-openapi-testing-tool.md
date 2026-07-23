# Lightweight OpenAPI Test Generation and Execution

Status: Implemented and verified (uncommitted)

## Objective

Add a small in-process path from a once-initialized persistent operation test
snapshot through one-to-one input generators to preflighted `TestCase`
requests and serial target execution. This provides a concrete alternative for
future Agent integrations without changing the current
Schemathesis-backed Supervisor.

## User-approved decisions

- One RESTScope deployment and generator database serve one current API. There
  is no `schema_id`, schema version history, or cross-API namespace.
- Operation identity is the parser's existing `METHOD /path` `operation_key`.
- Every configurable request input receives a deterministic `input_node_id`,
  semantic canonical path, parent ID, and local `SchemaIR` reference while
  building the first snapshot.
- The first App initialization freezes all operations and creates exactly one
  generator for every frozen input node in one transaction. A singleton marker
  prevents all later IR synchronization.
- Generator configuration is persisted by operation revision. Rows reference
  only `input_node_id`, inclusion probability, and strategy.
- Whole-set replace and node-level patch are the only mutation operations.
  There is no runtime delete/reset tool.
- `restscope.testing.run_operation` generates and serializes every case before
  any request, sends cases serially, and returns response metadata without
  reading response bodies.
- `restscope.http.request` remains a separate ToolSpec. The two capabilities
  share target URL/header/client/error primitives but have different request
  contracts and result boundaries.
- Both execution tools are high-risk, non-read-only, and allowed for every
  Agent role without another live-testing gate.
- Generator inspect/replace/patch capabilities are registered management
  endpoints but are deliberately excluded from Agent model tool selection in
  this iteration; no configuration-management Agent role was approved.

## Implemented boundary

- The parser indexes parameters, request-body roots, media types, object
  properties, array items, and `allOf`/`oneOf`/`anyOf` branches. IDs exclude
  source pointers, `$ref` locations, descriptions, and input declaration order.
- The frozen snapshot stores method, path, parameter serialization, supported
  body media contracts, input-tree relationships, and local generation
  constraints. `run_operation` does not access or compare the current IR.
- Later OpenAPI changes do not add, remove, update, or invalidate Catalog
  records. Even an operation removed from the current IR remains executable
  from the initial snapshot.
- The generator catalog enforces expected revisions, complete frozen node sets,
  generator structural validity, database compare-and-swap revisions, and
  inclusion probability `1.0` for required/structural nodes. Feedback-owned
  replace/patch values are not required to satisfy frozen Schema value
  constraints.
- Built-ins cover constants, weighted choice, integer/number ranges, random
  strings, booleans, UUID/date/date-time/email, objects, arrays, and weighted
  variants. Complete generation and request serialization still finish before
  any target request is sent.
- Serialization covers OpenAPI 3 path/query/header/cookie style+explode,
  Swagger 2 collection formats, and JSON, text, and URL-encoded request bodies.
- Every generated URL and merged header set is validated before the first
  client is opened. Each request then uses a fresh synchronous `httpx.Client`,
  a maximum 30-second timeout, no redirects, no retries, and no retained Cookie
  jar. Generated OpenAPI Cookie parameters are allowed only on this IR-bound
  path and merge without replacing same-name context cookies; the raw HTTP tool
  still rejects per-call Cookie and credential headers.
- Query serialization honors Swagger 2's implicit CSV collection format and
  OpenAPI `allowReserved`. This intentionally preserves `&` and `=` when the
  schema opts in, so such a value may be interpreted as query structure by
  form-style servers; `#` remains encoded because a literal hash starts a URI
  fragment and cannot be transmitted as query data.
- Nullable container nodes can explicitly generate JSON `null`. Unsupported
  automatic derivation from mixed combiners and conditional/`not` schemas is
  recorded as a recoverable node-level reason; a feedback Generator may replace
  that derivation without revalidating the generated value against the Spec.
- Reports and traces preserve generated values, sensitive-named parameters,
  merged ToolContext headers, and generator configuration arguments. The
  shared App Redactor replaces only exact configured THINK, FAST, and Phoenix
  API key values. Trace output keeps the independent 64 KiB limit; normal
  testing reports do not contain response bodies.

## Persistence and compatibility

- Alembic revision `0002_create_generator_configs` adds
  `generator_catalog_state`, `operation_generator_configs`, and
  `input_generator_configs` after the schema-source baseline.
- Each default App starts from a new database file. Switching to another API
  requires another `DB_URL` or explicit inspection and deletion of the retained
  run artifact; App initialization never clears Catalog rows.
- This approved persistence is limited to reusable generator input
  configuration. It does not reintroduce the former database-backed Planner,
  operation graph, inferred dependency store, scheduler queue, Agent state,
  generated cases, or reports.

## Non-goals

- Replacing Schemathesis in the Supervisor or `OperationTestAgent`.
- Response schema validation, postprocessors, multipart/file/binary requests,
  report persistence, redirects, retries, parallel execution, or live target
  verification.
- Schema-version drift detection or multiple API namespaces.
- Runtime Catalog reset/delete or automatic reconciliation with newer IRs.
- Commit, merge, push, or worktree cleanup without separate authorization.

## Verification evidence

- TDD first observed missing IR nodes, persistence, generators, serialization,
  transport extraction, capability registration, and App composition before
  implementing each boundary.
- Focused stable-ID, CRUD/revision/rollback/migration, generation,
  serialization, execution, capability, App, and raw HTTP transport tests pass.
- The first full root run reached 212 passed and 10 skipped; its only two
  failures were superseded single-table assertions. After updating those
  assertions, focused schema/generator migration tests passed 17 tests.
- A completion review found and drove fixes for sensitive rendered-path
  evidence, late transport validation, query serialization, nullable/mixed
  compositions, concurrent revision writes, case-insensitive header/media
  identity, and duplicate adapter exports.
- `uv sync && uv run pytest -q` passed with 228 tests and 10 skips.
- `uv sync --extra tracing && uv run --extra tracing pytest -q` passed with 241
  tests and 2 skips.
- `uv run pytest -q tests/test_schemathesis_mcp_contract.py` passed its real
  local stdio contract test.
- `uv run --extra tracing python -m compileall -q restscope` and
  `git diff --check` passed.

No live LLM, real target, or Phoenix collector request is part of this work.

## Follow-up decision: once-initialized independent Generator Catalog

On 2026-07-23, the user replaced IR synchronization and node-change detection
with a deliberately independent test model:

- the first successful App initialization persists every operation snapshot,
  its one-to-one default generators, disabled records, and the singleton
  Catalog marker in one transaction;
- later App initializations bind their current IR but do not inspect or mutate
  the initialized Catalog;
- `run_operation` reads only the frozen request snapshot and current target
  connection values;
- inspect exposes the complete frozen snapshot and generator values;
- full replace and node-level patch use revision locks, while delete/reset is
  not available;
- recoverable default-derivation failures were initially cleared only by a full
  replace; the later enum-priority and feedback-owned Generator decision below
  supersedes that restriction for node-level patch.

Fresh verification for this follow-up:

- `uv run pytest -q` passed 270 tests with 2 opt-in tests skipped.
- `uv run --extra tracing pytest -q` passed 270 tests with 2 opt-in tests
  skipped.
- `uv run pytest -q tests/test_schemathesis_mcp_contract.py` passed the real
  local stdio contract test.
- `.venv/bin/pytest -q tests/test_testing_migration.py` passed the rewritten
  migration test.
- Completion review also verified request-only `readOnly` projection, rejected
  serializer-incompatible nullable text values, invalid `deepObject`
  contracts, wildcard request media ranges, and ranges spanning discrete
  enum domains. The focused OpenAPI testing suite passed 67 tests.
- `.venv/bin/python -m compileall -q restscope` and `git diff --check` passed.
- No DeepSeek, real target, or Phoenix collector request was executed.

## Follow-up decision: unified exact-value redaction

On 2026-07-23, the user replaced the original report-redaction boundary with a
single App-owned Redactor. Local `_ValueRedactor` logic and sensitive
name/pattern heuristics are removed. This deliberately makes generated
token/password/Cookie values and target Authorization headers visible in both
the execution report and Phoenix traces while still masking configured
LLM/Phoenix keys by exact value.

Fresh verification after this follow-up:

- The focused redaction, observability, HTTP, ToolContext, testing, and LLM
  suites passed 82 tests.
- `uv run pytest -q` passed 247 tests with 2 opt-in tests skipped.
- `uv run --extra tracing pytest -q` passed 247 tests with 2 opt-in tests
  skipped.
- The real Schemathesis stdio contract passed 1 test.
- `uv run --extra tracing python -m compileall -q restscope` and
  `git diff --check` passed.
- No DeepSeek, real target, or Phoenix collector request was executed.

## Follow-up decision: one-shot fresh SQLite App lifecycle

On 2026-07-23, the user made the default App database a one-run artifact:

- a DB-backed App accepts only a nonexistent local file SQLite address,
  resolves relative paths from the startup working directory, exclusively
  creates the file, and automatically runs Alembic to head;
- every existing file, empty file, directory, or symbolic link is rejected,
  and memory SQLite, SQLite URI addresses, and non-SQLite URLs are unsupported;
- migration, runtime, analyzer, and App construction failures remove only the
  database and sidecars created by that construction attempt;
- successful construction, initialization failure, and `close()` retain the
  database, so a later start must use a new URL or an explicit manual deletion;
- injecting a complete `CapabilityRuntime` bypasses database handling, while a
  custom operation runner alone still uses the default fresh database.

This supersedes the earlier allowance for a later App to bind another IR while
reusing the initialized Catalog. Catalog independence still applies within the
single successful App initialization and to low-level Catalog consumers.

Focused TDD verification first failed on missing path normalization/migration,
legacy pre-created App databases, a broken-symbolic-link escape, and cleanup
after a post-creation file-claim failure. The updated Fresh SQLite and App
lifecycle focused suite passed 41 tests. Completion review also found and drove
fixes for falsey injected runtimes and tracing runtimes, internally owned MCP
host leaks during factory failure or interruption, repeated SQLite `uri` query
parameter bypasses, cleanup after process interruption, and inode-safe cleanup
when another process replaces the claimed database path. Atomic claim cleanup
also tracks descriptor ownership through `fstat` and `close` failures or
interruptions without retry-closing an ambiguously closed descriptor.

Fresh completion verification:

- `uv run pytest -q` passed 314 tests with 2 opt-in tests skipped.
- `uv run --extra tracing pytest -q` passed 314 tests with 2 opt-in tests
  skipped.
- `uv run pytest -q tests/test_schemathesis_mcp_contract.py` passed the real
  local stdio contract test.
- `uv run pytest -q tests/test_testing_migration.py
  tests/test_schema_catalog.py::test_alembic_chain_upgrades_and_downgrades_all_persistence_tables`
  passed 2 migration tests.
- `uv run --extra tracing python -m compileall -q restscope` and
  `git diff --check` passed.
- No live LLM, real target API, or Phoenix collector request was executed.

## Follow-up decision: enum-first defaults and feedback-owned generators

On 2026-07-23, the user separated Spec-derived defaults from generators learned
through test feedback:

- initial concrete-value precedence is `enum > const > default > example`;
  every non-empty enum becomes an equal-weight choice and is accepted even when
  the Spec contains contradictory sibling value constraints;
- an empty enum produces a recoverable reason attributed to its input node;
- persisted replace/patch generators are authoritative for generated values and
  may differ from frozen type, enum, pattern, format, range, container, or
  composition constraints;
- required and structural input nodes remain non-optional, while frozen
  parameter serialization and request-body media contracts remain mandatory;
- runtime generation dispatches by configured strategy and does not perform a
  second Spec value-validation pass;
- recoverable reasons carry `input_node_id`; patch clears reasons belonging to
  updated nodes and enables the operation after the final blocking reason is
  removed. Full replace still clears every recoverable derivation reason.

This decision does not change Catalog persistence, tool interfaces, response
collection, HTTP preflight, or the Schemathesis-backed Agent flow.

Fresh verification for this follow-up:

- `uv run pytest -q` passed 323 tests with 2 opt-in tests skipped.
- `uv run --extra tracing pytest -q` passed 323 tests with 2 opt-in tests
  skipped.
- The real local Schemathesis stdio contract plus Generator/Alembic migration
  checks passed 3 tests.
- `uv run --extra tracing python -m compileall -q restscope` and
  `git diff --check` passed.
- No live LLM, real target API, or Phoenix collector request was executed.
