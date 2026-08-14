# Progress and coverage

Use this Reference to explain which operations have positive or negative Batch
attempts and how many cases actually executed. When the Orchestrator already
has `test-progress`, treat that projection as the default summary and query only
the detail needed for a Replan or completion decision.

## Operation-level Batch progress

```sql
WITH batch_progress AS (
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
  COALESCE(batch_progress.positive_attempts, 0) AS positive_attempts,
  COALESCE(batch_progress.negative_attempts, 0) AS negative_attempts,
  COALESCE(batch_progress.positive_cases, 0) AS positive_cases,
  COALESCE(batch_progress.negative_cases, 0) AS negative_cases
FROM operations
LEFT JOIN batch_progress ON batch_progress.operation_key = operations.operation_id
ORDER BY positive_cases, negative_cases, operations.operation_id
LIMIT :limit
```

Use `{"limit": 100}` unless the operation catalog is known to be smaller.

## Drill into one operation's Batches

```sql
SELECT
  batch_id,
  json_extract(summary, '$.status') AS status,
  json_extract(summary, '$.test_mode') AS test_mode,
  json_extract(summary, '$.requested_case_count') AS requested_cases,
  json_extract(summary, '$.executed_case_count') AS executed_cases,
  json_extract(summary, '$.skipped_case_count') AS skipped_cases,
  json_extract(summary, '$.bug_count') AS reported_bug_count
FROM batches
WHERE json_extract(summary, '$.schema_version') = 1
  AND json_extract(summary, '$.operation_key') = :operation_key
ORDER BY batch_id
LIMIT :limit
```

## Interpretation rules

- Count Batch attempts separately from executed cases. A valid zero-case Batch
  is still an attempt; a skipped slot is not executed evidence.
- A `happy_path` case count does not by itself prove a 2xx result. Inspect its
  Observations when the exact outcome matters.
- A Batch `bug_count` is a bounded summary. Use the Oracle Reference and
  `oracle_assessments.is_bug` for the canonical replay-confirmed verdict.
- Do not materialize these counts as a second progress store or scheduler.
