# Batches and Observations

Use this Reference for executed request evidence, Batch membership, HTTP status,
transport failures, and bounded response content. An Observation ID is also the
durable executed Test Case ID.

## List one Batch's cases

```sql
SELECT
  observations.batch_case_index,
  observations.observation_id,
  observations.operation_id,
  observations.outcome_kind,
  observations.status_code,
  observations.media_type,
  observations.transport_code,
  observations.timestamp
FROM observations
WHERE observations.batch_id = :batch_id
ORDER BY observations.batch_case_index
LIMIT :limit
```

## Inspect one Observation's metadata and request

```sql
SELECT
  observation_id,
  operation_id,
  timestamp,
  outcome_kind,
  status_code,
  reason_phrase,
  media_type,
  request_json,
  body_format,
  transport_code,
  transport_message,
  abstract_test_case_id,
  batch_id,
  batch_case_index,
  replay_of_observation_id
FROM observations
WHERE observation_id = :observation_id
LIMIT 1
```

## Read response content in separate calls

Read complete response headers as the only selected column:

```sql
SELECT response_headers
FROM observations
WHERE observation_id = :observation_id
LIMIT 1
```

The Tool verifies the returned JSON is a complete stored header mapping and
redacts sensitive values. Do not add the Observation ID to this projection and
do not use `json_extract`, concatenation, aliases around expressions, or JSON
repackaging. Query the response body separately:

```sql
SELECT response_body
FROM observations
WHERE observation_id = :observation_id
LIMIT 1
```

The body is an exact stored BLOB, returned as at most 4 KiB of Base64 with its
full byte length and truncation flag. Use `body_format` and `media_type` from the
metadata query to interpret it.

## Interpretation rules

- `outcome_kind = 'http'` carries status, headers, and body;
  `outcome_kind = 'transport'` carries `transport_code` and message instead.
- A Primary Observation may have one Replay through
  `replay_of_observation_id`; use the Oracle Reference for its final verdict.
- Truncated Tool output is not proof that the stored evidence is truncated.
  Refine the query or use an existing domain Tool when a larger safe projection
  is authorized.
