# RESTScope code-reading guide

This guide is written for a reader who has never written code. It explains what
the repository is trying to do, how a normal run moves through the packages,
and how to read the local comments without first learning every Python feature.

## 1. The shortest mental model

RESTScope receives an OpenAPI description of an HTTP API and a base URL for a
real target service. It converts the description into an in-memory model,
generates requests, sends them, observes the responses, and uses bounded Agents
to decide what to try next.

The core loop is:

1. **Load configuration.** Read environment settings for the database, target
   API, language models, logging, and tracing.
2. **Parse OpenAPI.** Convert paths, operations, parameters, request bodies, and
   responses into a typed in-memory representation called the **IR**.
3. **Create runtime services.** Build the database repositories, HTTP
   transport, tool registry, language-model clients, tracing runtime, and
   testing services.
4. **Choose an operation.** The Supervisor selects one OpenAPI operation that
   still needs evidence.
5. **Generate a batch.** Generator configurations produce concrete path,
   query, header, cookie, and body values.
6. **Send HTTP requests.** Test cases are executed against the configured
   target API.
7. **Observe responses.** The API Behavior Monitor checks response contracts
   and learns narrowly approved identifier and response-value evidence.
8. **Diagnose failures.** Operation Smoke investigates failed cases, optionally
   using HTTP probes limited to the current operation.
9. **Patch parameters.** Confirmed parameter problems are grouped
   deterministically. A fresh Parameter Patch Agent proposes Generator or
   Constraint changes and reviews ten local samples.
10. **Validate effects.** All successful groups are applied to one candidate
    batch. Only real HTTP evidence decides whether the original failures were
    resolved.

Most state used in steps 4–10 is deliberately temporary. RESTScope does not
persist plans, Agent conversations, hypotheses, queues, or evolved OpenAPI
snapshots.

## 2. A few Python concepts used throughout

You do not need to master Python before reading RESTScope. These patterns cover
most files:

- `class Name:` defines a type that groups data or behavior.
- `def name(...):` defines a reusable operation called a function or method.
- A leading underscore, such as `_parse_value`, means “internal implementation
  detail”; other modules should normally use the package's public facade.
- `@dataclass` and Pydantic `BaseModel` classes primarily describe structured
  data. Their annotated fields state which values are allowed.
- `Protocol` describes behavior another object must provide without selecting
  one concrete implementation.
- `Literal["a", "b"]` limits a string field to the listed choices.
- `T | None` means a value may either be `T` or absent.
- `with ...:` creates a scope that guarantees cleanup. RESTScope uses this for
  database sessions, HTTP responses, traces, and temporary operation context.
- `try/except/finally` separates normal work, error translation, and cleanup
  that must occur on both success and failure.
- `model_validate(...)` checks untrusted dictionaries against a Pydantic data
  contract. `model_dump(...)` converts a validated model back to plain data.
- A package's `__init__.py` is its public doorway. Cross-package callers should
  import from that doorway instead of reaching into private files.

## 3. Important domain words

### OpenAPI

A machine-readable description of an HTTP API. It lists paths such as
`/projects/{projectId}`, methods such as `GET`, input parameters, request-body
schemas, and possible responses.

### IR (in-memory representation)

The normalized Python objects produced by the OpenAPI parser. Later code uses
the IR instead of repeatedly interpreting raw YAML or JSON.

### Operation key

A stable string combining an HTTP method and OpenAPI template path, for example
`GET /projects/{projectId}`. It identifies the operation even when one concrete
request uses `/projects/123`.

### Generator

A rule that produces values for one input. Examples include a constant, a
choice list, an integer range, a formatted date, or an observed resource
identifier.

### Constraint

A relationship that generated inputs must satisfy together. Examples include
“end is present only if start is present”, “minimum is no greater than
maximum”, or “exactly one of these fields is included”.

### F/C/O evidence references

Short aliases used only during one diagnosis:

