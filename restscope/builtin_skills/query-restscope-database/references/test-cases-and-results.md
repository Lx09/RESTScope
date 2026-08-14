# Executed test cases and results

## Use for

Use this Reference to inspect a test case's request, HTTP response, transport
failure, response headers, or response body, or to list the cases in one grouped
test run.

## RESTScope storage mapping

RESTScope stores every actually executed request and result as one row in
`observations`. Despite the table name, each `observation_id` is the durable ID
of an executed test case. Rows with a `batch_id` belong to a grouped test run in
`batches`, and `batch_case_index` preserves their original order. Rows without a
Batch may represent a direct request or an exact-request replay.

## Query recipes

List one grouped test run's cases:

```sql
SELECT
  batch_case_index AS case_index,
  observation_id AS test_case_id,
  operation_id,
  outcome_kind,
  status_code,
  media_type,
  transport_code,
  timestamp
FROM observations
WHERE batch_id = :test_run_id
ORDER BY batch_case_index
LIMIT :limit
```

Inspect one test case's metadata and request:

```sql
SELECT
  observation_id AS test_case_id,
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
  batch_id AS test_run_id,
  batch_case_index AS case_index,
  replay_of_observation_id AS replay_of_test_case_id
FROM observations
WHERE observation_id = :test_case_id
LIMIT 1
```

Read response content in two additional calls. Complete headers must be the
only selected column:

```sql
SELECT response_headers
FROM observations
WHERE observation_id = :test_case_id
LIMIT 1
```

Then query the body separately:

```sql
SELECT response_body
FROM observations
WHERE observation_id = :test_case_id
LIMIT 1
```

Do not add the test-case ID or other metadata to the header projection. Do not
use `json_extract`, concatenation, expression aliases, or JSON repackaging on
`response_headers`. The Tool verifies a complete stored mapping and redacts
sensitive header values. A response body is returned as at most 4 KiB of Base64
with its full byte length and truncation flag.

## Interpret results

- `outcome_kind = 'http'` carries an HTTP status, headers, and body.
  `outcome_kind = 'transport'` carries a transport failure code and message.
- Use `body_format` and `media_type` from the metadata query to interpret the
  response body.
- `replay_of_observation_id` links an exact-request replay to its original test
  case; load the confirmed-defect Reference for the final verdict.
- Tool truncation does not prove stored evidence was truncated. Refine the
  query when the missing portion matters.
