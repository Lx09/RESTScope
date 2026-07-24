# RESTScope

RESTScope is currently a small Python package for parsing Swagger 2.0 and
OpenAPI 3.x specifications into a normalized intermediate representation.

## Configuration

The parser-only package uses a short optional `.env` file:

```env
LOG_LEVEL=INFO
DATA_DIR=./data
# LOG_FILE=./data/logs/restscope.log
MCP_SERVERS_FILE=./mcp.servers.json

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
uv sync --project services/schemathesis-mcp
uv run pytest

cd services/schemathesis-mcp
uv run pytest
uv run ruff check .
```

The two Python projects intentionally keep separate environments and lock files.

## Database

The database stores OpenAPI schema sources, generator configuration, and the
narrow Resource Monitor evidence catalog for the single current API. Domain
services depend on repository and transaction protocols; SQLAlchemy models,
sessions, and adapters remain inside `restscope.db`.

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
rejected, and compensating rollback generator revisions. A schema
source stores either an absolute file path or verbatim JSON/YAML content. Paths
are reread on every load, while parsed IR, raw responses, and test reports are
not persisted:

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
parameter values, target Authorization/Cookie headers, and DeepSeek
`reasoning_content`. Only the exact configured THINK, FAST, and Phoenix API key
values are replaced; oversized content is truncated to the configured byte
limit. Model calls are represented by RESTScope's manual `LLMClient.invoke`
spans; the OpenAI SDK is not auto-instrumented.

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

RESTScope can run as a standalone lightweight MCP Host. The short `.env`
surface only points at a server config file:

```env
MCP_SERVERS_FILE=./mcp.servers.json
```

Put MCP server command and environment details in that JSON file. The default
`mcp.servers.json` runs the internal `schemathesis-mcp` service with Docker.
Build its image from the repository root before using that configuration:

```bash
docker build -t schemathesis-mcp services/schemathesis-mcp
```

For local stdio development without Docker, copy `mcp.servers.example.json`,
which starts the isolated subproject through `uv`:

```json
{
  "mcpServers": {
    "schemathesis": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "services/schemathesis-mcp",
        "schemathesis-mcp"
      ],
      "env": {
        "SCHEMATHESIS_MCP_ALLOWED_PATHS": "./assets:/tmp",
        "SCHEMATHESIS_MCP_ALLOWED_HOSTS": "localhost,127.0.0.1",
        "SCHEMATHESIS_MCP_ARTIFACT_DIR": "./.schemathesis-mcp"
      },
      "cwd": "."
    }
  }
}
```

Build a standalone capability runtime by letting RESTScope start the MCP server,
run `tools/list`, and register the discovered tools through the generic
capability layer:

```python
from restscope.capabilities import build_capabilities_with_mcp_host

runtime = build_capabilities_with_mcp_host(config="./mcp.servers.json")
```

Lower-level embedding remains possible through `build_capabilities(...)` when a
caller already has discovered tools and a call bridge. If the `schemathesis`
source is not configured or provided, preset registration raises
`PresetToolSourceNotFoundError`. To build a runtime without MCP sources, pass
`presets=()`.

`MCPToolAdapter` uses MCP annotations for read-only/risk classification, while
`ToolPolicy` remains the final execution gate.

## Lightweight generated operation tests

Every default `RESTScopeApp` runtime includes a process-local testing path that
does not call Schemathesis MCP:

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
creates an isolated HTTP client per case. When the default Resource Monitor is
present, it reads at most 1 MiB from a 2xx response before returning. A non-2xx
response is also read up to 1 MiB solely to build the batch failure report; it
is never sent to the Resource Monitor and its complete body is not returned or
persisted. The report contains generated/request values, merged target headers,
response metadata, transport errors, Resource Monitor warnings, a first-seen
list of at most 100 exact unique failure messages with case associations, and
`response_validation="not_evaluated"`. Only exact configured THINK, FAST, and
Phoenix API key values are replaced.

Both `restscope.testing.run_operation` and `restscope.http.request` are
high-risk, non-read-only capabilities allowed for every Agent role without a
separate approval gate. Calling either can trigger side effects on the bound
target. The raw HTTP tool remains independent and can issue an arbitrary
target-relative request; the generated testing tool can execute only an
operation stored in the frozen Generator Catalog using its complete persisted
generator configuration.
The raw HTTP result includes all response headers, including authentication and
Cookie headers, plus its bounded JSON or text body.

The default Supervisor uses `OperationSmokeAgent` and this lightweight batch
service. It does not start the Schemathesis MCP service. The older
`OperationTestAgent` and `SchemathesisOperationRunner` remain available only
when a caller explicitly injects the legacy runner path.

## Resource Monitor Agent

Every default `RESTScopeApp` includes a synchronous Resource Monitor for 2xx
JSON responses. The lightweight testing path supplies its already-known
operation key. The open-world `restscope.http.request` contract remains
`method + path`; after the response, a deterministic matcher resolves the
concrete path to exactly one OpenAPI operation. An ambiguous or missing match
adds a structured warning to the original HTTP result and does not write
resource evidence.

For an operation response group seen for the first time, an exact `id` field is
used without a model call. Otherwise the configured FAST model selects whether
the group represents a resource and, if so, its identifier field. The resulting
selector is reused for later 2xx responses. A learned selector that previously
produced an identifier but is later missing reports
`expected_resource_id_missing`; it is not silently relearned.

The catalog persists only canonical resource names and aliases, learned
selectors, typed string/integer identifiers, latest per-operation read/write
usage, and latest monitor errors. It does not store response bodies or model
reasoning. `restscope.resource.lookup` is a read-only local tool that returns
the latest reusable identifiers and the operations that observed them. Lookup
does not automatically modify generators or schedule another test.

## Operation Smoke Agent

`OperationSmokeAgent` runs a bounded generated batch and measures its 2xx
success rate. When the threshold is not met, the shared FAST model is called in
two narrow rounds. The first sees only unique failure messages, failed-case
generated values, omitted input IDs, and temporary evidence IDs; it selects
suspect input nodes. The second sees only that diagnosis and the current
generators for those nodes; it proposes `InputGeneratorPatch` updates.

The patch is stored as a candidate revision and is never validated by replaying
or cloning one failed case. A complete next batch accepts the candidate when it
meets the threshold. Otherwise the candidate is rejected and a compensating
rollback revision restores its parent configuration. Each model round can make
one structured-output repair call.

Reference-backed generators fail closed. A candidate that requires a resource
identifier or monitored response value does not send a batch while its value
pool is empty; the Agent returns `waiting`. The default Resource Monitor
adapter currently resolves persisted resource identifiers. Operation
dependency discovery through `OpenAPIRetrievalAgent` and a generic monitored
response-value catalog are not connected in this iteration.

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

Supervisor orders operations by stable path depth, passes the set of already
successful operation keys into each later Smoke request, and retains every
attempt. A Smoke result waiting on an unavailable reference pool is surfaced as
a blocked operation. Queue and dependency state are not persisted.

## OpenAPI Retrieval Agent

`OpenAPIRetrievalAgent` investigates the App-bound OpenAPI IR for operations
that may produce a consumer parameter value. Its public capability is
`restscope.openapi.retrieve`; the request contains only the retrieval query and
does not accept a file path. Internally it exposes only IR-backed
`openapi.inspect`, `openapi.find_operation`, `openapi.search_symbols`,
`openapi.read_operation`, and `openapi.read_evidence` tools. Symbol searches
scan the current IR directly and do not retain an index or raw-text fallback.
