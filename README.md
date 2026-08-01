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

The database is one audit artifact for one App. It contains exactly 19 business
tables: the complete current normalized OpenAPI and response-change events,
current per-input Generators and Constraints, narrow Resource Identifier and
bounded Response Value evidence, stable Failures, terminal Solve Attempts,
validated input attribution, and accepted Generator/Constraint change events.
It never stores raw responses, Test Cases, Batches, Patch samples, model
conversations, plans, queues, scheduler progress, or authentication material.

Successful App construction leaves its SQLite file in place, including after
`close()`. A later process must use a new `DB_URL` or explicitly inspect and
delete the old run artifact before starting. RESTScope never overwrites or
automatically deletes a successfully created database. A caller that injects a
complete custom `CapabilityRuntime` owns its persistence and bypasses this
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
and response schemas, OpenAI-compatible and DeepSeek providers, model selection
for thinking/fast roles, structured output validation, and Agent-owned toolboxes
in `restscope.capabilities`. Each Agent registers only the tools it can actually
use; the same toolbox validates and executes those calls. Unit tests provide
their own local stub providers;
the runtime package does not register an offline fake provider.

Provider calls are routed through `LLMClient`; providers normalize responses but
do not execute tools or write database rows.

All five direct LLM decision sites construct messages through
`restscope.context`. Workflow adapters first select typed facts, then
`CompactTextWriter` encodes untrusted API, Memory, tool, and sample values as
bounded Markdown. Bounded HTTP request/response evidence is rendered as JSON
inside safe Markdown fences so a complete test case stays easy to inspect.
`AgentContext` preserves complete tool exchanges and newest validation feedback
inside the role and model windows. Strict Agent outputs and provider tool
protocols also remain JSON.

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
local development on `127.0.0.1`. Traces intentionally include generated test
data, non-sensitive tool parameters, and model-visible reasoning, so anyone
with local Phoenix access can inspect those values even though target
credentials are redacted.

## Phoenix Evals for Operation Smoke Agents

The developer-only [`evaluations/`](evaluations/README.md) directory evaluates
Failure Dedup, Failure Solve, and Parameter Patch independently with native Phoenix Datasets,
Experiments, and code evaluators. Repository YAML Scenarios are synchronized by
stable ID, and each Scenario/repetition receives fresh temporary Memory and
scripted tools. Experiment runs call the real configured DeepSeek model and
record linked traces, but never open the RESTScope database or request a target
API. This is LLM evaluation, not part of the runtime test suite.

```bash
uv sync --group evaluation
uv run --group evaluation python -m evaluations list
uv run --group evaluation python -m evaluations run patch \
  --scenario patch-integer-range --prompt current --repetitions 1 --seed 0
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

Build a standalone capability runtime by letting RESTScope start the MCP server,
run `tools/list`, and place the discovered tools in an explicit external
toolbox:

```python
from restscope.capabilities import build_capabilities_with_mcp_host

