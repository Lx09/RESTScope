# Test inputs and data sources

## Use for

Use this Reference to explain where a request input obtains reusable values or
which input-generation rules were frozen for a historical grouped test run.

## RESTScope storage mapping

`operation_input_sources` stores exact producer-to-consumer propositions for
request inputs. `RESOURCE` means an input is resolved from a complete current
API resource instance; `VALUE_REUSE` means it is parsed from an exact field in
a retained successful response. RESTScope calls one input value strategy a
**Generator** and one cross-input rule a **Constraint**. Historical immutable
snapshots of those rules are stored in `abstract_test_cases` and referenced by
the summary of a grouped test run in `batches`.

The database does not store the current mutable test-input configuration or a
materialized pool of reusable response values.

## Query recipes

Trace exact data sources for one consumer endpoint:

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

Find the immutable input-rule snapshot used by one historical test run:

```sql
SELECT
  batches.batch_id AS test_run_id,
  abstract_test_cases.abstract_test_case_id AS input_rule_snapshot_id,
  abstract_test_cases.operation_id,
  abstract_test_cases.state_digest,
  abstract_test_cases.created_at
FROM batches
JOIN abstract_test_cases
  ON abstract_test_cases.abstract_test_case_id =
     json_extract(batches.summary, '$.abstract_test_case_id')
WHERE batches.batch_id = :test_run_id
LIMIT 1
```

Read the complete historical rules only when needed:

```sql
SELECT generators_json, constraints_json
FROM abstract_test_cases
WHERE abstract_test_case_id = :input_rule_snapshot_id
LIMIT 1
```

## Interpret results

- An abstract test case is a historical input-rule snapshot, not an executed
  test case and not the current in-memory configuration.
- A state digest identifies complete Generator/Constraint content but does not
  prove that generated requests succeeded.
- Input-source rows are propositions recorded when validated configuration is
  applied. Actual values are resolved later from retained evidence.
- Use `request_generation.get_input_state` for current configuration when that
  Tool is available; SQL can answer only the historical audit question.
