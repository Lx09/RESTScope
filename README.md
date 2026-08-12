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

RESTScope requires Python 3.12 or newer. With `uv` installed, the repository's
`.python-version` selects a compatible Python 3.12 interpreter automatically:

```bash
uv sync
uv run pytest
```

## Database

The database is one audit artifact for one App. It contains exactly 13 business
tables: the complete current normalized OpenAPI and response-change events plus
narrow Resource Identifier and bounded Response Value evidence. Request
Generation state is revisioned App-lifetime memory. The database never stores
Generators, Constraints, Patches, Failures, Batches, model conversations,
plans, queues, scheduler progress, or authentication material.

Successful App construction leaves its SQLite file in place, including after
`close()`. A later process must use a new `DB_URL` or explicitly inspect and
delete the old run artifact before starting. RESTScope never overwrites or
automatically deletes a successfully created database. A caller that injects a
concrete `HarnessRuntime` created through `build_harness()` owns its persistence
and bypasses this default database bootstrap.

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
and response schemas, OpenAI-compatible and DeepSeek providers, directly named
model configurations, and structured output validation. `restscope.agent`
defines explicit Profiles;
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
an offline fake provider. `run_system_agent(profile_name, task)` uses that same
resolution path for repeatable synchronous System Agent roots, but only for
Profiles registered with a Harness-owned result contract. Each call gets an
isolated prompt session and Agent tree and is closed after its validated result.

The project currently ships two built-in standard Skills. The medium-risk
`apply-parameter-patch` Skill reads current generation state, builds a complete
Generator/Constraint replacement, validates and samples it, performs value-level
semantic review, atomically applies it, and confirms the new revision. The
medium-risk `resolve-operation-failures` Skill owns evidence-driven diagnosis
for one operation and delegates a confirmed Parameter repair to an authorized
child Profile that selects `apply-parameter-patch`. The parent confirms the
applied revision from the Store and uses a later complete Batch to measure the
real target effect.

Each standard `SKILL.md` frontmatter contains only `name` and `description`,
while `restscope.yaml` declares version, risk, required Tools, and bounded
Context Sources. `restscope.skills` automatically discovers these packaged
files and exposes an immutable built-in Catalog; callers may add test
definitions but cannot replace a built-in. No production Profile selects these
Skills yet: the initial Main Profile still grants only its private Plan pair.
The retired specialized Failure Resolution, Patch, Review, and Compact Agents
are not runtime fallbacks.

The same generic `Agent` class runs the reusable Main Agent, task-scoped
Subagents, and deterministic-caller-started System Agents. A child receives its
own Profile and objective, never its parent's
conversation. The global `subagent.start`, `subagent.wait`, and
`subagent.cancel` Tools provide asynchronous direct-child control. The tree
shares only in-memory slots, cooperative cancellation, tracing relationships,
and weighted model budget. A System Agent instead receives a fresh unbounded
usage accountant: usage is still recorded, but token spend never ends its
correction loop. Provider failure, cancellation, shutdown, or safe compaction
failure can still terminate it. No Profile, task, queue, transcript, budget, or
compacted history is persisted.

A Profile may also select the paired `plan.read` and `plan.update` Tools. The
Harness gives each selected Main Agent, Subagent, or System Agent a separate
session-memory
Plan containing an optional update explanation and up to 100 ordered
`pending`, `in_progress`, or `completed` steps. At most one step may be active.
The Plan is neither shared nor persisted.

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
invalid summaries fail safely without deleting history. Strict Agent outputs
and provider tool protocols remain JSON.

The API Behavior Monitor's ambiguous resource-identifier and response-source
choices run through two `fast` System Agent Profiles. Stable judgment rules live
in Profile instructions and each call supplies only bounded dynamic candidates.
The result Schema is narrowed to that call's `I*` or `S*` aliases. The Harness
returns specific bounded validation feedback for malformed or unauthorized
output and keeps asking in the same session without an attempt limit. The
Monitor updates state only after Schema and local candidate validation pass.

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
with navigation through at most three levels. System Agents triggered while an
HTTP Tool is running remain independent root sessions, but appear as named,
status-labelled rows inside that Tool card. A focus-trapped Drawer shows their
complete conversation and any nested Tools. Schema-v3 contains only Agent-turn
and ordinary Tool-call events; Batch and Patch Apply therefore render as Tool
cards. Older browser snapshots are ignored.

The floating page state is Todo, sourced only from a successful `plan.update`
owned by the explicit Main Agent. It shows completed/total counts, explanation,
and every generic Plan step in a focus-trapped right Drawer. The in-progress
item is evident from its row status and is not repeated in a “当前” summary.
Historical Todo state is labeled read only. Search, status filtering, detail
expansion, copying, theme switching, and auto-follow are viewer-only; the
service exposes no mutation route.

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

## Generic request generation and Batch Tools