- `F1`, `F2`, … identify unique failure signatures.
- `C1`, `C2`, … identify baseline Smoke cases.
- `O1`, `O2`, … identify observations returned by diagnostic HTTP probes.

The aliases keep prompts smaller and make it possible to validate that a model
only cites evidence it was actually shown.

### Patch Group

A set of parameter changes that must be constructed and accepted atomically.
Inputs that overlap or require one same-request Constraint belong in the same
group. Each group receives a fresh Parameter Patch Agent with no history from
other groups.

### DTO

A “data transfer object”: a strict typed shape used at a boundary. In this
project Pydantic models are commonly used as DTOs to reject missing, extra, or
incorrectly typed model output.

## 4. Where to start reading

Read these files in order:

1. `README.md` — configuration and supported runtime workflows.
2. `restscope/app.py` — builds the application and owns shared resources.
3. `restscope/agent/supervisor/graph.py` — the top-level dynamic operation loop.
4. `restscope/testing/execution.py` — turns generated cases into real HTTP
   results.
5. `restscope/agent/operation_smoke/agent.py` — baseline Smoke, feedback rounds,
   candidate revisions, and effect acceptance.
6. `restscope/agent/operation_smoke/diagnosis.py` — root-cause investigation
   state machine and effect validation.
7. `restscope/agent/parameter_patch/agent.py` — Generator/Constraint proposal,
   deterministic validation, ten-sample review, and retry limits.
8. `restscope/agent/api_behavior_monitor/agent.py` — response observation and
   the narrow persistent evidence catalog.

When a file imports a name from a package, open that package's `__init__.py`
first. It shows which types are intended to be public.

## 5. Package map

### `restscope/app.py`

The composition root. It creates and connects the parser, repositories, HTTP
transport, tools, language models, Agents, testing service, and tracing
runtime. It also guarantees that owned resources are closed.

### `restscope/restscope_config.py`

Loads settings from environment variables. It translates text values into
typed database, model, logging, and tracing configuration while applying
defaults.

### `restscope/openapi_parser/`

Converts OpenAPI dictionaries into the IR.

- `parser.py` coordinates parsing.
- `ir.py` defines the normalized data structures.
- `resolver.py` resolves local `$ref` references safely.
- `parsers/` handles components, parameters, requests, responses, schemas, and
  security one concern at a time.
- `postprocess/` performs bounded cleanup and normalization after parsing.
- `document_builder.py` converts the evolved in-memory IR back to an OpenAPI
  document when a caller explicitly needs one.

### `restscope/testing/`

Owns deterministic request generation and execution.

- `snapshot.py` freezes the operation inputs used by one Generator config.
- `models.py` describes available value strategies and generated-case records.
- `generation.py` produces one generated case from strategies and constraints.
- `constraints.py` defines the expression language.
- `constraint_solver.py` finds assignments that satisfy those expressions.
- `serialization.py` turns generated values into an HTTP-shaped request.
- `execution.py` sends batches and records bounded evidence.
- `catalog.py` persists approved Generator configuration revisions.

### `restscope/agent/supervisor/`

Owns the dynamic top-level loop. It chooses operations from current runtime
evidence; it does not load a persisted static plan.

### `restscope/agent/operation_smoke/`

Runs a baseline batch, investigates failures, groups actionable inputs, invokes
Parameter Patch Agents, runs one combined candidate batch, and accepts only
groups whose original failures are resolved.

Important files:

- `agent.py`: feedback-loop orchestration.
- `diagnosis.py`: one-failure-at-a-time investigation.
- `evidence.py`: bounded and redacted F/C/O evidence journal.
- `grouping.py`: deterministic connected-component grouping.
- `probe.py`: HTTP tool limited to the current operation.
- `prompts.py` and `planning.py`: DTO-derived model protocols.
- `schemas.py`: runtime state and report contracts.
- `references.py`: observed-value options exposed as model-safe `R` aliases.

### `restscope/agent/parameter_patch/`

Constructs one complete Patch Group. It compiles model output into testing
types, validates Generator schemas and Constraints, generates exactly ten local
samples, and requires the same Agent to review them before acceptance.

