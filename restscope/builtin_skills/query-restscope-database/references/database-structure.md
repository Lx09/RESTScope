# Database structure

## Use for

Use this Reference when the question asks which tables or views exist, which
columns they expose, or how stored records are declared to relate. Discover the
current structure before relying on a recipe whose schema may have changed.

## RESTScope storage mapping

SQLite exposes table and view declarations through `sqlite_schema`.
RESTScope's business concepts live in ordinary application tables; the
`alembic_version` table only records migration state. Metadata is executable
evidence about storage shape, not proof of the business meaning of a row.

## Query recipes

List readable objects:

```sql
SELECT name, type
FROM sqlite_schema
WHERE type IN ('table', 'view')
  AND name NOT LIKE 'sqlite_%'
ORDER BY type, name
LIMIT :limit
```

Start with `{"limit": 100}`. Inspect one declaration using the exact discovered
name:

```sql
SELECT name, type, sql
FROM sqlite_schema
WHERE name = :object_name
LIMIT 1
```

When only output column names are needed, copy the exact discovered name into a
quoted identifier and request no rows:

```sql
SELECT *
FROM "exact_discovered_table_name"
LIMIT 0
```

Identifiers cannot be parameters. Preserve double quotes and double any quote
inside a discovered identifier before using the last form.

## Interpret results

- A declared foreign key proves a storage relationship, not its semantic
  meaning.
- JSON values are stored as SQLite text. Prefer `json_extract` for small scalar
  facts instead of returning a large document.
- Never use `PRAGMA` or `pragma_*()`; `database.query` deliberately rejects
  both.
- If current metadata and another Reference disagree, use metadata and report
  the mismatch instead of guessing a replacement join.