During `RESTScopeApp.initialize()`, every OpenAPI operation becomes an
App-lifetime request-generation snapshot. The in-memory
`RequestGenerationConfigStore` starts each operation at revision `0` with its
default Generator and complete active Constraint state. It exposes snapshots
by semantic input handle; internal node IDs never cross the Tool boundary.

`openapi.list_operations` discovers the initialized operations.
`request_generation.get_input_state` returns the complete directly and
transitively intersecting state for selected inputs.
`request_generation.validate_patch` compiles a full replacement, validates
schema and reference compatibility, solves Constraints, generates deterministic
samples, and returns a validation digest without changing state.
`parameter_patch.apply` revalidates the exact request under the operation lock,
stages complete response-value pool replacements, publishes the new Generation
State, and commits both as one visible change. A failed commit restores the old
state before unlocking; a stale application changes nothing.

`test_case.run_batch` freezes one complete generation revision and every named
reference pool before it
preflights and executes 1–5 cases. The result contains bounded inline canonical
requests and HTTP or transport outcomes plus the frozen revision. It creates no
`TC*`, `E*`, Test Case registry, Failure memory, candidate, or database row.
An already-running Batch is unaffected by a later Patch; a later Batch sees the
new revision. Batch execution can mutate the target API and does not retry,
follow redirects, or roll back effects.

These capabilities are bound by the production Harness but are not thereby
authorized to an Agent. The initial Main Profile still grants only
`plan.read` and `plan.update`; it selects neither testing Skill and cannot call
the new Tools until a later Profile decision explicitly grants them. The
default App does not start MCP processes.

## API Behavior Monitor Coordinator

Every default `RESTScopeApp` includes one synchronous API Behavior Monitor. The
lightweight testing path supplies its already-known operation key. The
open-world `restscope.http.request` contract remains `method + path`; after the
response, a deterministic matcher resolves the concrete path to exactly one
OpenAPI operation. An ambiguous or missing match adds a structured warning to
the original HTTP result and does not write evidence.

The Monitor coordinates three ordered responsibilities:

- Response Contract checks every first exact status/media observation. A real
  change updates the current App's OpenAPI representation and its durable audit
  document/event atomically.
- Observation persistence accepts complete valid 2xx JSON responses after
  sensitive request headers are removed. It keeps the original response text
  and latest 100 observations per operation; there is no flattened-scalar or
  per-response JSON-size limit.
- Resource Monitor reuses unambiguous known identity fields or asks the bounded
  FAST Resource Identifier System Agent for a new direct field combination. It
  then stores operation roles and recursively merged current instance state.
  DELETE observations mark instances logically deleted. Extraction rules and
  model reasoning are not persisted.

`parameter_patch.apply` records one exact consumer input source using producer
operation, concrete successful status, normalized media type, selector, and
field name. Both RESOURCE and VALUE_REUSE meanings begin with a neutral
Beta(1,1) prior. No shared response-value pool exists: VALUE_REUSE parses typed
scalars from matching retained observations when needed, while RESOURCE reads
complete non-deleted instances and keeps composite identity fields correlated.
Batch preflight records or reuses one immutable abstract Generator/Constraint
snapshot before the first request, and successful generated observations point
to it.

The public read-only Tool Backend exposes `resource.list_resources`,
`resource.list_ids`, and `openapi.find_observed_response_fields`. The last Tool
discovers scalar selectors directly from raw observations without returning
their values. A source transaction stages exact producer-to-consumer rows,
publishes matching in-memory Store state, and restores the old Store revision
if the database commit fails.

## Failure Resolution and Parameter Patch workflow

The standard Skills define the future generic parent/child method without
granting it to the current Main Profile:

1. A parent using `resolve-operation-failures` receives bounded inline Batch
   evidence for one operation, separates Parameter from non-Parameter causes,
   and establishes a root cause, atomic value predicates, and the minimum
   complete affected-input scope.
2. It delegates the bounded repair to one authorized child Profile that
   explicitly selects `apply-parameter-patch`. The parent does not generate,
   rewrite, or apply the Patch itself.
3. The child reads the current revision and complete intersecting Constraint
   closure, constructs a full replacement, and calls
   `request_generation.validate_patch`.
4. The child reviews every value predicate against the final Generator domain,
   inclusion and variant rules, reference-source identity and non-emptiness,
   full Constraint closure, and sample witnesses. Compilation, finite samples,
   HTTP success, or a missing Failure is never a substitute for that proof.
5. Only the exact reviewed Patch and validation digest may be sent to
   `parameter_patch.apply`. A conflict requires a fresh state read and a new
   validation; it must not blindly replay the stale request.
6. After success, the child reads the state again and reports the applied
   revision and digest. The parent independently repeats that state read before
   trusting the completion, then runs a new complete Batch to measure the target
   effect.

Apply changes only future RESTScope request generation. It does not send HTTP,
prove that the API Failure is fixed, create a candidate `P*`, or persist Patch
history. A later Batch failure does not roll back the Patch; it becomes new
evidence for another explicit diagnosis and replacement.

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
