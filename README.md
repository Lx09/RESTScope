# RESTScope

RESTScope is currently a small Python package for parsing Swagger 2.0 and
OpenAPI 3.x specifications into a normalized intermediate representation.

If you are new to programming or to this repository, start with the
[code-reading guide](docs/code-reading-guide.md). It explains the runtime
flow, package ownership, domain terms, and safe places to investigate or
optimize before you read individual modules.

## Configuration

The parser-only package uses a short optional `.env` file:

```env
LOG_LEVEL=INFO
DATA_DIR=./data
# LOG_FILE=./data/logs/restscope.log
# MCP_SERVERS_FILE=/path/to/mcp.servers.json

DB_URL=sqlite:///./data/restscope.db
DB_ECHO=false
# Optional. If omitted, one seed is generated at App startup and reported.
RANDOM_SEED=731

THINK_PROVIDER=openai_compatible
THINK_MODEL=glm-4.5-air
THINK_API_KEY=your-api-key
THINK_BASE_URL=https://open.bigmodel.cn/api/paas/v4

FAST_PROVIDER=openai_compatible
FAST_MODEL=glm-4.7-flash
# FAST_PROVIDER, FAST_API_KEY, and FAST_BASE_URL default to THINK_* values

# Optional local read-only run observer.
UI_ENABLED=false
UI_PORT=8765
```

The default `RESTScopeApp` runtime accepts only a local file SQLite URL whose
target does not yet exist. Relative database paths are resolved from the
process startup directory. App construction exclusively creates the file and
runs the packaged Alembic migrations; an existing file, directory, or symbolic
link is rejected. In-memory SQLite, SQLite URI addresses, and non-SQLite URLs
are not supported by this App lifecycle.

The official DeepSeek API is available through the explicit `deepseek`
provider. DeepSeek protocol differences remain inside the LLM adapter, so
Agents use the same provider-neutral requests and tool loops:

```env
THINK_PROVIDER=deepseek
THINK_MODEL=deepseek-v4-pro
THINK_API_KEY=your-deepseek-api-key
THINK_REASONING_MODE=enabled
THINK_REASONING_EFFORT=high

FAST_PROVIDER=deepseek
FAST_MODEL=deepseek-v4-flash
FAST_REASONING_MODE=disabled
```

`https://api.deepseek.com` is used by default. Third-party DeepSeek gateways
are not part of the supported contract.

Parameter Patch Proposal and Review decisions use JSON Schema responses on the
standard endpoint and are validated again locally before compilation or
acceptance. Proposal may first make bounded read-only resource and observed-
response-field lookups; Review receives no tools. Parameter Patch deliberately
disables thinking for these compact structured decisions.

## Development

```bash
uv sync
uv run pytest
```

## Database

The database is one audit artifact for one App. It contains exactly 19 business
tables: the complete current normalized OpenAPI and response-change events,
current per-input Generators and Constraints, narrow Resource Identifier and
bounded Response Value evidence, stable Failures, terminal Resolution Attempts,
validated input attribution, and accepted Generator/Constraint change events.
It never stores raw responses, Test Cases, Batches, Patch samples, model
conversations, plans, queues, scheduler progress, or authentication material.

Successful App construction leaves its SQLite file in place, including after
`close()`. A later process must use a new `DB_URL` or explicitly inspect and
delete the old run artifact before starting. RESTScope never overwrites or
automatically deletes a successfully created database. A caller that injects a
complete custom `HarnessRuntime` owns its persistence and bypasses this
default database bootstrap.

Alembic now has one `0001_current_baseline` for fresh databases. Old exploratory
database files and the former `0001`–`0006` chain are intentionally
incompatible. Every project-created SQLite connection enables foreign keys;
fresh creation also runs integrity and foreign-key checks. There is no restore,
resume, reset, migration-from-old, or automatic-delete API.

```python
from restscope import RESTScopeApp, RESTScopeConfig

config = RESTScopeConfig.from_environment()
app = RESTScopeApp.from_config(config)
# After app.initialize(...):
current_document = app.export_current_openapi()
events = app.list_openapi_change_events("GET /projects/{projectId}")
```

