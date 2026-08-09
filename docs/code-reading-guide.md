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
3. **Create runtime services.** Build the database repositories, shared HTTP
   transport, Agent Profiles, selected Tool Bindings, language-model clients,
   tracing runtime, and testing Harness.
4. **Start Main.** `RESTScopeApp.start()` creates the `main` Profile Agent and
   blocks in its model loop without constructing a task DTO.
5. **Use authorized capabilities.** The initial Profile can read and replace
   only its private Plan; unfinished testing Skills and Tools are absent.
6. **Finish safely.** Main returns its internal `AgentCompletion`, normally to
   report that testing cannot proceed until those capabilities are connected.

The remaining focused Operation Smoke flow is a temporary migration path, not
something the production Main Profile can currently start:

7. **Index Test Cases.** One run-local Catalog retains every sent request's
   structured input JSON and only failed response bodies.
8. **Resolve the failed Batch.** Runtime collapses exact duplicate messages into
   `E*` sources, then one continuous Failure Resolution Agent owns semantic
   grouping, investigation order, and a revisioned reference-only worklist. It
   queries OpenAPI, `TC*` cases, Parameter history, or the operation-scoped HTTP
   Probe only when needed. At 80% of configured input capacity, its nested FAST
   Compact Agent reads the unchanged system prompt plus the complete saved
   conversation and one temporary checkpoint instruction. Runtime replaces old
   assistant/tool exchanges with the original Failure prompt plus the Markdown
   handoff; worklist and registries stay untouched.
9. **Build, select, and finalize Patches.** Resolution calls the Parameter Patch
    Coordinator as an internal tool. Each reviewed candidate remains in a
    session registry behind `P*`. When the Agent finishes, deterministic code
    validates decided items and atomically commits compatible candidates plus
    their Failures and Attempts before the next complete Batch.

Most backend state used in steps 4–9 is deliberately temporary. RESTScope does
not persist plans, Agent conversations, hypotheses, queues, Batches, Test Cases,
or Patch samples in its database. The local observer page is a narrow testing
exception: it may cache the latest five complete, already-redacted UI
snapshots—including raw Provider Reasoning—in that browser's same-origin
IndexedDB, but the App never reads them and cannot recover or influence a test
from them. RESTScope persists the complete current
normalized OpenAPI plus change events for audit/export, current per-input
Generators and Constraints, bounded
Behavior Monitor evidence, stable Failures, terminal Resolution Attempts, validated
input attribution, and deterministic accepted-change events. None of these
artifacts restores an App.

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
choice list, an integer range, text matching a regular expression, a formatted
date, or an observed resource identifier. A regular-expression Generator uses
Python search semantics and explicit whole-string length bounds so matching
text is generated deterministically without allowing unbounded output.

### Constraint

A relationship that generated inputs must satisfy together. Examples include
“end is present only if start is present”, “minimum is no greater than
maximum”, or “exactly one of these fields is included”.

### Request-local references

Resolution uses `E1`, `E2`, … for exact Failure sources, `TC1`, `TC2`, … for
run-local cases, and `P1`, `P2`, … for reviewed Patch candidates created in its
own session. Its initial prompt contains only the operation key, exact messages,
and original E-to-TC associations. It discovers semantic Parameters and uses
run-local `test_case.*` tools instead of receiving full HTTP JSON. It does not
receive a Failure-message lookup because those messages are already present.
When a message is unclear, `openapi.list_response_fields` discovers contract
path candidates and `test_case.get_response_field_value` reads one selected
path from the associated failed cases. Each Test Case tool returns only the
selected request or response JSON fragment. Inside JSON, `sort` is a direct key at
`request.query.sort`; `query.sort` remains the unique semantic handle used by
OpenAPI, Catalog, Memory, Patch, and Agent output. An unused request Parameter
and an unretained response body are reported with explicit terminal status text
rather than a boolean presence flag.
Resolution can additionally query one input or response-field Schema at a time.
The complete OpenAPI IR and database primary keys never enter model prompts.

### Failure and Resolution Attempt