runtime = build_capabilities_with_mcp_host(
    config="/path/to/mcp.servers.json",
    server_names=("example",),
)
```

Lower-level embedding remains possible through `build_capabilities(...)` when a
caller already has discovered tools and a call bridge. Explicit sources are
registered in mapping order. The external toolbox remains isolated and is never
automatically injected into a RESTScope Agent. Calling `build_capabilities()`
without sources creates the shared target HTTP implementation and no
model-visible toolbox.

`MCPToolAdapter` preserves MCP input and optional output contracts but does not
translate annotations into a central permission policy. The caller or owning
Agent decides which discovered tools, if any, to include.

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
with its actually sent semantic Parameter values. Successful responses keep no
body. A 4xx/5xx response keeps a decoded body up to 10 MiB plus its separately
normalized Failure; redirects and transport errors keep only bounded Failure
facts. The Catalog is released when the operation's Smoke run ends and is
never persisted.

`restscope.http.request` is a high-risk, non-read-only model capability that
can trigger side effects on the bound target. Failure Solve receives a further
operation-scoped wrapper around it. Generated batch execution remains internal
to Operation Smoke and can execute only an initialized operation using its
complete current Generator and Constraint configuration.
The raw HTTP result includes all response headers, including authentication and
Cookie headers, plus its bounded JSON or text body.

The default and only Supervisor execution path is
`Supervisor → OperationSmokeCoordinator → OperationTestingService.run_smoke_batch`.
The default App does not start MCP processes.

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
including sensitive-looking names, may be retained. `restscope.resource.lookup`
remains the explicit read-only lookup tool; Response Value pools are consumed
internally by Operation Smoke.

## Operation Smoke workflow

`OperationSmokeCoordinator` owns ordering around three LLM Agents:

1. A complete generated Batch establishes the current evidence and 2xx rate.
   The App-wide `RANDOM_SEED` is reused by Batch inputs, Constraint solving, and
   Patch samples.
2. `FailureDeduplicator` extracts normalized error-message Fingerprints and
   keeps only the first test case for each exact Fingerprint. One Fingerprint
   bypasses the LLM. With several Fingerprints, `FailureDedupAgent` groups them
   by equal complete suspected causal Parameter sets. Its initial Markdown
   context contains only the operation, each Failure Message, and a
   representative `TC*` reference. It discovers Parameters through
   `openapi.lookup_operation` and queries exact case values through
   `query_test_case_catalog`; native structured tool results are compact JSON.
   It reads no Failure history. Deterministic validation and Markdown
   correction run before Memory is written. Every current-round Failure
   carries exactly one representative `TC*`.
3. Each debug item gets a fresh `FailureSolveAgent`. Solve preloads the current
   Failure, may query other Failure history by semantic Parameter handle, and
   may query any currently known `TC*`. Its current-operation HTTP probe supports
   GET, HEAD, OPTIONS, POST, PUT, PATCH, and DELETE. Every attempted Probe adds
   another `TC*`; mutating Probes are not rolled back. Before replacing a
   Parameter Generator it must inspect that Parameter's earlier Failure/Patch
   history.
4. Solve calls a fresh FAST `ParameterPatchAgent` as an internal tool.
   Structured root cause, affected inputs, desired behavior, and acceptance
   criteria are compiled into Generator/Constraint changes. DTO, Schema,
   satisfiability, and local samples are validated before the tool returns a
   session-local `P*` candidate reference.
5. Solve finishes with `apply_patch(P*)`, `no_patch`, or `conflict`. Only the
   first action changes Generator or Constraint state. A successful Patch
   commits the current-state changes, one Solve Attempt, validated input links,
   and one append-only Generator change event in one transaction. A state
   conflict records a separate `conflict` Solve Attempt without applying the
   stale candidate.

Every item in the fixed Dedup result finishes before another Batch is allowed.
If no Patch was applied, Smoke passes with `no_patch_applied`. Otherwise the next
complete Batch validates all applied changes together, and
`success_rate_reached` stops at the configured threshold (80% by default).
There is no Effect Agent, candidate Batch, rollback snapshot, or permanent
`resolved` flag.

Dedup has a shared 50-output budget. One Fingerprint uses zero outputs; several
normally require an OpenAPI lookup, optional Catalog reads, and one final
decision. Each Solve has a 50-output budget that also counts every nested
Parameter Patch LLM output; one Patch tool call is capped at 20 outputs.
Malformed replies and invalid tool-requesting model outputs count.
Solve outputs 10, 20, 30, and 40 are tool-free continuation checks. Dedup or
Solve exhaustion is a technical error; one Patch-tool exhaustion is recoverable
feedback within its Solve session.

The database keeps stable structured Failures, append-only Solve Attempts,
current input Generators and Constraints, and append-only deterministic change
events. Rejected session candidates, Patch samples, raw Batches/responses, HTTP
transcripts, and LLM transcripts are not persisted. Public results contain
Batch run IDs plus bounded round, Solve Attempt, and Generator change-event
summaries. Request/response reports are intentionally absent.

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
Coordinator immediately sends generated requests to the target bound during
`initialize()`, including operations that may have side effects. Run it only
against a target you are authorized to test.

App construction prepares the database before building the default capability
and LLM runtimes. If construction fails, RESTScope removes only the SQLite file
and sidecars created by that attempt. A failure during `initialize()` never
deletes or overwrites the retained database. Parse and validation failures that
occur before the OpenAPI catalog is initialized remain retryable on the same
App object.

Initialization validates the file, URL, or inline schema source and parses it
exactly once for the lifetime of the App. The resulting IR and target settings
are bound out-of-band to trusted tool handlers; they are not copied into graph
state, tool schemas, or model arguments.

Supervisor orders operations by stable path depth and retains every attempt.
Smoke receives only the target operation key. Its three normal stop reasons are
all satisfied Supervisor outcomes, even when the measured rate remains below
80%. An operation-scoped technical error may be scheduled in a later round so
other operations can add global pool evidence first; the default is at most
three attempts. Unsupported operations are recorded without retry. Shared
setup or uncaught runtime failures stop immediately as
`errored/technical_error`. Queue and retry state are not persisted.