These methods are read-only audit/export interfaces. They do not recover an
App from the retained file. Detailed table ownership, keys, retention, and
transaction rules are documented in `docs/database_design.md`.

## LLM

The MVP LLM layer lives in `restscope.llm`. It provides provider-neutral request
and response schemas, OpenAI-compatible and DeepSeek providers, model selection,
and structured output validation. `restscope.agent` defines explicit Profiles;
`restscope.tools` owns the global subject-grouped Tool Catalog and execution
runtime; `restscope.skills` owns reusable instruction metadata; and
`restscope.harness` validates every Profile and child relationship at
construction, then `start_main_agent(profile_name)` atomically resolves and
binds only that Profile's model configuration, ordered Tools, selected Skill
metadata, bounded Context Sources, and described direct child Profiles. A
private Prompt Session automatically adds `skill.read` when Skills are selected
so the model can load only an authorized `SKILL.md` body on demand. A Profile
must explicitly grant `file.read` when a selected Skill requires its linked
Markdown References; the Harness binds that Tool only to the selected Skills'
startup-validated in-memory files. It does not expose a separate resolution or
Prompt object that callers could use to assemble a broader Agent. Unit tests
provide their own local stub providers; the runtime package does not register
an offline fake provider.

The project currently ships two built-in standard Skills. The low-risk
`build-parameter-patch` Skill owns detailed Generator, Constraint,
compiler/sampling, proposal-correction, and semantic-Review methods. The
medium-risk `resolve-operation-failures` Skill owns one-operation Failure
grouping, evidence-driven diagnosis, controlled HTTP Probe guidance, Worklist
method, and delegation of a confirmed Parameter repair to an authorized child
Profile that selects `build-parameter-patch`. The parent Skill deliberately has
no direct Patch-generation Tool. A generic child completion remains advice
until a future deterministic bridge compiles, samples, reviews, and registers a
real candidate.

Each standard `SKILL.md` frontmatter contains only `name` and `description`,
while `restscope.yaml` declares version, risk, required Tools, and bounded
Context Sources. `restscope.skills` automatically discovers these packaged
files and exposes an immutable built-in Catalog; callers may add test
definitions but cannot replace a built-in. The transitional specialized Patch
Agent reads `build-parameter-patch`'s proposal Reference through that Catalog
without offering `file.read` to its model. No production generic Profile
selects either complete Skill yet, so the specialized Failure Resolution and
Patch/Review runtime below remains unchanged.

The same generic `Agent` class runs the reusable Main Agent and task-scoped
Subagents. A child receives its own Profile and objective, never its parent's
conversation. The global `subagent.start`, `subagent.wait`, and
`subagent.cancel` Tools provide asynchronous direct-child control. The tree
shares only in-memory slots, cooperative cancellation, tracing relationships,
and weighted model budget. No Profile, task, queue, transcript, budget, or
compacted history is persisted.

A Profile may also select the paired `plan.read` and `plan.update` Tools. The
Harness gives each selected Main Agent or Subagent a separate session-memory
Plan containing an optional update explanation and up to 100 ordered
`pending`, `in_progress`, or `completed` steps. At most one step may be active.
The Plan is neither shared nor persisted and does not replace the richer
Failure Resolution Worklist.

Provider calls are routed through `LLMClient`; providers normalize responses but
do not execute tools or write database rows.

Every direct LLM decision constructs messages through `restscope.context`.
Workflow adapters first select the domain facts that matter, then
`CompactTextWriter` safely renders untrusted API, Memory, tool, and sample
values as bounded, readable Markdown cards. Scalars use normal JSON notation,
nested values remain nested, and missing values are distinct from null and
empty containers. Bounded HTTP request/response evidence is the sole JSON
prompt exception and is rendered inside a safe Markdown fence so a complete
test case stays easy to inspect.
`AgentContext` preserves complete tool exchanges and newest validation feedback
inside the Profile model window. The generic Agent's private Prompt Session
keeps stable system/developer guidance, sends only changed Context replacements
after their first full value, and reserves the immutable Tool and output
schemas. It compacts at 80% using the same model with Tools disabled, then
re-anchors every current Context Source but not reloadable Skill bodies; two
invalid summaries fail safely without deleting history. The transitional
Failure Resolution flow still uses its existing nested FAST Compact Agent until
that named Agent is migrated. Strict Agent outputs and provider tool protocols
remain JSON.