A Failure is stable across rounds when its operation, normalized message set,
and complete suspected causal Parameter state match. Its short display summary
is generated mechanically from those authoritative messages instead of being
restated by the Agent. The representative `TC*` remains only in the run-local
Test Case Catalog. A terminal Attempt stores its outcome, final worklist root
cause, and decision reason. Applied-Patch input
attribution comes from the selected candidate; no-Patch attribution is resolved
from the item's validated semantic handles, with `[]` representing an
operation-level cause. When Resolution selects a validated Patch, deterministic
Generator/Constraint before-and-after changes commit with that Attempt;
candidate samples are not persisted.

### DTO

A “data transfer object”: a strict typed shape used at a boundary. In this
project Pydantic models are commonly used as DTOs to reject missing, extra, or
incorrectly typed model output.

## 4. Where to start reading

Read these files in order:

1. `README.md` — configuration and supported runtime workflows.
2. `restscope/app.py` — builds the application and owns shared resources.
3. `restscope/harness/agent_runtime.py` — validates Profiles and constructs the
   generic Main Agent or authorized Subagents.
4. `restscope/request_generation/` — builds values and enforces cross-input
   Constraints without sending network requests.
5. `restscope/harness/operation_testing/service.py` — turns generated cases
   into real target HTTP results and run-local Test Cases.
6. `restscope/operation_smoke/coordinator.py` — temporary complete-Batch rounds,
   Resolution session dispatch, and explicit stop conditions.
7. `restscope/operation_smoke/failure_resolution/agent.py` and
   `finalizer.py` — one continuous Agent session, reference-only draft state,
   registry checks, and atomic finalization.
8. `restscope/operation_smoke/parameter_patch/agent.py`,
   `restscope/operation_smoke/parameter_patch/coordinator.py`,
   `restscope/operation_smoke/parameter_patch/review/agent.py`, and
   `restscope/operation_smoke/memory/patch_application.py` — strict proposal,
   executable construction, fresh-context semantic review, and atomic
   persistence.
9. `restscope/api_behavior_monitor/coordinator.py` — response observation and
   the narrow persistent evidence catalog.

When a file imports a name from a package, open that package's `__init__.py`
first. It shows which types are intended to be public.

## 5. Package map

### `restscope/app.py`

The composition root. It creates and connects the parser, repositories, HTTP
transport, tools, language models, Agents, testing service, and tracing
runtime. It also guarantees that owned resources are closed.

### `restscope/config.py`

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

### `restscope/openapi_audit/`

Owns the database-independent current OpenAPI audit boundary. It initializes
one complete normalized document, atomically replaces that document while
appending real response-contract change events, and provides read-only export
and event queries. It does not reopen an App from those records.

### `restscope/request_generation/` and `restscope/harness/operation_testing/`

Request Generation owns deterministic values, Generator configuration, Schema
snapshots, Constraint evaluation, solving, and request serialization:

- `snapshot.py` freezes the operation inputs used by one Generator config.
- `models.py` describes available value strategies and generated-case records.
- `generation.py` produces one generated case from strategies and constraints,
  including bounded strings generated from regular expressions.
- `constraints.py` defines the expression language.
- `constraint_solver.py` finds assignments that satisfy those expressions.
- `serialization.py` turns generated values into an HTTP-shaped request.
- `store.py` combines App-memory operation snapshots with current persisted
  per-input Generator rows. It does not store operation snapshots or revisions.

Operation Testing owns the deterministic network lifecycle. `service.py` sends
Batches and returns Catalog-ready Test Cases; `test_case_catalog/` retains
run-local `TC*` evidence; and `probe_evidence.py` records scoped Resolution
Probes without moving diagnostic decisions into the Harness.

### `restscope/operation_references/`

`request.py` owns the pure in-memory `RequestInputReference` Interface shared by OpenAPI
lookup, Testing, and the Test Case Catalog. It constructs semantic handles such
as `query.sort`, reads the corresponding direct-name request JSON, and projects
bounded evidence fragments. It owns no operation registry or persistent state.

`response.py` owns the pure in-memory `ResponseFieldReference` Interface shared by OpenAPI
lookup and API Behavior Monitor response-value handling. It gives one response
field the same identity when OpenAPI spells it as a `body...` handle and stored
observations spell it as a `$...` selector, including arrays and Schema
combination branches. It owns no OpenAPI registry, response values, or
persistent state.

