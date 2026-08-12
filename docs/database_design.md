# RESTScope Database Design

Status: Active exploratory design (2026-08-11)

RESTScope creates one SQLite file for one App. The file is an evidence and audit
artifact, not a recovery image: a later App rejects the existing path and never
resumes it. The fresh baseline contains nine business tables plus Alembic's
`alembic_version` table.

## Boundary

- `restscope.api_behavior_monitor.catalog` is the single database-independent
  Interface for the current normalized OpenAPI, append-only contract changes,
  operations, observations, resources, input sources, and abstract test cases.
- `restscope.request_generation` keeps the current revisioned
  Generator/Constraint state in memory. Exact source bindings participate in
  that state, but producer values are parsed from observations on demand.
- `restscope.db` owns one API Behavior SQLAlchemy Adapter, one ORM module,
  foreign-key setup, and the one fresh baseline migration. The concrete
  Repository stays private; only the Unit of Work is an infrastructure entry.
- LLM reasoning, extraction rules, Patch history and samples, Failures, plans,
  queues, scheduler state, and restorable Agent state never enter SQLite.

Every SQLite connection enables foreign keys. Fresh database creation finishes
with integrity and foreign-key checks. Request headers conventionally carrying
authorization, cookies, tokens, API keys, or secrets are removed before an
observation is written.

## API Behavior Catalog: 9 tables

All nine tables belong to one Catalog and one SQLAlchemy transaction family.
The headings below group their contents for explanation; they are not separate
runtime repositories or App collaborators.

### OpenAPI audit facts: 2 tables

#### `openapi_current`

The singleton row stores the complete normalized OpenAPI document. A real
observed response-contract change replaces it atomically with its change event.

#### `openapi_change_events`

Each real response-contract change records operation text, actual status,
normalized media type, change labels, and affected Response before/after data.
Contract matches, pending retries, and internal check failures add no event.

### Response and resource evidence: 7 tables

#### `operations`

`operation_id` is the normalized `METHOD /path` primary key. `method + path` is
also unique; `description` is refreshed from the current OpenAPI IR.

#### `resources`

`resource_id` is the database identity. `name` is the unique lowercase
alphanumeric value formerly called `normalized_name`. `identity_fields` is an
immutable sorted list of direct response properties. Each instance must contain
every field as a string or non-Boolean integer.

#### `operation_resource_edges`

The primary key is `(operation_id, resource_id, role)`. Roles describe how the
response uses the resource: `CREATED`, `REFERENCED`, `UPDATED`, or `DELETED`.
`_alpha` and `_beta` store the neutral Beta(1,1) proposition evidence. This
version defines no evidence-update policy or Tool.

#### `resource_instances`

The primary key is `(resource_type, resource_instance_id)`, where
`resource_type` stores `resources.name` and the instance ID is canonical typed
JSON over all identity fields. `current_state_json` is updated incrementally:
missing properties stay, nested objects merge recursively, arrays replace as a
whole, and a new null never overwrites old state. `_deleted=true` is logical
deletion and deleted instances are hidden from ordinary generation and Tools.

#### `observations`

Each eligible response stores `observation_id`, operation, completion time,
actual status/media type, a sanitized actual request envelope, and the complete
original valid JSON response text. An optional `abstract_test_case_id` links a
generated request to its immutable configuration. In the same insertion
transaction, rows older than the newest 100 for that operation are physically
deleted. There is no per-response JSON-size or flattened-scalar limit.

#### `operation_input_sources`

The composite primary key records consumer operation/input, producer
operation, concrete successful status, normalized media type, selector,
display field name, and `consume_type` (`RESOURCE` or `VALUE_REUSE`). `_alpha`
and `_beta` start at Beta(1,1) and are not updated in this version. Two consume
types may coexist for identical response coordinates.

No response-value table exists. A VALUE_REUSE Generator selects typed scalars
from matching retained observations when it needs values. A RESOURCE Generator
reads current non-deleted instances and uses one shared per-case resource seed,
so composite identity components never form an unobserved combination.

#### `abstract_test_cases`

One immutable row per `(operation_id, state_digest)` stores the complete
Generator configuration, exact reference bindings, and Constraints. Batch
preflight writes or reuses it after every request is generated and serialized
but before the first network call. It is audit metadata, not a restorable
Generation Store or per-concrete-case registry.

## Response processing order

For every matched response, Contract Monitor runs first. Internal Contract
Monitor failure becomes a warning and does not block a valid observation. A
complete valid 2xx JSON response then commits as the factual observation. Only
after that commit does Resource Monitor derive resource definitions, role
edges, and current instances in a separate transaction; resource failure never
removes the observation.

Unknown resource groups ask the bounded Resource Identifier System Agent for
direct identity fields. Existing unambiguous definitions are reused. No model
reasoning or extraction rule is persisted.

## Lifecycle and compatibility

The default App accepts only a nonexistent local SQLite file, claims it before
migration, and retains it after close. Existing files, directories, and
symlinks are rejected unchanged. Alembic has one `0001_current_baseline` and no
old-database data migration. Retired Resource Catalog and Response Value pool
schemas are intentionally incompatible with this fresh baseline.

## Deferred design

- Evidence update semantics, confidence-based selection, merging, or decay.
- Database encryption, secure deletion, automatic vacuuming, and artifact
  reopening or restoration.
- Persisted extraction rules, plans, operation graphs, scheduler queues, or
  Agent memory.
