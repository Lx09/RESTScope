# Schema discovery

Use this Reference before relying on a table or column that may have changed.
SQLite metadata is current executable evidence; recipes in the other References
describe the present baseline but do not override it.

## List readable objects

```sql
SELECT name, type
FROM sqlite_schema
WHERE type IN ('table', 'view')
  AND name NOT LIKE 'sqlite_%'
ORDER BY type, name
LIMIT :limit
```

Start with `{"limit": 100}`. The App currently owns twelve business tables;
`alembic_version` is migration bookkeeping rather than behavior evidence.

## Inspect one object's declaration

```sql
SELECT name, type, sql
FROM sqlite_schema
WHERE name = :object_name
LIMIT 1
```

Pass the exact name returned by the first query as `object_name`. The `sql`
field contains SQLite's current `CREATE TABLE` or `CREATE VIEW` declaration,
including declared types, keys, constraints, and foreign keys.

When only the stable output column names are needed, copy the exact discovered
table name into a quoted identifier and request no rows:

```sql
SELECT *
FROM "exact_discovered_table_name"
LIMIT 0
```

Identifiers cannot be bound as parameters. Use this second form only after the
first query returned the exact object name; preserve double quotes and double
any embedded quote. If metadata and a recipe disagree, use metadata and report
the mismatch rather than guessing a replacement join.

## Interpretation rules

- A declared foreign key proves an enforced storage relationship, not the
  semantic meaning of the related rows.
- JSON columns are stored as SQLite text. Use `json_extract` for small scalar
  fields instead of returning a complete large document by default.
- Never use `PRAGMA ...` or `pragma_*()`; SQLite authorizes both as PRAGMA
  operations and `database.query` deliberately denies them.
