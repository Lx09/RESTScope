# schemathesis-mcp

`schemathesis-mcp` is a CLI-first MCP server for agent-driven API testing. It
runs the official Schemathesis command as an isolated subprocess and consumes
its sanitized NDJSON report. It does not import Schemathesis Engine internals.

## Tools

- `get_capabilities`: describe supported tools, run options, limits, and safe
  configuration state.
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

For local development:

```console
uv sync
```

## MCP configuration

For local development, point the MCP client at the virtualenv script and pass
deployment-level policy through environment variables:

```json
{
  "mcpServers": {
    "schemathesis": {
      "command": "/Users/lixin/Workplace/schemathesis-mcp/.venv/bin/schemathesis-mcp",
      "env": {
        "SCHEMATHESIS_MCP_ALLOWED_PATHS": "/workspace:/tmp",
        "SCHEMATHESIS_MCP_ALLOWED_HOSTS": "localhost,127.0.0.1,api.test.example",
        "SCHEMATHESIS_MCP_ARTIFACT_DIR": "/workspace/.schemathesis-mcp",
        "SCHEMATHESIS_MCP_ARTIFACT_TTL": "1h"
      }
    }
  }
}
```

For reusable package-based installs, run the server through `uvx`:

```json
{
  "mcpServers": {
    "schemathesis": {
      "command": "uvx",
      "args": ["schemathesis-mcp"],
      "env": {
        "SCHEMATHESIS_MCP_ALLOWED_PATHS": "/workspace:/tmp",
        "SCHEMATHESIS_MCP_ALLOWED_HOSTS": "localhost,127.0.0.1",
        "SCHEMATHESIS_MCP_ARTIFACT_DIR": "/workspace/.schemathesis-mcp",
        "SCHEMATHESIS_MCP_ARTIFACT_TTL": "1h"
      }
    }
  }
}
```

The server uses stdio and does not open a network listener.

## Reusable configuration model

Keep reusable MCP server configuration split by responsibility:

- MCP client configuration starts the server and supplies deployment-level
  environment variables.
- Environment variables define local policy, such as allowed schema paths,
  allowed hosts, and an optional Schemathesis CLI override.
- Tool arguments describe one test run, such as schema source, base URL,
  checks, generation settings, timeouts, and reports.
- `get_capabilities` lets Agents discover supported tools, options, limits, and
  whether optional environment variables are configured.

Do not put deployment policy in `start_run`. Keep local paths, host allowlists,
CLI overrides, and similar reusable settings in MCP `env` or the process
environment.

`get_capabilities` returns a safe summary. It reports whether optional
configuration is present, but it does not expose full local paths, host lists, or
CLI override values:

```json
{
  "name": "schemathesis-mcp",
  "version": "0.1.0",
  "transport": "stdio",
  "backend": {
    "type": "schemathesis-cli",
    "cli_version": "schemathesis 4.21.10",
    "command_overridden": false
  },
  "tools": [
    "get_capabilities",
    "start_run",
    "get_run",
    "get_events",
    "get_result",
    "get_failure",
    "cancel_run"
  ],
  "resources": [
    "schemathesis://runs/{run_id}/{name}",
    "schemathesis://runs/{run_id}/failures/{failure_id}.json"
  ],
  "schema_inputs": {
    "kinds": ["file", "url", "inline"],
    "inline_formats": ["yaml", "json"]
  },
  "run_options": {
    "reports": ["junit", "har", "vcr", "allure"],
    "supports_headers": true,
    "supports_tls_verify": true,
    "supports_filters": true,
    "supports_timeout": true,
    "supports_seed": true
  },
  "limits": {
    "max_concurrent_runs": 4,
    "artifact_ttl_seconds": 3600
  },
  "configuration": {
    "env": [
      {
        "name": "SCHEMATHESIS_CLI",
        "required": false,
        "configured": false,
        "purpose": "Override the Schemathesis CLI command"
      },
      {
        "name": "SCHEMATHESIS_MCP_ALLOWED_PATHS",
        "required": false,
        "configured": false,
        "purpose": "Add allowed local schema roots"
      },
      {
        "name": "SCHEMATHESIS_MCP_ALLOWED_HOSTS",
        "required": false,
        "configured": false,
        "purpose": "Restrict URL schema and base_url hosts"
      },
      {
        "name": "SCHEMATHESIS_MCP_ARTIFACT_DIR",
        "required": false,
        "configured": false,
        "purpose": "Store run artifacts in a persistent directory"
      },
      {
        "name": "SCHEMATHESIS_MCP_ARTIFACT_TTL",
        "required": false,
        "configured": false,
        "purpose": "Set artifact retention time, for example 30m, 1h, or 7d"
      }
    ],
    "path_policy": {
      "default_allows_current_working_directory": true,
      "additional_roots_configured": false
    },
    "target_policy": {
      "host_allowlist_configured": false
    },
    "artifact_policy": {
      "persistent_root_configured": false,
      "default_uses_temporary_directory": true,
      "ttl_seconds": 3600,
      "ttl_configured": false
    }
  }
}
```

## Starting a run

`start_run` should contain only parameters for a single test run. Schema input
is explicit and immutable for file and inline sources:

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

By default, artifacts are written under a temporary directory created at server
startup, such as `/tmp/schemathesis-mcp-*` on Linux. Set
`SCHEMATHESIS_MCP_ARTIFACT_DIR` to keep artifacts in a stable directory:

```console
export SCHEMATHESIS_MCP_ARTIFACT_DIR="/workspace/.schemathesis-mcp"
```

Runs are stored below that directory by run ID:

```text
/workspace/.schemathesis-mcp/{run_id}/
  schemathesis.ndjson
  events.ndjson
  stdout.log
  stderr.log
  schema.json
  result.json
  failures/
```

Artifacts expire after one hour by default. Override the retention period with
`SCHEMATHESIS_MCP_ARTIFACT_TTL`:

```console
export SCHEMATHESIS_MCP_ARTIFACT_TTL="30m"
```

Supported TTL formats are seconds (`3600` or `15s`), minutes (`30m`), hours
(`1h`), and days (`7d`). Expired run directories are removed when the next
`start_run` call triggers cleanup.

## Security

- Run directories use mode `0700`.
- Schema snapshots and temporary configuration use mode `0600`.
- Headers are stored in `schemathesis.toml`, not process arguments, and the file
  is deleted when the CLI exits.
- CLI output sanitization is always enabled.
- Arbitrary CLI argument passthrough is not supported.
- At most four runs execute concurrently; artifacts expire after one hour by
  default.
- `SCHEMATHESIS_MCP_ARTIFACT_DIR` controls where output files are written; it
  does not grant schema read access. Schema files are still restricted by
  `SCHEMATHESIS_MCP_ALLOWED_PATHS`.

Local files are restricted to the server working directory by default. Add
allowed roots with the platform path separator:

```console
export SCHEMATHESIS_MCP_ALLOWED_PATHS="/workspace:/shared/contracts"
```

Optionally restrict URL targets:

```console
export SCHEMATHESIS_MCP_ALLOWED_HOSTS="api.test.example,localhost,127.0.0.1"
```

Use `get_capabilities` to diagnose whether optional security configuration is
present. It exposes boolean summaries only; it does not replace these runtime
checks or reveal the configured paths, hosts, or CLI command.

Write operations remain enabled because this server is intended for isolated
test environments. Use operation filters to narrow scope.

## Development

```console
uv run pytest
uv run ruff check .
uv build
```