## Local live run observer

RESTScope can host a read-only React/Ant Design page for the current App run.
It is disabled by default and is independent of Phoenix. Install the optional
server dependencies and enable it in the worktree's local `.env`:

```bash
uv sync --extra ui
```

```env
UI_ENABLED=true
UI_PORT=8765
```

The host is fixed to `127.0.0.1`; remote binding and viewer authentication are
not available. After App construction, `app.ui_url` is the actual page URL or
`None` when hosting is disabled or could not start. Missing optional packages,
an occupied port, a disconnected viewer, or observation errors do not change a
test result.

The page shows a read-only Codex-style document conversation for the one generic
Agent explicitly marked `lifecycle=main`. A transitional run without that
identity shows “此运行未启动 Main Agent”; legacy workflow Agents are never
silently promoted. The conversation has no profile or message-type heading and
uses the complete page width. Incremental System, Developer, User, and ordinary
Assistant text is rendered directly as prose without `User Task`, `Commentary`,
or `Final Answer` annotations.

Provider Reasoning appears immediately before its response, expanded by default
as muted synthetic-oblique text on the same left edge as ordinary prose. It has
no icon, title, chevron, or copy action: clicking the text collapses it to an
ellipsis, which can be clicked to reopen the complete already-redacted value.
Assistant Tool Call messages and Tool Result messages are not repeated as prose.
Ordinary Tools appear as compact no-chevron rows that are collapsed by default
and open their complete detail in place. Subagent lifecycle calls are aggregated
by child session and display the child Profile name instead of protocol names;
clicking opens the child's same-style conversation in a focus-trapped Drawer
with navigation through at most three levels. Smoke Batches and other run
notifications remain in schema-v2 but do not enter the conversation.

The floating page state is Todo, sourced only from a successful `plan.update`
owned by the explicit Main Agent. It shows completed/total counts, explanation,
and every generic Plan step in a focus-trapped right Drawer. The in-progress
item is evident from its row status and is not repeated in a “当前” summary.
Failure Resolution's domain-specific Worklist is never promoted to Todo; its
read/write calls remain ordinary collapsed Tool detail. Historical Todo state
is labeled read only. Search, status filtering, detail expansion, copying,
theme switching, and auto-follow are viewer-only; the service exposes no
mutation route.

Observer data lives only in the RESTScope process and browser memory. A new run
replaces the previous run, and App shutdown clears it. Details are deliberately
not evicted, so long runs can consume enough memory to slow or terminate the
process. The page applies the same exact-value Redactor as tracing: configured
THINK, FAST, and Phoenix API key values are replaced, while ordinary target
Authorization, Cookie, and business fields remain visible. Use the loopback
page as a developer diagnostic surface, not a credential boundary.

Interrupting the blocking Main loop and closing the App are separate lifecycle
events. A `KeyboardInterrupt` such as Ctrl-C marks the observed Main lifetime
as `stopped` and re-raises, while the App, UI server, complete event snapshot,
and latest Todo remain available until explicit close:

```python
app = RESTScopeApp.from_environment()
try:
    app.initialize(
        schema_source={"kind": "file", "path": "openapi.yaml"},
        base_url="http://127.0.0.1:8000",
    )
    try:
        app.start()
    except KeyboardInterrupt:
        print(f"Main Agent stopped; observer retained at {app.ui_url}")
        input("Press Enter to close RESTScope and its observer...")
finally:
    app.close()
```

This is process-local interruption, not a remote control interface: the page
remains GET-only and offers no stop, pause, retry, or mutation action.
`app.close()` remains the only operation that shuts down the App-owned UI and
clears its memory. A Main loop can start only once per App.

## Local trace monitoring with Phoenix

Phoenix tracing is optional and disabled by default. Install the tracing extra,
start the loopback-only Phoenix service, and enable tracing in the worktree's
local `.env`:

```bash
uv sync --extra tracing
docker compose -f compose.phoenix.yaml up -d
```

