# Confirmed API defects

## Use for

Use this Reference to find failed test cases that were replayed and confirmed
as reproducible API defects, or to explain why a suspicious response was not
confirmed.

## RESTScope storage mapping

RESTScope stores one final deterministic defect-verification verdict in
`oracle_assessments`. This internal record is called a **Bug Oracle
Assessment**. It links an original executed test case to at most one
exact-request replay stored in `observations`. Only `is_bug = 1` is a confirmed
defect; an unexpected status or a test-run summary alone is not enough.

## Query recipes

List confirmed defects:

```sql
SELECT
  oracle_assessments.primary_observation_id AS original_test_case_id,
  oracle_assessments.replay_observation_id AS replay_test_case_id,
  original_result.operation_id,
  original_result.status_code AS original_status,
  replay_result.status_code AS replay_status,
  json_extract(oracle_assessments.assessment_json, '$.checks[0].status') AS verification_status,
  json_extract(oracle_assessments.assessment_json, '$.checks[0].primary_reasons') AS original_reasons,
  json_extract(oracle_assessments.assessment_json, '$.checks[0].replay_reasons') AS replay_reasons,
  oracle_assessments.completed_at
FROM oracle_assessments
JOIN observations AS original_result
  ON original_result.observation_id = oracle_assessments.primary_observation_id
LEFT JOIN observations AS replay_result
  ON replay_result.observation_id = oracle_assessments.replay_observation_id
WHERE oracle_assessments.is_bug = 1
ORDER BY oracle_assessments.completed_at DESC,
         oracle_assessments.primary_observation_id
LIMIT :limit
```

Inspect all final verdicts for one endpoint:

```sql
SELECT
  oracle_assessments.primary_observation_id AS original_test_case_id,
  oracle_assessments.replay_observation_id AS replay_test_case_id,
  oracle_assessments.is_bug AS is_confirmed_defect,
  json_extract(oracle_assessments.assessment_json, '$.schema_version') AS schema_version,
  json_extract(oracle_assessments.assessment_json, '$.checks[0].status') AS verification_status,
  oracle_assessments.completed_at
FROM oracle_assessments
JOIN observations
  ON observations.observation_id = oracle_assessments.primary_observation_id
WHERE observations.operation_id = :operation_key
ORDER BY oracle_assessments.completed_at DESC
LIMIT :limit
```

## Interpret results

- `no_candidate`, `not_reproduced`, and `replay_failed` are final verdicts but
  are not confirmed defects.
- `reproduced` with `is_bug = 1` means the replay reproduced the original
  deterministic reason set.
- Current reasons include `server_error` and
  `invalid_input_unexpected_status`. Quote the stored reason instead of
  inventing a broader defect taxonomy.
- An HTTP status or `batches.summary` defect count never replaces the final
  assessment row.
