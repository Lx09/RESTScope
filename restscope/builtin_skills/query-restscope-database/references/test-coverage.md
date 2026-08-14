# API test coverage

## Use for

Use this Reference to answer which API endpoints have positive or negative
test attempts, how many test cases actually ran, and where coverage is missing.

## RESTScope storage mapping

RESTScope calls one grouped test run a **Batch** and stores its identity and
bounded summary in `batches`. The summary records the API operation, test mode,
requested case count, executed case count, and skipped case count. API endpoints
are stored in `operations`. A Batch is a test-run summary, not an individual
HTTP result.

## Query recipes

Summarize coverage for every endpoint:

```sql
WITH run_progress AS (
  SELECT
    json_extract(summary, '$.operation_key') AS operation_key,
    SUM(CASE WHEN json_extract(summary, '$.test_mode') = 'happy_path' THEN 1 ELSE 0 END) AS positive_attempts,
    SUM(CASE WHEN json_extract(summary, '$.test_mode') = 'exceptional' THEN 1 ELSE 0 END) AS negative_attempts,
    SUM(CASE WHEN json_extract(summary, '$.test_mode') = 'happy_path'
             THEN CAST(json_extract(summary, '$.executed_case_count') AS INTEGER) ELSE 0 END) AS positive_cases,
    SUM(CASE WHEN json_extract(summary, '$.test_mode') = 'exceptional'
             THEN CAST(json_extract(summary, '$.executed_case_count') AS INTEGER) ELSE 0 END) AS negative_cases
  FROM batches
  WHERE json_extract(summary, '$.schema_version') = 1
  GROUP BY json_extract(summary, '$.operation_key')
)
SELECT
  operations.operation_id,
  operations.method,
  operations.path,
  COALESCE(run_progress.positive_attempts, 0) AS positive_attempts,
  COALESCE(run_progress.negative_attempts, 0) AS negative_attempts,
  COALESCE(run_progress.positive_cases, 0) AS positive_cases,
  COALESCE(run_progress.negative_cases, 0) AS negative_cases
FROM operations
LEFT JOIN run_progress ON run_progress.operation_key = operations.operation_id
ORDER BY positive_cases, negative_cases, operations.operation_id
LIMIT :limit
```

Use `{"limit": 100}` unless the endpoint catalog is known to be smaller. Drill
into the runs for one endpoint:

```sql
SELECT
  batch_id AS test_run_id,
  json_extract(summary, '$.status') AS status,
  json_extract(summary, '$.test_mode') AS test_mode,
  json_extract(summary, '$.requested_case_count') AS requested_cases,
  json_extract(summary, '$.executed_case_count') AS executed_cases,
  json_extract(summary, '$.skipped_case_count') AS skipped_cases,
  json_extract(summary, '$.bug_count') AS reported_defect_count
FROM batches
WHERE json_extract(summary, '$.schema_version') = 1
  AND json_extract(summary, '$.operation_key') = :operation_key
ORDER BY batch_id
LIMIT :limit
```

## Interpret results

- Count test-run attempts separately from executed test cases. A valid
  zero-case run is still an attempt; a skipped slot is not executed evidence.
- `happy_path` and `exceptional` are RESTScope's positive and negative test
  modes. Neither mode proves a particular HTTP status.
- A summary defect count is not the canonical confirmed-defect verdict. Load
  the confirmed-defect Reference when that distinction matters.
- Do not copy these counts into another progress store or scheduler.
