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
and response schemas, a `FakeProvider` for offline tests, an OpenAI-compatible
provider, model selection for thinking/fast roles, structured output validation,
and a safe tool-call shell in `restscope.capabilities`.

Provider calls are routed through `LLMClient`; providers normalize responses but
do not execute tools or write database rows.

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
passes only schema, target URL, runtime headers, and the method/path filter;
other Schemathesis settings use service defaults.

```python
from restscope.agent import (
    LLMOperationDependencyAnalyzer,
    OperationCandidate,
    OperationReference,
    OperationTestAgent,
    OperationTestRequest,
    SchemathesisOperationRunner,
)
from restscope.capabilities import build_capabilities_with_mcp_host
from restscope.llm import ModelSelector, build_llm_client
from restscope.restscope_config import RESTScopeConfig

config = RESTScopeConfig.from_environment()
runtime = build_capabilities_with_mcp_host(config="./mcp.servers.json")
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
        schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
        base_url="http://localhost:8000",
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
    report = app.run(
        RESTScopeRunRequest(
            schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
            base_url="http://localhost:8000",
            allow_live_testing=True,
        )
    )
```

Supervisor orders operations by stable path depth, retains every attempt, and
retries blocked operations only in later rounds after all direct prerequisites
have produced a passing run with an observed 2xx. Queue and dependency state are
not persisted.
