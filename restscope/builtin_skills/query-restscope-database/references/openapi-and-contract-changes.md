# OpenAPI and contract changes

Use this Reference for the normalized operation catalog, the current durable
OpenAPI audit document, and append-only response-contract changes caused by
Observations.

## Normalized operations

```sql
SELECT operation_id, method, path, description
FROM operations
ORDER BY path, method, operation_id
LIMIT :limit
```

## Contract change history

```sql
SELECT
  id,
  operation_id,
  status_code,
  media_type,
  changes,
  created_at
FROM openapi_change_events
WHERE (:operation_key IS NULL OR operation_id = :operation_key)
ORDER BY created_at DESC, id DESC
LIMIT :limit
```

Inspect the changed response schemas only after selecting a specific event:

```sql
SELECT response_before, response_after
FROM openapi_change_events
WHERE id = :event_id
LIMIT 1
```

## Current normalized document

```sql
SELECT singleton_id, created_at, updated_at
FROM openapi_current
LIMIT 1
```

Read `document` separately only when a bounded OpenAPI Tool cannot answer the
question:

```sql
SELECT document
FROM openapi_current
WHERE singleton_id = 1
LIMIT 1
```

The complete document commonly exceeds the 4,000-character cell limit. Prefer
`openapi.list_operations` and the schema lookup Tools for model-facing slices.

## Interpretation rules

- `openapi_current` is the latest normalized audit document, not App recovery
  state and not a static execution plan.
- Change events are append-only evidence of contract widening. A missing event
  means RESTScope recorded no change, not that the target API never changed.
- `operation_id` stores RESTScope's normalized operation key such as
  `GET /orders`, regardless of the source document's optional `operationId`.
