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
uv run pytest
```

## Database

The MVP database layer lives in `restscope.db` and provides SQLAlchemy ORM
mappings, repositories, a UnitOfWork transaction boundary, and packaged Alembic
migrations for the MVP tables.

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

Put MCP server command and environment details in that JSON file. The first
supported preset is `schemathesis`:

```json
{
  "mcpServers": {
    "schemathesis": {
      "command": "/Users/lixin/Workplace/schemathesis-mcp/.venv/bin/schemathesis-mcp",
      "env": {
        "SCHEMATHESIS_MCP_ALLOWED_PATHS": "/Users/lixin/Workplace/RESTScope:/tmp",
        "SCHEMATHESIS_MCP_ALLOWED_HOSTS": "localhost,127.0.0.1"
      }
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

The first agent scaffold lives in `restscope.agent`. It tests one operation with
a fixed LangGraph flow: smoke, conformance, positive, negative, boundary,
evaluate, and report. The MVP runner calls Schemathesis through the capability
runtime, so MCP tools still pass through `ToolPolicy` and `ToolExecutor`:

```python
from restscope.agent import OperationTestAgent, OperationTestRequest, SchemathesisOperationRunner
from restscope.capabilities import build_capabilities_with_mcp_host

runtime = build_capabilities_with_mcp_host(config="./mcp.servers.json")
agent = OperationTestAgent(
    runner=SchemathesisOperationRunner(tool_executor=runtime.tool_executor),
)

report = agent.run(
    OperationTestRequest(
        schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
        base_url="http://localhost:8000",
        method="GET",
        path="/pets",
        allow_live_testing=True,
    )
)
```

## Program Startup

`RESTScopeApp` is the Python API entrypoint for the standalone runtime. It loads
RESTScope config, builds the capability runtime, and runs the global supervisor
graph over explicitly selected operations:

```python
from restscope import OperationSelection, RESTScopeApp, RESTScopeRunRequest

with RESTScopeApp.from_environment() as app:
    report = app.run(
        RESTScopeRunRequest(
            schema_source={"kind": "file", "path": "assets/openapi/petstore-v3.json"},
            base_url="http://localhost:8000",
            operations=[
                OperationSelection(method="GET", path="/pets"),
            ],
            allow_live_testing=True,
        )
    )
```

The first supervisor graph is intentionally narrow: direct request input,
selected operations only, no CLI, and no business fact table writes.