```env
TRACING_ENABLED=true
PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006
PHOENIX_PROJECT_NAME=restscope
PHOENIX_API_KEY=
PHOENIX_PROTOCOL=http/protobuf
TRACING_BATCH=true
TRACING_MAX_CONTENT_BYTES=65536
TRACING_FLUSH_TIMEOUT_SECONDS=5
```

Open [http://localhost:6006](http://localhost:6006) to inspect traces. RESTScope
records App, Agent, LLM, and tool spans. Trace inputs and outputs preserve
generated parameter values, but sensitive request headers such as
Authorization, Cookie, API key, token, secret, and CSRF headers are represented
only as `[redacted]`. Exact configured THINK, FAST, and Phoenix API key values
are also replaced wherever they appear. Provider-private tool-call context is
not projected into traces; model-visible reasoning remains visible when it is
part of a recorded message.

Agent, Tool, and chain inputs and outputs are indented JSON. The App and Main
Agent root spans contain bounded lifecycle summaries; Tool details stay on
their child spans. Manual `LLMClient.invoke` spans use OpenInference message
attributes, so Phoenix renders system, user, and assistant messages separately;
their generic input and output values contain only readable summaries and parsed
JSON. Oversized content is truncated to a structured JSON preview at the
configured byte limit. The OpenAI SDK is not auto-instrumented.

Tracing is fail-open: missing optional packages, exporter failures, or shutdown
timeouts do not change RESTScope results. Stop Phoenix without deleting its
named SQLite volume with:

```bash
docker compose -f compose.phoenix.yaml down
```

The compose service disables Phoenix analytics, external UI resources, and its
built-in MCP server. It does not enable authentication and is intended only for
local development on `127.0.0.1`. Traces intentionally include generated test
data, non-sensitive tool parameters, and model-visible reasoning, so anyone
with local Phoenix access can inspect those values even though target
credentials are redacted.

## Phoenix Evals for Operation Smoke Agents

The developer-only [`evaluations/`](evaluations/README.md) directory evaluates
the continuous Failure Resolution flow with one native Phoenix Dataset,
Experiments, and independent code evaluators. Repository YAML Scenarios cover
semantic merge, semantic split, and a real nested Parameter Patch/Review flow.
Each repetition receives fresh session registries and a storage-free finalizer.
Experiment runs call the configured model and record linked traces, but never
open the RESTScope database or request a target API. This is LLM evaluation,
not part of the runtime test suite.

```bash
uv sync --group evaluation
uv run --group evaluation python -m evaluations list
uv run --group evaluation python -m evaluations run resolution \
  --scenario resolution-patch-bounded-identifier --prompt current \
  --repetitions 1 --seed 0
```

Use one repetition while exploring and three when comparing complete prompt
variants. Semantic scores are intentionally independent; there is no aggregate
pass/fail and no LLM Judge.

## MCP Tools

RESTScope retains a generic lightweight MCP Host for caller-owned integrations.
Set an optional server config file explicitly:

```env
MCP_SERVERS_FILE=/path/to/mcp.servers.json
```

RESTScope does not bundle an MCP server or default server configuration. A
caller can provide any compatible stdio server:

```json
{
  "mcpServers": {
    "example": {
      "command": "/path/to/example-mcp",
      "args": ["--stdio"],
      "env": {}
    }
  }
}
```

Build a standalone Harness by letting RESTScope start the MCP server, run
`tools/list`, and place the discovered definitions in a separate external Tool
Catalog and toolbox:

```python
from restscope.harness import build_harness_with_mcp_host

runtime = build_harness_with_mcp_host(
    config="/path/to/mcp.servers.json",
    server_names=("example",),
)
```

Lower-level embedding remains possible through `build_harness(...)` when a
caller already has discovered tools and a call bridge. Explicit sources are
registered in mapping order. The external Catalog remains isolated and is never
automatically injected into an Agent; an Agent Profile must select every Tool
name explicitly. Calling `build_harness()` without sources creates the shared
target HTTP implementation and no external model-visible toolbox.

`MCPToolAdapter` preserves MCP input and optional output contracts but does not
translate annotations into a hidden permission policy. The Agent Profile decides
which discovered Tools, if any, the Harness may bind.

## Operation Smoke testing

Every default `RESTScopeApp` runtime includes the Operation Smoke testing path:

There are no model-callable Testing or Generator-configuration tools.
`OperationSmokeCoordinator` reaches complete batch execution through the narrower
internal `SmokeBatchRunner` interface, so other Agent roles cannot bypass
Smoke's round ordering, budgets, shared seed, or direct Patch transaction.

During the first successful `RESTScopeApp.initialize()`, every OpenAPI operation
becomes an App-lifetime request snapshot. Each parameter, body, media type,
property, array item, and composition branch has a deterministic
`input_node_id` and exactly one current generator row. Method, path,
serialization rules, media type, and enabled or disabled state remain derived
from the in-memory OpenAPI representation instead of being copied into the
database. The complete normalized OpenAPI document is persisted once as the
current audit document. One default App owns one fresh database and one
initialization; starting another App against the retained file is rejected.
Testing another API requires a new database URL or an explicit operational
deletion of the old run artifact; there is no runtime reset or delete tool.

Initial generators treat the OpenAPI document as the source for their defaults.
For concrete values the precedence is `enum`, `const`, `default`, then
`example`; a non-empty enum becomes an equal-weight choice containing every
declared value. Later Generator changes can enter only through a validated,
directly accepted Patch and may deliberately generate values that do not match
the initialized Schema. Required and structural nodes must still use inclusion
probability `1.0`, and every generated case must still serialize under the
parameter and request-body contract before any request is sent. An accepted
Patch clears recoverable default-generation failures attributed to the nodes
it updates and enables the operation once no blocking reason remains.

The internal Smoke batch runner accepts an initialized Catalog `operation_key`
such as `POST /orders`. It combines the App-lifetime operation snapshot with
the current input Generator rows and reads the operation's current Constraints
from the database before every complete Batch. It generates all requested
cases in preflight and only then sends requests serially to the current
App-bound target. It supports at most 20 cases, does not follow redirects or
retry, and creates an isolated HTTP client per case. When the default API
Behavior Monitor is present, it reads at most 1 MiB from each response before
returning. Every first `operation + exact status + normalized media type`
observation is compared with the current OpenAPI representation. A real
contract change updates the in-memory response, the complete current audit
document, and one append-only change event in one critical section. A database
failure restores the in-memory response and Tracker state. Invalid or
truncated JSON stays pending for the next matching response and writes no
event.

Only valid 2xx JSON bodies continue into Resource Identifier and Response Value
tracking. Batch execution returns concrete Test Cases instead of building a
parallel report. Every attempted case enters one run-local `TestCaseCatalog`
with its actually sent inputs as structured `path`, `query`, `header`,
`cookie`, and optional `body` JSON. Direct JSON keys such as `sort` remain
distinct from the unique cross-tool handle `query.sort`. Successful responses
keep no body. A 4xx/5xx response keeps a decoded body up to 10 MiB plus its
separately normalized Failure; redirects and transport errors keep only bounded
Failure facts. The Catalog is released when the operation's Smoke run ends and
is never persisted.

`restscope.http.request` is a high-risk, non-read-only model capability that
can trigger side effects on the bound target. Failure Resolution receives a further
operation-scoped wrapper around it. Generated batch execution remains internal
to Operation Smoke and can execute only an initialized operation using its
complete current Generator and Constraint configuration.
The raw HTTP result includes all response headers, including authentication and
Cookie headers, plus its bounded JSON or text body.

The product entry path is `RESTScopeApp.start → Agent.start → model/Tool loop`.
The first Main Profile grants only `plan.read` and `plan.update`; existing Smoke,
Failure Resolution, and Patch Modules remain transitional internal capabilities
and are not selected by that Profile. The default App does not start MCP
processes.

## API Behavior Monitor Coordinator

Every default `RESTScopeApp` includes one synchronous API Behavior Monitor. The
lightweight testing path supplies its already-known operation key. The
open-world `restscope.http.request` contract remains `method + path`; after the
response, a deterministic matcher resolves the concrete path to exactly one
OpenAPI operation. An ambiguous or missing match adds a structured warning to
the original HTTP result and does not write evidence.

The Monitor coordinates three bounded responsibilities:

- Response Contract checks every first exact status/media observation. A real
  change updates the current App's OpenAPI representation and its durable audit
  document/event atomically.
- Resource Identifier reuses the exact-`id` heuristic and bounded FAST
  classification. Learned selectors, typed identifiers, resource aliases,
  operation usage, and errors remain in the App database.
- Response Value registers a stable value pool when Operation Smoke selects a
  system-provided `response_value` option. Candidate producer fields come from
  the latest IR; exact normalized names are selected locally and an optional
  bounded FAST choice handles semantic names such as `commitId` and `sha`.
  Every valid, non-truncated 2xx JSON response contributes flattened scalar
  evidence. The latest 100 observations per operation and the 100 most recently
  active values per pool are retained, allowing a later monitor registration
  to backfill a deduplicated typed value pool. A response with more than 1000
  valid scalars is skipped in full and returns a structured warning; partial
  evidence is never written.

A learned Resource Identifier selector that previously produced an identifier
but is later missing reports `expected_resource_id_missing`; it is not silently
relearned. Distinct typed Resource Identifiers are retained without capacity
eviction. Raw response bodies, LLM reasoning, and response-contract pending
state are never persisted. The current normalized OpenAPI and append-only
contract change events are the audit exception; bounded flattened response
scalar evidence is another narrow exception, and all non-null scalar fields,
including sensitive-looking names, may be retained. The public read-only
Capability exposes `resource.list_resources`, `resource.list_ids`, and
`openapi.find_observed_response_fields`. Only Parameter Patch receives these
tools, and only for its short-lived proposal session. Response Value pools are
read without registration while a candidate is sampled; applying the selected
Patch performs the first producer-to-consumer pool registration.

## Operation Smoke workflow

`OperationSmokeCoordinator` owns deterministic Batch ordering around one
continuous `FailureResolutionAgent` session:

1. A complete generated Batch establishes the current evidence and 2xx rate.
   The App-wide `RANDOM_SEED` is reused by Batch inputs, Constraint solving, and
   candidate samples. Every sent case enters the run-local Test Case Catalog.
2. Runtime folds completely identical Failure messages and assigns deterministic
   `E*` references while retaining every original `E* → TC*` association. The
   initial Agent prompt contains only the operation key, exact messages, and
   those associations. Semantic Parameter handles, OpenAPI details, Test Cases,
   Memory, and Generator state are available only through tools.
3. The Agent creates and maintains one revisioned, reference-only worklist. It
   may freely merge, split, overlap, reorder, reopen, or leave items undecided;
   `active_item_id` expresses its own investigation order. A whole-list write is
   accepted atomically only when its expected revision and every `E*`, `TC*`,
   `P*`, and Parameter handle are real. The harness does not judge the Agent's
   grouping, diagnosis, progress text, or decision quality.
4. OpenAPI and Test Case tools provide bounded exact evidence. Failure messages
   are already in the initial prompt, so Resolution has no duplicate
   Failure-message lookup. If a message is unclear, it can list candidate paths
   for that HTTP status with `openapi.list_response_fields`, then inspect one
   associated failed response with `test_case.get_response_field_value`.
   Parameter Memory is read only and queried on demand. The operation-scoped
   HTTP Probe supports GET, HEAD, OPTIONS, POST, PUT, PATCH, and DELETE; every
   invocation sends a fresh request and creates a fresh `TC*`, even when the
   call repeats or can mutate the target. An exact reproduced Failure message
   becomes optional evidence for its existing `E*`, but does not enlarge
   initial coverage.
5. `generate_parameter_patch` creates a fresh production Patch/Review flow for
   the active item. Deterministic code checks scope, Schema, references,
   Constraints, compilation, and samples; a separate Review Agent sees only
   normalized requirement and candidate facts. The complete reviewed object is
   held exclusively in a session registry. Resolution receives a short `P*`
   plus a bounded summary and can recover that summary with
   `parameter_patch.read_candidate`; executable DTOs and sample values never
   enter the worklist.
6. When the Agent returns `FailureResolutionFinish`, the harness requires every
   original `(E*, TC*)` association to appear at least once, then mechanically
   validates only decided items. `no_patch` derives real source messages and
   input node IDs from registries. `apply_patch` dereferences its selected `P*`;
   Agent text cannot alter Generator/Constraint changes, samples, affected
   inputs, or candidate provenance. Selected candidates must be unique,
   baseline-current, non-overlapping, compilable, and freshly sampleable as one
   combined state.
7. All decided stable Failures, terminal Attempts, compatible selected
   candidates, and per-candidate change events commit in one database
   transaction. Any validation, optimistic-state, or write failure returns to
   the same Agent session without a partial write. Items without a decision,
   unselected candidates, worklist drafts, progress, and conversation history
   disappear when the session ends.

If no Patch was applied, Smoke stops with `no_patch_applied`. Otherwise the next
complete Batch measures all applied changes together, and
`success_rate_reached` stops at the configured threshold (80% by default).
There is no Effect Agent, candidate Batch, rollback snapshot, fixed Failure todo
queue, or permanent `resolved` flag.

Resolution, Compact, Parameter Patch, and Review share one hard limit of 1000
model outputs for the entire Operation Smoke run. There are no smaller per-Agent
budgets, repeated-output fingerprints, or repeated-tool stopping rules. From
the eleventh non-terminal output for the current active item, the harness adds a
budget reminder; the Agent still owns whether to gather more evidence, record
`no_patch`, form a candidate, or switch items. Hitting the hard limit returns
`failure_resolution_limit_exceeded` and writes none of the unfinalized session.

The database keeps stable structured Failures, append-only terminal Attempts,
current input Generators and Constraints, and append-only deterministic change
events. Rejected or unselected candidates, worklists, Patch samples, raw
Batches/responses, HTTP transcripts, and LLM transcripts are not persisted.
Public results contain Batch run IDs plus bounded Resolution item and Generator
change-event summaries; request/response reports are intentionally absent.

Reference-backed generators fail closed. Empty pools are never exposed as
candidate options and therefore cannot create a reference-backed Generator.
If an existing reference Generator nevertheless points to an empty pool, that
is an `operation_error`, not a wait state. The default API Behavior Monitor
adapter resolves both persistent Resource Identifier and Response Value pools.
Its ambiguous identifier and semantic producer-field decisions use the same
task-focused boundary with request-local `G*/I*` and `P*/S*` aliases.
Deterministic exact matches do not call the model.

## Program Startup

`RESTScopeApp` is the Python entrypoint for the standalone runtime. It loads
configuration, binds one API target, then starts one blocking generic Main
Agent. Startup has no task argument and returns no result DTO:

```python
from restscope import RESTScopeApp

with RESTScopeApp.from_environment() as app:
    app.initialize(
        schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
        base_url="http://localhost:8000",
        headers={"Authorization": "Bearer ..."},
    )
    app.start()
```

`app.start()` blocks until the Main Agent returns its internal bounded
`AgentCompletion`, is interrupted, or fails safely. The initial production
Profile intentionally has only the private Plan pair: no testing Skills,
OpenAPI discovery Tool, HTTP Tool, Context Source, or child Profile is yet
authorized. It therefore reports the missing capability instead of testing the
target. Later capability work must still obtain authorization before any live
external action.

App construction prepares the database before building the default capability
and LLM runtimes. If construction fails, RESTScope removes only the SQLite file
and sidecars created by that attempt. A failure during `initialize()` never
deletes or overwrites the retained database. Parse and validation failures that
occur before the OpenAPI catalog is initialized remain retryable on the same
App object.

Initialization validates the file, URL, or inline schema source and parses it
exactly once for the lifetime of the App. The resulting IR and target settings
are bound out-of-band to trusted tool handlers; they are not copied into Harness
state, tool schemas, or model arguments.

The Main Profile currently has no Operation discovery or testing method, so App
startup does not generate or send cases. Smoke still receives one operation key
when an internal focused caller invokes it, and its legacy coordinators retain
their existing stop and persistence contracts while they await Skill/Subagent
migration. No FIFO or retry scheduler is part of the App entrypoint.
