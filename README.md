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

THINK_PROVIDER=openai_compatible
THINK_MODEL=glm-4.5-air
THINK_API_KEY=your-api-key
THINK_BASE_URL=https://open.bigmodel.cn/api/paas/v4

FAST_PROVIDER=openai_compatible
FAST_MODEL=glm-4.7-flash
# FAST_PROVIDER, FAST_API_KEY, and FAST_BASE_URL default to THINK_* values
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

## Development

```bash
uv sync
uv run pytest
```

## Database

The database stores OpenAPI schema sources, generator configuration, and the
narrow API Behavior Monitor evidence catalogs for the single current API.
Those catalogs contain Resource Identifier facts and registered Response Value
selectors and typed values; response-contract IR mutations remain in memory.
Domain services depend on repository and transaction protocols; SQLAlchemy
models, sessions, and adapters remain inside `restscope.db`.

Successful App construction leaves its SQLite file in place, including after
`close()`. A later process must use a new `DB_URL` or explicitly inspect and
delete the old run artifact before starting. RESTScope never overwrites or
automatically deletes a successfully created database. A caller that injects a
complete custom `CapabilityRuntime` owns its persistence and bypasses this
default database bootstrap.

The Alembic chain starts with the schema-source baseline, adds operation
generator configuration in revision `0002_create_generator_configs`, and adds
the resource evidence catalog in `0003_create_resource_catalog`. Revision
`0004_create_generator_revision_history` adds immutable candidate, accepted,
rejected, and legacy compensating rollback generator revisions. Current
Operation Smoke candidate finalization does not create rollback lifecycle
rows: a fully valid candidate becomes accepted in place, while partial or
fully rejected candidates create a new accepted revision containing only
validated changes. Revision
`0005_create_response_value_catalog` adds persistent Response Value monitor,
source, and typed-value tables. A schema source stores either an absolute file
path or verbatim JSON/YAML content. Paths are reread on every load, while
parsed/evolved IR, raw responses, and test reports are not persisted:

```python
from restscope import RESTScopeConfig, SchemaSourceInput, build_schema_catalog

config = RESTScopeConfig.from_environment()
catalog = build_schema_catalog(config)
schema = catalog.register(
    SchemaSourceInput(file_path="assets/openapi/petstore-v3.json")
)
parsed = catalog.load(schema.id)
```

## LLM

The MVP LLM layer lives in `restscope.llm`. It provides provider-neutral request
and response schemas, OpenAI-compatible and DeepSeek providers, model selection
for thinking/fast roles, structured output validation, and a safe tool-call shell
in `restscope.capabilities`. Unit tests provide their own local stub providers;
the runtime package does not register an offline fake provider.

Provider calls are routed through `LLMClient`; providers normalize responses but
do not execute tools or write database rows.

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
parameter values and target Authorization/Cookie headers. Only the exact
configured THINK, FAST, and Phoenix API key values are replaced. Provider-private
tool-call context is not projected into traces; model-visible reasoning remains
visible when it is part of a recorded message.

Agent, tool, and chain inputs and outputs are indented JSON. App and Supervisor
root spans contain bounded run summaries, while operation and case details stay
on their child spans. Manual `LLMClient.invoke` spans use OpenInference message
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
local development on `127.0.0.1`. Because traces intentionally include target
credentials, generated test data, complete tool parameters, and model
reasoning, anyone with local Phoenix access can inspect those values.

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

Build a standalone capability runtime by letting RESTScope start the MCP server,
run `tools/list`, and register the discovered tools through the generic
capability layer:

```python
from restscope.capabilities import build_capabilities_with_mcp_host

runtime = build_capabilities_with_mcp_host(
    config="/path/to/mcp.servers.json",
    server_names=("example",),
)
```

Lower-level embedding remains possible through `build_capabilities(...)` when a
caller already has discovered tools and a call bridge. Explicit sources are
registered in mapping order. Calling `build_capabilities()` without sources
builds the local RESTScope tools only.

`MCPToolAdapter` uses MCP annotations for read-only/risk classification, while
`ToolPolicy` remains the final execution gate.

## Operation Smoke testing

Every default `RESTScopeApp` runtime includes the Operation Smoke testing path:

- `restscope.testing.inspect_operation_inputs`
- `restscope.testing.replace_operation_generators`
- `restscope.testing.patch_operation_generators`
- `restscope.testing.run_operation`

The three configuration capabilities are management endpoints registered in
the runtime but excluded from model tool selection by the current policy. The
execution capability is model-visible to every Agent role.

During the first successful `RESTScopeApp.initialize()`, every OpenAPI operation
is frozen into a persistent request snapshot. Each parameter, body, media type,
property, array item, and composition branch has a deterministic
`input_node_id` and exactly one initial generator. The same transaction stores
all enabled or disabled operation records and a singleton Catalog marker. One
default App owns one fresh database and one initialization; starting another
App against the retained file is rejected. Testing another API requires a new
database URL or an explicit operational deletion of the old run artifact;
there is no runtime reset or delete tool.

