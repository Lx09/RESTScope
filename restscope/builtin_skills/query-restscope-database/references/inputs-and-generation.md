# Inputs and generation

Use this Reference to trace exact producer-to-consumer input propositions or
inspect the immutable Generator and Constraint snapshot used by a past Batch.
The database does not store the current mutable generation configuration.

## Exact operation input sources

```sql
SELECT
  operation_input_sources.consumer_operation_id,
  operation_input_sources.consumer_input_node_id,
  operation_input_sources.consume_type,
  operation_input_sources.producer_operation_id,
  operation_input_sources.status_code,
  operation_input_sources.media_type,
  operation_input_sources.selector,
  operation_input_sources.field_name
FROM operation_input_sources
WHERE operation_input_sources.consumer_operation_id = :consumer_operation
ORDER BY operation_input_sources.consumer_input_node_id,
         operation_input_sources.consume_type,
         operation_input_sources.producer_operation_id
LIMIT :limit
```

`RESOURCE` uses a complete current Resource instance. `VALUE_REUSE` parses one
exact observed producer field. `_alpha` and `_beta` are stored evidence counters,
not values or a materialized response pool.

## Immutable state used by a Batch

```sql
SELECT
  batches.batch_id,
  abstract_test_cases.abstract_test_case_id,
  abstract_test_cases.operation_id,
  abstract_test_cases.state_digest,
  abstract_test_cases.created_at
FROM batches
JOIN abstract_test_cases
  ON abstract_test_cases.abstract_test_case_id =
     json_extract(batches.summary, '$.abstract_test_case_id')
WHERE batches.batch_id = :batch_id
LIMIT 1
```

Read the full abstract definitions only when needed:

```sql
SELECT generators_json, constraints_json
FROM abstract_test_cases
WHERE abstract_test_case_id = :abstract_test_case_id
LIMIT 1
```

## Interpretation rules

- An Abstract Test Case is a durable snapshot used by past execution, not the
  current in-memory Request Generation state.
- A state digest identifies a complete Generator/Constraint state but does not
  prove that its generated HTTP requests succeeded.
- Input sources are propositions recorded when a validated Patch is applied.
  Actual reused values are resolved later from retained evidence and are not
  persisted in a shared pool.
- Use `request_generation.get_input_state` when an authorized Profile needs the
  current revision rather than historical audit evidence.
