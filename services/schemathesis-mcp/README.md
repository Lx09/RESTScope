# schemathesis-mcp

`schemathesis-mcp` is a CLI-first MCP server for agent-driven API testing. It
runs the official Schemathesis command as an isolated subprocess and consumes
its sanitized NDJSON report. It does not import Schemathesis Engine internals.

## Tools

- `start_run`: start an asynchronous Schemathesis CLI run.
- `get_run`: read state, phase, and progress counters.
- `get_events`: page through stable MCP events projected from CLI NDJSON.
- `get_result`: read the final outcome, CLI metadata, failures, and artifacts.
- `get_failure`: read a sanitized request, response, reconstructed cURL command,
  and related stateful cases.
- `cancel_run`: terminate the CLI process group, escalating to a forced kill
  after the grace period.

`inspect_api` and `replay_failure` are intentionally absent. The Agent owns and
understands the OpenAPI document; the MCP server only snapshots and executes it.

## Installation

```console
uv tool install schemathesis-mcp
```

The package supports `schemathesis>=4.21,<5`. By default it invokes the CLI from
the same Python environment:

```text
python -m schemathesis.cli
```

Override the command when required:

```console
export SCHEMATHESIS_CLI="/opt/tools/schemathesis"
```

At startup the backend checks the CLI version and required NDJSON flags.

For local development against the sibling Schemathesis checkout:

```console
uv sync
```

For development without that checkout:

```console
uv sync --no-sources
```

## MCP configuration

```json
{
  "mcpServers": {
    "schemathesis": {
      "command": "/Users/lixin/Workplace/schemathesis-mcp/.venv/bin/schemathesis-mcp"
    }
  }
}
```

The server uses stdio and does not open a network listener.

## Starting a run

Schema input is explicit and immutable for file and inline sources:

```json
{
  "schema": {
    "kind": "file",
    "path": "/workspace/openapi.yaml"
  },
  "phases": ["coverage", "fuzzing"],
  "generation_modes": ["positive", "negative"],
  "max_examples": 50,
  "max_failures": 10,
  "max_time": 120,
  "seed": 1234,
  "reports": ["junit", "har"]
}
```

Supported schema forms:

```json
{"kind": "file", "path": "/workspace/openapi.yaml"}
{"kind": "url", "url": "https://api.test/openapi.json"}
{"kind": "inline", "format": "yaml", "content": "openapi: 3.1.0\n..."}
```

Inline input supports OpenAPI JSON or YAML. GraphQL SDL should use a `.graphql`
file or URL so the Schemathesis CLI can identify the specification type.

Additional reports are opt-in: `junit`, `har`, `vcr`, and `allure`. Sanitized
Schemathesis NDJSON is always generated because it is the MCP/CLI protocol.

## Results and artifacts

CLI exit codes map to MCP outcomes:

- `0` → `completed / passed`
- `1` → `completed / failed`
- `2` or another CLI error → `failed / errored`
- MCP timeout or cancellation → `cancelled / interrupted`

Each result includes the CLI version, sanitized command, exit code, schema
source metadata, and snapshot SHA-256 where available.

Artifacts include:

```text
schemathesis://runs/{run_id}/schemathesis.ndjson
schemathesis://runs/{run_id}/events.ndjson
schemathesis://runs/{run_id}/stdout.log
schemathesis://runs/{run_id}/stderr.log
schemathesis://runs/{run_id}/schema.json
schemathesis://runs/{run_id}/failures/{failure_id}.json
```

Requested JUnit, HAR, VCR, or Allure output is added to the same run namespace.

## Security

- Run directories use mode `0700`.
- Schema snapshots and temporary configuration use mode `0600`.
- Headers are stored in `schemathesis.toml`, not process arguments, and the file
  is deleted when the CLI exits.
- CLI output sanitization is always enabled.
- Arbitrary CLI argument passthrough is not supported.
- At most four runs execute concurrently; artifacts expire after one hour.

Local files are restricted to the server working directory by default. Add
allowed roots with the platform path separator:

```console
export SCHEMATHESIS_MCP_ALLOWED_PATHS="/workspace:/shared/contracts"
```

Optionally restrict URL targets:

```console
export SCHEMATHESIS_MCP_ALLOWED_HOSTS="api.test.example,localhost,127.0.0.1"
```

Write operations remain enabled because this server is intended for isolated
test environments. Use operation filters to narrow scope.

## Development

```console
uv run pytest
uv run ruff check .
uv build
```