### `restscope/agent/runtime.py`

Owns the shared one-Tool-or-final model loop. `Agent.start()` is the taskless,
one-shot Main entry; `Agent.run(AgentTask)` remains the bounded task protocol
for Subagents and focused internal callers. The Harness is the only constructor.

### `restscope/operation_smoke/`

Owns thin coordination, a run-local Test Case Catalog, structured Failure
Memory, public round summaries, and reference adaptation.

Important files:

- `coordinator.py`: complete-Batch and continuous Resolution orchestration.
- `restscope/harness/operation_testing/test_case_catalog/`: `TC*` identity,
  bounded response retention, exact
  structured-JSON queries, and four single-purpose tools registered for
  Resolution. The unregistered Failure-message query remains available to
  trusted Catalog callers.
- `memory/`: domain Memory Interface and atomic Patch application.
- `schemas.py`: public request and bounded result summaries.
- `references.py`: current resource/response evidence validation, candidate-
  only response sampling, and Apply-time response pool registration.

### `restscope/operation_smoke/failure_resolution/`

Owns deterministic exact-message folding and one continuous Agent session for
all Failures in a failed Batch. `worklist.py` validates only revisions and real
references; `candidates.py` hides precise reviewed objects behind `P*`;
`agent.py` owns semantic grouping, investigation, and finish timing; and
`finalizer.py` performs mechanical compatibility checks and one atomic commit.
The future generic method is recorded separately in the standard
`resolve-operation-failures` Skill under
`restscope/builtin_skills/resolve-operation-failures/`. Its core routes to
progressively disclosed diagnosis, Worklist, Tool/Probe, Patch-Subagent,
decision, and completion References. It delegates confirmed Parameter repair
to an authorized child Profile that selects `build-parameter-patch`; it does
not call the specialized Patch-generation Tool. No production Profile selects
this Skill yet, so it does not change the current specialized runtime.

### `restscope/operation_smoke/parameter_patch/`

Constructs one Resolution-owned Patch candidate. It compiles model output into
testing types, validates Generator schemas and Constraints, generates
`case_count` local samples, and coordinates a separate semantic Reviewer.
`agent.py` owns one continuing proposal/revision conversation;
`coordinator.py` owns deterministic checks, shared output budget, and feedback.
The standard project-native `build-parameter-patch` Skill lives under
`restscope/builtin_skills/build-parameter-patch/`. Its `SKILL.md` and focused
references own evidence authority, Generator construction, Constraint solving,
compilation/sampling semantics, proposal correction, and semantic Review. The
generic built-in Skill loader validates and caches those package files before
an Agent starts. The specialized Proposal Agent temporarily consumes only the
packaged proposal Reference through that Catalog. The complete Skill also
records the future generic Agent's self-review method, but no production
Profile selects it yet.
Both Proposal and Review return JSON Schema responses that are validated again
locally. The Proposal Schema exposes only model-constructible Generators and a
recursive Constraint language that uses semantic input handles. The Proposal
Agent alone can query canonical resources, populated typed IDs, and observed
response fields. It submits those sources directly as Generator strategies;
deterministic compilation revalidates current evidence and derives private
response pool names. Its private `review/` subpackage owns the FAST Review
Agent. Every compiled
candidate receives a fresh context containing only normalized requirement,
Generator, Constraint, reference-provenance, and sample facts. Review issues
are authoritative: an empty array passes, while non-empty issues return to the
Patch Agent; no separate Operation Smoke Review Interface is exposed.

### `restscope/context/`

The project-level message-construction Module used by every direct LLM
decision. `CompactTextWriter` turns already-selected DTO, Memory, tool, and
sample facts into bounded Markdown cards while escaping untrusted values.
Strings are JSON-quoted, nested mappings and lists stay visibly nested, and
missing, null, empty-list, and empty-object values remain distinguishable. The
Writer does not decide which domain facts matter; the owning workflow makes
that choice before rendering. HTTP request/response evidence uses safe JSON
blocks inside that Markdown.
`AgentContext` keeps stable system/developer instructions, the original task,
complete tool-call groups, newest feedback, explicit clipping, and numeric
trace metrics inside each role's budget. It knows no Operation Smoke, Behavior
Monitor, database, or Agent registry concepts.

