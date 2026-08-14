# API contracts and changes

## Use for

Use this Reference to inspect known API endpoints, the current normalized
OpenAPI document, or response-contract changes learned from executed requests.

## RESTScope storage mapping

RESTScope stores normalized endpoints in `operations`, the latest complete
normalized OpenAPI document in the singleton `openapi_current` row, and
append-only response-contract changes in `openapi_change_events`. A change event
records how one executed response widened the known response contract; it is
audit evidence, not a static execution plan.

## Query recipes

List normalized endpoints:

```sql
SELECT operation_id, method, path, description
FROM operations
ORDER BY path, method, operation_id
LIMIT :limit
```

List response-contract changes, optionally for one endpoint:

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

Inspect the before/after schemas only after selecting one event:

```sql
SELECT response_before, response_after
FROM openapi_change_events
WHERE id = :event_id
LIMIT 1
```

Inspect current document metadata:

```sql
SELECT singleton_id, created_at, updated_at
FROM openapi_current
LIMIT 1
```

Read the complete document only when a bounded OpenAPI Tool cannot answer the
question:

```sql
SELECT document
FROM openapi_current
WHERE singleton_id = 1
LIMIT 1
```

## Interpret results

- The complete OpenAPI document commonly exceeds the 4,000-character cell
  limit. Prefer bounded OpenAPI Tools for small model-facing slices.
- A missing change event means RESTScope recorded no change; it does not prove
  the target API never changed.
- `operation_id` is RESTScope's normalized endpoint key such as
  `GET /orders`, independent of an OpenAPI `operationId` value.
