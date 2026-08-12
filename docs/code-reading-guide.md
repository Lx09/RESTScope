# RESTScope code-reading guide

This guide is the shortest reliable map of the current repository. Historical
task records explain earlier experiments, but the files named here describe the
runtime that exists now.

## 1. The shortest mental model

RESTScope starts one long-lived generic Main Agent. The Main Profile currently
grants only a private in-memory Plan, so the new testing capabilities are built
and bound but are not yet activated for production Agent use.

The runtime has five distinct responsibilities:

1. `restscope.openapi_parser` turns an OpenAPI document into an in-memory
   representation (IR).
2. `restscope.request_generation` owns request-input identities, Generator and
   Constraint semantics, compilation, deterministic sampling, and one
   revisioned App-lifetime configuration Store.
3. `restscope.harness.operation_testing` freezes one generation revision,
   prepares a complete Batch, and sends its requests.
4. `restscope.tools` exposes narrow model-callable behaviors. Profiles—not the
   global Catalog—decide which Tools an Agent may call.
5. `restscope.api_behavior_monitor` owns the nine-table evidence/audit Catalog
   and the ordered response-processing flow.
6. `restscope.agent`, `restscope.skills`, and `restscope.harness` run the same
   generic Agent as a Main Agent, Subagent, or System Agent. Skills teach a
   method but do not execute code or grant Tools.

Operation Smoke and its dedicated Failure Resolution, Patch, Review, Compact,
candidate, Finalizer, and Memory Modules have been retired. Do not look for a
hidden compatibility workflow.

## 2. Important domain words

### Operation key

An operation key is the normalized HTTP method and OpenAPI path template, for
example `POST /orders`.

### Semantic input handle

A semantic input handle identifies one request input in terms a model can use,
such as `query.limit` or a request-body property path. Internal input-node IDs
remain an implementation detail and never cross the new Tool boundary.

### Generator

A Generator describes the possible values for one input and its inclusion
probability. Strategies include constants, numeric or string ranges, choices,
resource identity fields, and values observed at an exact producer response
source.

### Resource definition, instance, and input source

A Resource definition has one normalized name and immutable direct identity
fields. A Resource instance uses typed canonical JSON over those fields as its
identity and keeps recursively merged current state. An operation input source
connects one consumer input to an exact producer operation, actual status,
media type, selector, and field as either RESOURCE or VALUE_REUSE. Composite
resource fields always come from one complete current instance.

### Observation and Abstract Test Case

An Observation is one complete valid 2xx JSON response plus its sanitized
actual request. An Abstract Test Case is the immutable Generator/Constraint
state used by a Batch. It is created before network execution, and every
eligible response from that Batch references it.

### Constraint

A Constraint describes a relationship that involves multiple inputs, such as
equality, ordering, implication, or a Boolean combination. A single input's
value domain belongs in its Generator rather than a Constraint.

### Patch

A Parameter Patch is a complete semantic replacement for the affected
Generators and their intersecting active Constraint closure. It is not a text
diff. Validation is read only; application is the sole state mutation and
changes only future request generation.

### Revision and digest

Each operation starts at revision `0`. Its state digest identifies the complete
current Generator/Constraint state. A validation digest identifies the exact
revision, Patch, seed, sample count, and validation result. These values prevent
a stale or changed Patch from being applied.

## 3. Read the main runtime in this order

1. `restscope/main.py` — installed command, target arguments, exit codes, and
   complete process lifetime. Then read `restscope/app/runtime.py` for the
   embeddable App lifecycle, `restscope/app/target.py` for target validation,
   and `restscope/app/composition.py` only for the production object graph.
2. `restscope/agent/profile.py` and `restscope/agent/runtime.py` — Profile
   authorization and the generic model/Tool loop.
3. `restscope/harness/runtime.py` — Profile graph validation, Tool binding,
   Context, Subagent lifecycle, and repeatable synchronous System Agent roots.
   App-owned domain runtimes arrive already constructed. `build_harness()`
   returns the concrete `HarnessRuntime` used directly by the App; there is no
   second App-private Harness Protocol.
4. `restscope/request_generation/store.py` — revisioned operation state,
   snapshots, locks, and atomic replacement.
5. `restscope/request_generation/parameter_patch/models.py` — semantic Patch
   language.
