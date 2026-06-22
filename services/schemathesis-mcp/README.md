# schemathesis-mcp

`schemathesis-mcp` exposes Schemathesis as an MCP server for API-testing agents.
It embeds the Schemathesis Engine directly, so agents receive structured progress,
failures, reproduction commands, and artifacts without parsing terminal output.

## Features

- Inspect OpenAPI and GraphQL schemas before testing.
- Run examples, coverage, fuzzing, and stateful phases asynchronously.
- Poll progress, page through events, cancel runs, and replay failures.
- Produce sanitized NDJSON, JUnit XML, HAR, and per-failure JSON artifacts.
- Keep test outcome separate from MCP job state.
- Limit concurrent runs, enforce run timeouts, expire artifacts, and optionally
  restrict target hosts.

## Installation

Published-package installation:

```console
uv tool install schemathesis-mcp
```

Local development against the sibling Schemathesis checkout:

```console
cd /Users/lixin/Workplace/schemathesis-mcp
uv sync
```

`pyproject.toml` keeps the public dependency declaration
`schemathesis>=4.21,<5` and uses an editable `../schemathesis-master` source only
for local `uv` development.

## MCP configuration

Example stdio configuration:

```json
{
  "mcpServers": {
    "schemathesis": {
      "command": "/Users/lixin/Workplace/schemathesis-mcp/.venv/bin/schemathesis-mcp"
    }
  }
}
```

The executable uses stdio by default and does not open a network listener.

## Tools

- `inspect_api`: load a schema and list selected operations.
- `start_run`: start an asynchronous Schemathesis run and return a `run_id`.
- `get_run`: return job state and progress counters.
- `get_events`: page through projected events with a monotonic cursor.
- `get_result`: return the final outcome, summary, failure IDs, and artifacts.
- `get_failure`: return a sanitized request, response, check, cURL command, and
  related stateful cases.
- `cancel_run`: request cooperative Engine cancellation.
- `replay_failure`: replay the original in-memory request and report whether the
  observed status code was reproduced.

Run states are `queued`, `loading`, `running`, `cancelling`, `completed`,
`failed`, `cancelled`, and `expired`. Test outcomes are `passed`, `failed`,
`errored`, and `interrupted`.

## Typical agent flow

1. Call `inspect_api` with a schema URL or local schema path.
2. Call `start_run`, normally with a fixed seed and an explicit time budget.
3. Poll `get_run` and optionally consume `get_events`.
4. Once complete, call `get_result`.
5. Fetch interesting failures with `get_failure`.
6. Use `replay_failure` to check whether a failure is stable.

Example `start_run` arguments:

```json
{
  "schema": "https://api.example.com/openapi.json",
  "headers": {"Authorization": "Bearer ${TOKEN}"},
  "phases": ["coverage", "fuzzing", "stateful"],
  "generation_modes": ["positive", "negative"],
  "max_examples": 50,
  "max_failures": 10,
  "max_time": 120,
  "seed": 1234
}
```

Write operations are enabled by default because the server is intended for
isolated test environments. Use Schemathesis operation filters to narrow scope.

## Resources

Artifacts are exposed under:

```text
schemathesis://runs/{run_id}/events.ndjson
schemathesis://runs/{run_id}/junit.xml
schemathesis://runs/{run_id}/har.json
schemathesis://runs/{run_id}/failures/{failure_id}.json
```

The in-memory event window is bounded. If a cursor expires, clients should read
the NDJSON artifact.

## Security

Authorization, API key, cookie, password, token, and secret fields are redacted
from tool responses and persisted artifacts. Raw credentials remain only in the
in-memory run request and replay data.

Set an optional comma-separated host allowlist:

```console
export SCHEMATHESIS_MCP_ALLOWED_HOSTS=api.test.example,localhost,127.0.0.1
```

TLS verification is enabled by default. The server permits at most four active
runs, uses cooperative cancellation, and retains artifacts for one hour by
default.

## Development

```console
uv run pytest
uv run ruff check .
uv build
```

The end-to-end suite starts the Booking API from the sibling Schemathesis
repository and verifies that its intentional `room_type` bug is reported.