Initial generators treat the OpenAPI document as the source for their defaults.
For concrete values the precedence is `enum`, `const`, `default`, then
`example`; a non-empty enum becomes an equal-weight choice containing every
declared value. After initialization, a management-side replace or patch is
feedback-owned configuration and may deliberately generate values that do not
match the frozen Schema. Required and structural nodes must still use inclusion
probability `1.0`, and every generated case must still serialize under the
frozen parameter and request-body contract before any request is sent. A patch
clears recoverable default-generation failures attributed to the nodes it
updates and enables the operation once no blocking reason remains.

`run_operation` accepts a frozen Catalog `operation_key` such as
`POST /orders`. It reads method, path, serialization rules, input constraints,
and generators only from that persisted snapshot; it does not compare the
operation with the current `ToolContext.ir`. It generates all requested cases
in preflight and only then sends requests serially to the current App-bound
target. It supports at most 20 cases, does not follow redirects or retry, and
creates an isolated HTTP client per case. When the default API Behavior Monitor
is present, it reads at most 1 MiB from each response before returning. Every
first `operation + exact status + normalized media type` observation is
compared with the current OpenAPI IR. The Monitor can conservatively add an
exact status, media type, optional field, or wider type directly to that
in-memory IR. Invalid or truncated JSON stays pending for the next matching
response; the evolved IR and first-observation registry are not persisted.

Only valid 2xx JSON bodies continue into Resource Identifier and Response Value
tracking. Non-2xx bodies are also reused to build the batch failure report, but
never become reusable input values and are not persisted. The report contains
generated/request values, merged target headers, response metadata, transport
errors, API Behavior Monitor warnings, a first-seen list of at most 100 exact
unique failure messages with case associations, and a `response_validation`
state of `evaluated`, `partial`, or `not_evaluated`. Only exact configured
THINK, FAST, and Phoenix API key values are replaced.

Both `restscope.testing.run_operation` and `restscope.http.request` are
high-risk, non-read-only capabilities allowed for every Agent role without a
separate approval gate. Calling either can trigger side effects on the bound
target. The raw HTTP tool remains independent and can issue an arbitrary
target-relative request; the generated testing tool can execute only an
operation stored in the frozen Generator Catalog using its complete persisted
generator configuration.
The raw HTTP result includes all response headers, including authentication and
Cookie headers, plus its bounded JSON or text body.

The default and only Supervisor execution path is
`Supervisor → OperationSmokeAgent → restscope.testing.run_operation`. The
default App does not start MCP processes.

## API Behavior Monitor Agent

Every default `RESTScopeApp` includes one synchronous API Behavior Monitor. The
lightweight testing path supplies its already-known operation key. The
open-world `restscope.http.request` contract remains `method + path`; after the
response, a deterministic matcher resolves the concrete path to exactly one
OpenAPI operation. An ambiguous or missing match adds a structured warning to
the original HTTP result and does not write evidence.

The Monitor coordinates three bounded responsibilities:

- Response Contract checks every first exact status/media observation and
  evolves only the current App's OpenAPI IR.
- Resource Identifier reuses the exact-`id` heuristic and bounded FAST
  classification. Learned selectors, typed identifiers, resource aliases,
  operation usage, and errors remain in the App database.
- Response Value registers a stable value pool when Operation Smoke selects a
  system-provided `response_value` option. Candidate producer fields come from
  the latest IR; exact normalized names are selected locally and an optional
  bounded FAST choice handles semantic names such as `commitId` and `sha`.
  Every valid, non-truncated 2xx JSON response contributes flattened scalar
  evidence. The latest 100 observations per operation are persisted, allowing
  a later monitor registration to backfill a deduplicated typed value pool.

A learned Resource Identifier selector that previously produced an identifier
but is later missing reports `expected_resource_id_missing`; it is not silently
relearned. Raw response bodies, LLM reasoning, evolved IR snapshots, and
response-contract first-observation state are never persisted. Flattened
response scalar evidence is the narrow exception: all non-null scalar fields,
including sensitive-looking names, may be retained. `restscope.resource.lookup`
remains the explicit read-only lookup tool; Response Value pools are consumed
internally by Operation Smoke.

## Operation Smoke Agent

`OperationSmokeAgent` runs a bounded generated batch and measures its 2xx
success rate. When the threshold is not met,
`OperationSmokeDiagnoser` starts a fresh in-memory Plan & Solve session. A
THINK model first classifies every failure as:

- `ready` when the cause, affected request input, and change direction are
  known;
- `pending` when a falsifiable hypothesis still needs evidence;
- `non-parameter` when changing a generated input cannot solve it; or
- `unplanned` when no bounded investigation is currently available.

The model uses semantic request paths such as `path.projectId`,
`query.filter`, or `body.items[].sku`; internal node IDs stay in code. Failure,
case, and HTTP-observation evidence use request-local `F*`, `C*`, and `O*`
references. Each prompt is rebuilt from typed `PlanState` and
`EvidenceJournal`, not from an accumulating chat transcript.