6. `restscope/request_generation/parameter_patch/runtime.py` — state reads,
   validation orchestration, deterministic samples, digests, and atomic Apply.
7. `restscope/harness/operation_testing/service.py` — frozen-revision Batch
   generation and execution.
8. `restscope/tools/request_generation/`, `restscope/tools/parameter_patch/`,
   and `restscope/tools/test_case/` — the Agent-visible contracts.
9. `restscope/builtin_skills/apply-parameter-patch/` and
   `restscope/builtin_skills/resolve-operation-failures/` — progressively
   disclosed Agent methods.

## 4. Package map

### `restscope/app/`

The App is the only production composition root. `runtime.py` owns only
initialize/start/close state and the optional UI URL. `target.py` validates the
OpenAPI and target HTTP inputs. `profiles.py` owns App-specific Agent Profiles.
`composition.py` privately creates and closes the database-backed Catalog,
Generation Store, Target API Client, Harness, optional UI, and tracing. Audit
data remains in the Catalog rather than becoming App query methods.

### `restscope/openapi_parser/`

This package loads, resolves, validates, and normalizes OpenAPI into the IR used
by every downstream Module. `openapi.list_operations` and Schema-query Tools
read this initialized state through a narrow backend.

### `restscope/operation_references/`

Request and response references give stable semantic paths inside one
operation. Request references become Tool-facing handles; response references
identify observed producer fields.

### `restscope/request_generation/`

This is the owner of the whole request-generation language:

- `models.py` defines operation snapshots, input nodes, Generators, and
  Constraints.
- `generators.py`, `constraints.py`, `compiler.py`, and `solver.py` compile and
  solve executable cases.
- `store.py` keeps current state per operation under an App-lifetime lock.
- `parameter_patch/models.py` defines the semantic replacement accepted by Tools.
- `parameter_patch/compiler.py` owns pure semantic and Constraint compilation.
- `parameter_patch/projection.py` owns bounded model-facing output.
- `parameter_patch/runtime.py` exposes state read, validation, and atomic Apply.
- `reference_values.py` owns `BehaviorMonitorReferences`: it resolves exact
  input sources from retained observations or complete current resource
  instances, then stages the final source rows around Patch publication. It
  does not maintain a shared value pool.

The Store is not persistent and records no Patch history or rollback state.

### `restscope/harness/operation_testing/`

`OperationTestingService` freezes a complete Store snapshot and current exact
reference values before generating the whole Batch. After every request passes
preflight, it persists or reuses one Abstract Test Case before the first
network call. `outcomes.py` and `failure.py` define bounded inline evidence.
There is no concrete per-case registry or `TC*`/`E*` identity layer.

### `restscope/tools/`

Each subject package owns its complete Tool schema, execution binding, output
validation, bounding, and safe failures. The important new Tools are:

- `openapi.list_operations` — discover initialized operations.
- `request_generation.get_input_state` — read selected Generator state and the
  complete intersecting Constraint closure.
- `request_generation.validate_patch` — compile and sample without mutation.
- `parameter_patch.apply` — the only generation-state mutation Tool.
- `test_case.run_batch` — execute 1–5 cases from one frozen revision and return
  inline results.

`restscope.http.request` remains a separate high-risk single-request Tool. Tool
availability never substitutes for authorization to call a live target.

### `restscope/builtin_skills/`

Built-in Skills are standard directories discovered from package data:

- `apply-parameter-patch` teaches state read, complete Patch construction,
  deterministic validation, value-level review, atomic apply, and confirmation.
- `resolve-operation-failures` teaches diagnosis of one operation's inline Batch
  evidence and delegation to an authorized Patch child Profile.

`skill.read` reveals only the selected `SKILL.md`; `file.read` reveals only a
directly linked first-level Markdown Reference registered at startup. Neither
Tool grants the domain Tools named by a Skill.

### `restscope/agent/` and `restscope/harness/`

There is one generic Agent implementation for Main, child, and System sessions.
A Profile selects a model, ordered Tools, Skills, Context Sources, child
Profiles, and bounded instructions. A registered `SystemAgentDefinition` binds
only the expected result contract and task adapter; it does not grant
capabilities. Every System invocation is a fresh root and has no token budget
limit, while the Harness still records usage and validates final output until
it is valid or a terminal runtime event occurs. The Harness performs mechanical
validation and execution but does not decide testing semantics.