For local compaction, `AgentContext` keeps stable system/developer prompts
separate from its replaceable history. `messages_for_compaction` creates
temporary `B + H + C` messages, while `replace_compacted_history` installs
`H' = U + S` without changing `B`. These generic methods do not interpret
Resolution state.

Skill- and Harness-specific code remains responsible for selecting and
interpreting domain facts. No model-facing runtime evidence is produced by
dumping a DTO or Memory object as JSON; strict JSON remains the final Agent
output and provider Tool protocol.

### `restscope/api_behavior_monitor/`

Observes real HTTP responses. It can check response contracts and maintain the
explicitly approved resource-identifier and response-value catalogs. It never
persists raw responses or general Agent memory.

### `restscope/agent/`, `restscope/skills/`, and `restscope/tools/`

`agent/` defines the one generic Agent, its task/completion/result contracts,
and the Profile that names its model configuration, ordered Tools, Skills,
bounded context sources, and described authorized child Profiles. Its private
`prompt.py` Module owns generic Profile prompt roles, changing Context
fingerprints, Skill instruction injection, protocol reservation, and compaction
requests without exporting a Prompt platform. `builtin_skills/` stores standard
project-owned `SKILL.md` instruction assets, optional `restscope.yaml` runtime
manifests, and directly linked Markdown References. `skills/` discovers them in
stable order, validates the standard and runtime contracts, and caches
immutable definitions. A Skill executes nothing and discovery grants nothing;
each Profile must still select it and grant every dependency. `tools/` owns every
RESTScope Tool contract and execution Adapter, grouped by the thing handled:
HTTP, OpenAPI, Resource, Test Case, Worklist, Plan, Skill, File, Parameter, and
Subagent. Its immutable built-in Catalog is authoritative, while one Profile
still selects the exact definitions made executable for an Agent.
`tools/plan/` owns the small read/replace Interface for one Agent session's
private task progress;
`tools/skill/` owns the one Harness-bound core loader contract; selecting Skills
automatically appends it after explicitly ordered Profile Tools. `tools/file/`
owns explicit, Profile-scoped reads of only startup-registered Skill Reference
Markdown held in memory; it never resolves model input as a filesystem path; and
`tools/worklist/` retains Failure Resolution's reference-rich domain behavior.

### `restscope/harness/`

Owns deterministic App and Agent lifecycle, Profile validation, live dependency
binding, session state, Tool execution, tracing, and logs. Runtime-discovered
MCP Tools remain in a separate external Catalog. Raw logs are not Agent context;
model-visible results are structured, bounded, and redacted.

Start with `harness/agent_runtime.py`: it validates the complete immutable
Profile graph and is the only place that turns names into a live Agent. Then
read `harness/agent_control.py` for direct-parent authorization, asynchronous
child state, open/active slots, cooperative cancellation, and the shared
weighted rollout budget. `agent/runtime.py` owns the model-and-Tool loop, while
private `agent/prompt.py` owns request assembly and 80% Tool-free compaction
around the shared `AgentContext` history implementation.

### `restscope/target_http/`

The shared low-level target HTTP boundary. `request.py` validates target paths
and prepares headers, `transport.py` sends requests and bounds responses, and
`observation.py` offers completed exchanges to the Behavior Monitor without
making testing decisions.

### `restscope/llm/`

Provider-independent language-model contracts and clients.

- `schemas.py` defines messages, requests, responses, tools, and model config.
- `client.py` selects a provider and creates LLM trace spans.
- `model_selector.py` maps semantic roles to thinking or fast models.
- `output_validator.py` parses and validates structured output.
- `providers/` translates the common request into provider-specific calls.

### `restscope/db/`

Owns database setup, ORM records, migrations, and domain-adjacent SQLAlchemy
Adapters under `db/adapters/`. Repositories are narrow storage APIs; callers
should not manipulate ORM rows directly. The
single baseline creates 19 business tables. Every SQLite connection enables
foreign keys, and the default App always rejects existing database paths.

