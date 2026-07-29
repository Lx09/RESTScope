# RESTScope Database Design

Status: Active exploratory design (2026-07-23)

The database owns durable OpenAPI source locations/content and generator
configuration for one current API. It does not persist parsed IR, operation
facts, inferred dependencies, test plans, scheduler state, test cases, or test
reports.

## Boundary

- `restscope.catalog` owns schema-source DTOs, validation, repository protocols,
  and the `SchemaCatalog` application service.
- `restscope.testing` owns frozen operation request snapshots, generator
  contracts, generation, serialization, and operation execution.
- `restscope.db` owns SQLAlchemy ORM mappings, sessions, migrations, and the
  concrete repository and unit-of-work adapters.
- Domain code must not import SQLAlchemy, database configuration, or concrete
  persistence adapters.
- Composition roots wire both catalogs to the configured database.

## `schemas`

| Column | Type | Rule |
| --- | --- | --- |
| `id` | string | Primary key using `schema_<uuid>` |
| `file_path` | text, nullable | Absolute path to an OpenAPI file |
| `raw_content` | text, nullable | Verbatim JSON or YAML content |
| `created_at` | timestamp | Set when registered |
| `updated_at` | timestamp | Set when the source is replaced |

A check constraint requires exactly one of `file_path` and `raw_content`.
File-backed rows do not contain snapshots: each load reads the current file.

Sources are parsed and checked for parser error diagnostics before registration
or replacement. Parsed IR, diagnostics, catalog status, and operation counts are
not persisted.

## Generator configuration

`generator_catalog_state` is a singleton initialization marker.
`RESTScopeApp.initialize()` creates it together with every initial operation
and input generator in one transaction. The default App never opens an existing
database, so the marker is an internal Catalog invariant rather than a
cross-process App reuse mechanism.

`operation_generator_configs` stores one frozen test model per original
`OperationIR.operation_key`: method, path, parameter serialization rules,
request-body media contracts, input tree, local schema constraints,
enabled/disabled reasons, active media type, and a monotonically increasing
configuration revision. There is intentionally no `schema_id`: one deployment
and database serve one API lifecycle.

`input_generator_configs` stores the complete one-to-one generator set for that
frozen operation. Each row references only the stable `input_node_id`, its
inclusion probability, and its discriminated generator strategy.

`generator_config_revisions` stores the complete immutable configuration for
each revision. Only the initial configuration and directly accepted revisions
exist; there is no pending candidate, Effect evaluation, rejected state, or
compensating rollback revision. Operation Smoke records the reason for an
accepted revision in its separate Applied Patch memory. Test cases, response
bodies, and failure reports are not persisted.

Whole-set replacement and node-level patch both use an expected revision, a
database compare-and-swap update, and one transaction. Concurrent writers with
an old revision fail instead of overwriting a newer revision. Required or
structural nodes must have inclusion probability `1.0`; optional nodes may use
`0.0` through `1.0`.

There is no OpenAPI synchronization after initialization. Later constraint,
input, or operation changes neither alter nor invalidate the frozen Catalog.
An operation removed from the current IR remains executable from its stored
request snapshot, using only the current App's target base URL and headers.

## Lifecycle

Schema sources support register, get, list, whole-source replace, and load into
`OpenAPISpecIR`. Generator configurations support inspect, whole-set replace,
node-level patch, and load for preflight generation. There is no delete/reset
tool.

The default DB-backed `RESTScopeApp` accepts only a nonexistent local file
SQLite target. Construction resolves relative paths from the startup working
directory, exclusively creates the file, and upgrades it to the packaged
Alembic head before composing capabilities. Existing paths and unsupported
database URLs fail without modification. Construction failures remove only the
database and SQLite sidecars created by that attempt; successful construction,
`initialize()` failures, and `close()` retain the database.

Each retained database is therefore a one-run artifact. A later App start must
use a new URL or follow an explicit operational inspect/delete workflow.
Injecting a complete custom `CapabilityRuntime` bypasses this lifecycle because
the caller owns its persistence.

The migration history has a schema-source baseline, generator configuration
revision `0002`, resource catalog revision `0003`, and generator history
revision `0004`. Revision `0004` backfills each existing active generator
configuration as one accepted baseline revision. It does not upgrade databases
created by the former ten-table Planner MVP.

## Deferred design

- Multi-API generator namespaces and schema version history.
- An explicit operational Catalog rebuild workflow.
- `operations`: persisted fact fields and synchronization semantics.
- `operation_dependencies`: intended to be read and replaced as a unit per
  consuming operation, with input entries containing candidate producers; the
  entry contract is intentionally not defined yet.
