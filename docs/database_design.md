# RESTScope Database Design

Status: Active exploratory baseline (2026-07-19)

The database currently owns only durable OpenAPI source locations or content.
Operation facts and dependencies remain deliberately undefined until their
update and identity contracts are approved.

## Boundary

- `restscope.catalog` owns domain DTOs, validation, repository protocols, and
  the `SchemaCatalog` application service.
- `restscope.db` owns SQLAlchemy ORM mappings, sessions, migrations, and the
  concrete repository and unit-of-work adapters.
- Catalog domain code must not import SQLAlchemy, database configuration, or
  concrete persistence adapters.
- A composition root wires `SchemaCatalog` to the configured database.

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

## Lifecycle

The supported operations are register, get by ID, list, replace the whole
source, and load the current source into `OpenAPISpecIR`. Deletion and partial
updates are outside the current scope.

The migration history is a destructive single-table baseline and does not
upgrade databases created by the former ten-table MVP.

## Deferred design

- `operations`: fact fields, stable identity, and synchronization semantics.
- `operation_dependencies`: intended to be read and replaced as a unit per
  consuming operation, with input entries containing candidate producers; the
  entry contract is intentionally not defined yet.