### `restscope/observability/`

Creates Phoenix/OpenTelemetry spans, explicit logging, redaction, and the
independent current-run live event narrative. `runtime.py` is the business-code
tracing seam; `observer.py` owns current-run state and event ordering;
`span.py`, `http_exchange.py`, and `projection.py` own their focused adapters
and projections. Phoenix retains its lower-level spans;
the browser schema exposes only `agent_turn`, `tool_call`, and `smoke_batch`.
Both outputs are optional and fail-open.

### `restscope/ui/` and `ui/`

`restscope/ui/` adapts the live observer to loopback-only snapshot, SSE, and
static GET routes. The top-level `ui/` directory contains the React/TypeScript/
Ant Design source; its Vite build is versioned under `restscope/ui/static/` so
Python installations do not require Node.js at runtime. The page has no command
or write route. Its live state comes from server memory; `runHistory.ts` may
additionally retain the latest five complete snapshots in same-origin browser
IndexedDB so a tester can reopen recent visual evidence. This local cache
contains every detail visible in the UI, including already-redacted raw Provider
Reasoning and target credentials, and is never read by the backend.
`conversationProjector.ts` selects only the explicit Main Agent and projects
incremental prompt prose, default-expanded title-free Reasoning, model responses,
collapsed Tool calls, and child-Profile Drawer entries. Tool Call and Tool
Result messages are excluded from prose because the matching Tool row owns
their detail. `ConversationView.tsx` owns dynamic-height virtualization and
keeps all icons and document text on one left edge; the App owns the nested
Subagent Drawer and three-level breadcrumb. `FloatingTodo.tsx` and
`TodoPanel.tsx` render only the explicit Main
Agent's latest successful generic `plan.update`; Failure Resolution Worklist
writes remain ordinary Tool events and never replace Todo. Smoke Batches stay
available in schema-v2 history but outside the conversation document.

### `evaluations/`

Provides the developer-only Phoenix Evals entrypoint for the continuous
Resolution boundary. `registry.py` exposes one suite; `core.py` owns only
Dataset synchronization, prompt selection, and Experiment metadata; and
`agents/resolution/` owns Scenario DTOs, temporary collaborators, the Phoenix
task, code evaluators, and YAML evidence. It reuses production Resolution,
Parameter Patch, and Review Agents but never imports a database Adapter or sends
target HTTP requests.

### `tests/`

Tests are executable behavior examples. Fixture helpers construct small OpenAPI
operations and fake providers; focused files protect module contracts; files
ending in `_live.py` are opt-in and can call real services.

## 6. How to follow one request

For a normal generated Smoke case:

```text
RESTScopeApp
  -> HarnessRuntime.start_main_agent("main")
  -> Agent.start()
  -> AgentPromptSession taskless bootstrap
  -> LLMClient
  -> authorized plan.read / plan.update
  -> internal AgentCompletion ends the blocking call
```

The first production Main Profile has no testing Skill or domain Tool, so this
is currently a runtime/lifecycle path rather than an executable Smoke path.
`OperationSmokeCoordinator`, `OperationTestingService`, Failure Resolution, and
Parameter Patch remain readable transitional Modules but are not selected by
the Main Profile.

For a failed Batch that receives an applied Patch:

```text
BatchExecutionResult + TestCaseCatalog
  -> deterministic exact-message folding into E* sources
  -> one continuous FailureResolutionAgent for the failed Batch
  -> Agent-owned reference-only worklist (merge/split/reorder/reopen)
  -> optional Catalog, OpenAPI, Parameter-memory, and HTTP tools
  -> internal ParameterPatchCoordinator tool
  -> Patch proposal + compile/sample + independent Review
  -> reviewed candidate retained in the registry behind P*
  -> Agent finishes when its worklist is ready
  -> final coverage and multi-candidate mechanical validation
  -> atomic Failures + Attempts + Generator/Constraint + change events write
  -> next complete Batch
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
- **Database access:** optimize inside repositories; preserve the approved
  structured Memory boundary and never persist raw transcripts or Plans.
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
