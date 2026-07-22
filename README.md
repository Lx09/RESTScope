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

The database currently stores only OpenAPI schema sources. Domain code in
`restscope.catalog` depends on repository and transaction protocols; SQLAlchemy
models, sessions, and the protocol adapters remain inside `restscope.db`.

The destructive Alembic baseline is intended for new databases. A schema stores
either an absolute file path or verbatim JSON/YAML content. Paths are reread on
every load, while parsed catalog metadata and operations are not persisted yet:

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
records App, Agent, LLM, and tool spans. Trace content is recursively redacted,
DeepSeek `reasoning_content` is represented only by presence and length, and
oversized inputs or outputs are truncated to the configured byte limit. Model
calls are represented by RESTScope's manual `LLMClient.invoke` spans; the
OpenAI SDK is not auto-instrumented.

Tracing is fail-open: missing optional packages, exporter failures, or shutdown
timeouts do not change RESTScope results. Stop Phoenix without deleting its
named SQLite volume with:

```bash
docker compose -f compose.phoenix.yaml down
```

The compose service disables Phoenix analytics, external UI resources, and its
built-in MCP server. It does not enable authentication and is intended only for
local development on `127.0.0.1`.

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

## Operation Test Agent

OperationTestAgent executes one Schemathesis run for one operation attempt, then
uses the configured Thinking model to identify direct operation dependencies.
There are no smoke/conformance/positive/negative/boundary stages. The runner
reads the baseline schema source, target URL, and runtime headers from its bound
`ToolContext`, then sends them only to Schemathesis `start_run` together with
the method/path filter. Other Schemathesis settings use service defaults.

```python
from restscope.agent import (
    LLMOperationDependencyAnalyzer,
    OperationCandidate,
    OperationReference,
    OperationTestAgent,
    OperationTestRequest,
    SchemathesisOperationRunner,
)
from restscope.capabilities import ToolContext, build_capabilities_with_mcp_host
from restscope.llm import ModelSelector, build_llm_client
from restscope.openapi_parser import OpenAPIParser
from restscope.restscope_config import RESTScopeConfig

config = RESTScopeConfig.from_environment()
runtime = build_capabilities_with_mcp_host(config="./mcp.servers.json")
runtime.tool_executor.bind_context(
    ToolContext(
        ir=OpenAPIParser.parse("assets/openapi/petstore-v3.json"),
        baseline_schema_source={
            "kind": "file",
            "path": "assets/openapi/petstore-v3.json",
        },
        base_url="http://localhost:8000",
        headers={},
    )
)
agent = OperationTestAgent(
    runner=SchemathesisOperationRunner(tool_executor=runtime.tool_executor),
    dependency_analyzer=LLMOperationDependencyAnalyzer(
        client=build_llm_client(config.llm),
        model=ModelSelector.from_config(config.llm).select("operation_dependency_analyzer"),
    ),
)

operation = OperationReference(method="GET", path="/pets", operation_id="listPets")
report = agent.run(
    OperationTestRequest(
        operation=operation,
        candidate_operations=[OperationCandidate(operation=operation)],
        allow_live_testing=True,
    )
)
```

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
    report = app.run(RESTScopeRunRequest(allow_live_testing=True))
```

Initialization validates the file, URL, or inline schema source and parses it
exactly once for the lifetime of the App. The resulting IR and target settings
are bound out-of-band to trusted tool handlers; they are not copied into graph
state, tool schemas, or model arguments.

Supervisor orders operations by stable path depth, retains every attempt, and
retries blocked operations only in later rounds after all direct prerequisites
have produced a passing run with an observed 2xx. Queue and dependency state are
not persisted.

## OpenAPI Retrieval Agent

`OpenAPIRetrievalAgent` investigates the App-bound OpenAPI IR for operations
that may produce a consumer parameter value. Its public capability is
`restscope.openapi.retrieve`; the request contains only the retrieval query and
does not accept a file path. Internally it exposes only IR-backed
`openapi.inspect`, `openapi.find_operation`, `openapi.search_symbols`,
`openapi.read_operation`, and `openapi.read_evidence` tools. Symbol searches
scan the current IR directly and do not retain an index or raw-text fallback.