The Main Profile is intentionally plan-only today. The new testing Tools and
Skills cannot be used by it until a later approved Profile change.

### `restscope/api_behavior_monitor/`

The API Behavior Monitor checks every matched response contract, persists
complete valid 2xx JSON observations, and then derives resource state in a
separate transaction. `catalog.py` owns all nine persisted tables, including
the current normalized OpenAPI and append-only contract-change events.
`contract_monitor.py` checks and updates response contracts;
`coordinator.py` owns stage ordering; `resource_monitor.py` derives resources;
and `resource_identity.py` contains the bounded System Agent contract used for
an unknown identity. The Monitor never calls an LLM client directly and does
not persist extraction rules or reasoning.

Parameter Patch stores exact RESOURCE or VALUE_REUSE source propositions with
neutral Beta(1,1) evidence. Values are resolved on demand from raw observations
or current non-deleted instances. There is no response-value pool or second
response-source System Agent.

### `restscope/data_types/`

This small boundary defines recursive JSON value and object types shared by
parsers, Monitor observations, and model result contracts. It exists so JSON is
described precisely without the forbidden `typing.Any`; opaque non-JSON
provider objects remain `object` until their owning Adapter validates them.

### `restscope/target_api/`

This top-level Module is the shared foundation for every request to the tested
API. `request.py` prepares and validates a request without network effects;
`client.py` sends it and independently supplies the complete Monitor fact, a
bounded Live Observer view, and the caller's requested body. The HTTP Tool and
generated Batch execution consume the same Client; Harness does not own it.

### `restscope/db/`

The one baseline migration creates nine business tables. Their SQLAlchemy
mappings live together in `orm/api_behavior_monitor.py`, and the sole concrete
transaction Adapter is `adapters/api_behavior_monitor.py`. Two tables retain
OpenAPI audit/export facts; seven retain response and resource evidence.
There are no response-value pools, extraction-rule, concrete Test Case,
Failure, Attempt, plan, queue, or candidate tables.

### `restscope/observability/`, `restscope/ui/`, and `ui/`

Observability records redacted Agent turns, Tool calls, Subagent relationships,
System Agent roots, HTTP exchanges, and the Main Plan. Browser schema-v3 has
only `agent_turn` and `tool_call` events. A System root keeps an empty
`parent_session_id` but uses the active HTTP Tool's `parent_event_id`, allowing
the UI to nest one or more System conversations under that Tool without copying
events. Batch and Patch Apply are ordinary Tool cards. Same-origin IndexedDB
retains at most five complete v3 snapshots and ignores older schemas.

## 5. Follow a Patch from diagnosis to target evidence

```text
inline Batch evidence
  -> parent diagnoses root cause and value predicates
  -> Patch child reads current revision and Constraint closure
  -> child constructs one complete semantic replacement
  -> validate_patch compiles and generates deterministic witnesses
  -> child reviews every predicate against the full generated domain
  -> parameter_patch.apply revalidates and atomically advances the revision
  -> child and parent independently read the new state
  -> parent runs a new complete Batch against the target
```

The apply step proves only that RESTScope's in-memory generation state changed.
The later Batch provides target evidence; it is not an automatic proof that
every semantic predicate was correct. A failed Batch does not roll back state.

## 6. Where to add or change behavior

- Change Generator or Constraint meaning in `request_generation`, then expose
  only the smallest needed Tool behavior.
- Change Agent methodology in the relevant built-in Skill and its References.
- Change authorization in a Profile; never infer it from Catalog discovery.
- Change persistence only in the API Behavior Adapter and baseline
  migration after explicit approval.
- Change tested-API request behavior in `target_api`, not inside a Tool, Skill,
  Agent, or Harness.

When a proposed shortcut would recreate a dedicated workflow Agent, candidate
Registry, Test Case Registry, or persistent Generation state, stop and compare
it with the accepted retirement ADR first.

## 7. How comments should help

Production modules begin with a plain-language responsibility and flow
docstring. Public Interfaces and non-trivial helpers explain their important
inputs, outputs, mutations, failure boundaries, and security consequences.
Nearby comments explain why a validation, lock, transaction, or bounded-output
branch exists; they should not restate Python punctuation.