After the initial tool-free plan, THINK may call a constrained view of
`restscope.http.request`. Each tool round contains at most four serial requests
and can only use the current frozen operation's method and a concrete path
matching its path template. Context authentication is injected by code. Query,
ordinary headers, and JSON/text/form bodies may be varied to test a hypothesis.
Responses pass through the same API Behavior Monitor and can add new `F*` and
`O*` evidence. Tool failures are evidence and are not automatically retried.

One diagnosis allows at most 20 valid plan decisions and 40 HTTP tool rounds;
callers may lower both limits. A malformed model decision gets one free repair.
HTTP tool rounds do not consume the decision budget. When the decision budget
is exhausted, already-ready analyses still proceed to patching while pending
or unplanned work remains visible in the result.

At analysis completion, the FAST model is called once to convert all ready
items into one compatible Generator patch. It must account for every ready
item as covered or deferred and may change each semantic input at most once.
Every concrete change names the covered `I*` items it serves, so code can later
retain or remove that change independently.
Code—not the model—maps semantic paths and observed-value `R*` aliases to real
input nodes and pools, compiles generator intents such as `sample_values`,
`integer_between`, or `observed_value`, and enforces required inputs,
serialization, and Generator validity. The final output gets one repair
outside the THINK decision budget.

The diagnosis input includes generated values, omitted inputs, bounded failure
response bodies, monitor summaries, and the previous candidate experiment
summary. Full Schema, config revisions, internal node IDs, prepared target
headers, ToolContext Authorization/Cookie values, and observed pool values are
not sent to the model. Private response bodies and the in-memory plan do not
enter the public testing report, LangGraph state, or the database.

The patch is stored as a candidate revision and exercised by one complete
same-seed batch; RESTScope never validates by replaying or cloning one failed
case. A separate THINK Patch Validation call then classifies each covered item
as `resolved`, `persisting`, or `unknown` from its cause, proposed solution,
current `F*/C*` evidence, and changed-input coverage. This validation has one
repair, cannot call HTTP tools, and does not consume the Plan & Solve decision
budget.

When the candidate reaches the 2xx success threshold, the entire candidate is
accepted even if Patch Validation still reports a persisting item. Below the
threshold, changes owned by at least one resolved item are accepted
immediately; changes owned only by persisting or unknown items are removed.
Shared changes are retained when any owning item is resolved. The next patch is
staged from this newly accepted revision, so earlier effective fixes accumulate.
Partial acceptance deliberately does not run an extra stabilization batch. The
next PlanState receives a compact experiment summary that marks accepted and
removed changes and warns that the candidate evidence included the complete
experimental patch. The final feedback round still validates and finalizes its
candidate, but performs no further diagnosis or patch and never rolls back the
whole accumulated configuration.

Reference-backed generators fail closed. Empty pools are never exposed as
candidate options and therefore cannot create a reference-backed Generator.
If an existing reference Generator nevertheless points to an empty pool, that
is an `operation_error`, not a wait state. The default API Behavior Monitor
adapter resolves both persistent Resource Identifier and Response Value pools.
Its ambiguous identifier and semantic producer-field decisions use the same
task-focused boundary with request-local `G*/I*` and `P*/S*` aliases.
Deterministic exact matches do not call the model.

## Program Startup

`RESTScopeApp` is the Python API entrypoint for the standalone runtime. It loads
RESTScope config, builds the capability and Thinking-model runtimes, discovers
all schema operations, and schedules them through round-based FIFO queues:

```python
from restscope import RESTScopeApp, RESTScopeRunRequest

with RESTScopeApp.from_environment() as app:
    app.initialize(
        schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
        base_url="http://localhost:8000",
        headers={"Authorization": "Bearer ..."},
    )
    report = app.run(RESTScopeRunRequest())
```

`RESTScopeApp.run()` is an execution API, not a dry-run API. The default Smoke
Agent immediately sends generated requests to the target bound during
`initialize()`, including operations that may have side effects. Run it only
against a target you are authorized to test.

App construction prepares the database before building the default capability
and LLM runtimes. If construction fails, RESTScope removes only the SQLite file
and sidecars created by that attempt. A failure during `initialize()` does not
remove the database and remains retryable on the same App object.

Initialization validates the file, URL, or inline schema source and parses it
exactly once for the lifetime of the App. The resulting IR and target settings
are bound out-of-band to trusted tool handlers; they are not copied into graph
state, tool schemas, or model arguments.

Supervisor orders operations by stable path depth and retains every attempt.
Smoke receives only the target operation key. A local `retry` or
`operation_error` is scheduled in a later round so other operations can add
global pool evidence first; the default is at most three attempts per
operation. Unsupported operations are recorded without retry. Local failures
do not interrupt later operations, and a completed run with any final failures
returns `failed/completed_with_failures`. Only shared setup, database, or
runtime failures stop immediately as `errored/technical_error`. Queue and retry
state are not persisted.