### `restscope/agent/api_behavior_monitor/`

Observes real HTTP responses. It can check response contracts and maintain the
explicitly approved resource-identifier and response-value catalogs. It never
persists raw responses or general Agent memory.

### `restscope/capabilities/`

Defines model-callable tools and the executor that applies policies, tracing,
redaction, and error translation. `http_request.py` contains the bounded
open-world HTTP tool; Operation Smoke wraps it with a stricter current-operation
scope.

### `restscope/http_transport.py`

The shared low-level HTTP boundary. It validates target paths, merges headers,
limits response bodies, translates transport errors, and synchronously offers
responses to the Behavior Monitor.

### `restscope/llm/`

Provider-independent language-model contracts and clients.

- `schemas.py` defines messages, requests, responses, tools, and model config.
- `client.py` selects a provider and creates LLM trace spans.
- `model_selector.py` maps semantic roles to thinking or fast models.
- `output_validator.py` parses and validates structured output.
- `providers/` translates the common request into provider-specific calls.

### `restscope/context/`

Builds bounded prompt context. Policies decide which sections a role may see,
the budget manager limits size, and the renderer produces model messages.

### `restscope/db/`

Owns database setup, ORM records, migrations, and repositories. Repositories
are narrow storage APIs; callers should not manipulate ORM rows directly.

### `restscope/observability/`

Creates Phoenix/OpenTelemetry spans while applying redaction and size limits.
Tracing is optional: the disabled runtime preserves behavior without exporting
data.

### `restscope/memory/`

Contains bounded in-process conversation history helpers. It is not durable
Agent memory and must not become one without a new architecture decision.

### `tests/`

Tests are executable behavior examples. Fixture helpers construct small OpenAPI
operations and fake providers; focused files protect module contracts; files
ending in `_live.py` are opt-in and can call real services.

## 6. How to follow one request

For a normal generated Smoke case:

```text
RESTScopeApp
  -> RESTScopeMainGraph
  -> OperationSmokeAgent
  -> OperationTestingService
  -> generate_test_case
  -> serialize generated values
  -> TargetHTTPTransport
  -> APIBehaviorMonitorAgent
  -> OperationExecutionReport
```

For a failed batch that receives a candidate Patch:

```text
OperationExecutionReport
  -> EvidenceJournal (F/C refs)
  -> Failure investigation
  -> optional scoped HTTP Probe (O refs)
  -> ActionableFailure
  -> deterministic Patch Groups
  -> fresh ParameterPatchAgent per Group
  -> compile + solve + 10 local samples
  -> one combined candidate HTTP batch
  -> Effect Validator
  -> accept or reject whole Groups
```

## 7. Where optimization is safe

Before optimizing, identify the boundary being changed:

- **Prompt size:** adjust only model-facing projections; keep full run-local
  evidence for deterministic validation and provenance.
- **HTTP throughput:** preserve response-size limits, operation identity, and
  Behavior Monitor processing.
- **Generation speed:** preserve stable seeds, Constraint satisfiability, and
  request-shape projection.
- **LLM retries:** preserve DTO validation and explicit stop reasons; do not
  silently accept malformed output.
- **Database access:** optimize inside repositories; do not turn transient
  plans or Agent state into persistent records.
- **Tracing cost:** preserve parent-child structure and redaction even when
  reducing payload size.

When unsure, find the relevant test and task record before editing the code.
Tests show current executable behavior; task records explain why a decision was
made and whether it remains current.

## 8. How comments should help

A useful comment answers at least one of these questions:

- Why is this step needed?
- Which invariant would break if it were removed?
- Why is a simpler-looking alternative unsafe?
- Which earlier object owns this data?
- Is this state run-local or persistent?
- Which errors are ordinary evidence, and which are technical failures?
- What must be cleaned up even if an exception occurs?

A comment that only says “increment the counter” above
`counter += 1` does not help. The nearby explanation should instead say why
that counter is bounded and what happens when it reaches the limit.
