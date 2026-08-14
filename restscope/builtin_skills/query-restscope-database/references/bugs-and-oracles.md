# Bugs and Oracles

Use this Reference to find deterministic unexpected-status assessments and
their optional exact-request Replays. Only `oracle_assessments.is_bug = 1`
represents a replay-confirmed Bug.

## List confirmed Bugs

```sql
SELECT
  oracle_assessments.primary_observation_id,
  oracle_assessments.replay_observation_id,
  primary_observation.operation_id,
  primary_observation.status_code AS primary_status,
  replay_observation.status_code AS replay_status,
  json_extract(oracle_assessments.assessment_json, '$.checks[0].status') AS check_status,
  json_extract(oracle_assessments.assessment_json, '$.checks[0].primary_reasons') AS primary_reasons,
  json_extract(oracle_assessments.assessment_json, '$.checks[0].replay_reasons') AS replay_reasons,
  oracle_assessments.completed_at
FROM oracle_assessments
JOIN observations AS primary_observation
  ON primary_observation.observation_id = oracle_assessments.primary_observation_id
LEFT JOIN observations AS replay_observation
  ON replay_observation.observation_id = oracle_assessments.replay_observation_id
WHERE oracle_assessments.is_bug = 1
ORDER BY oracle_assessments.completed_at DESC,
         oracle_assessments.primary_observation_id
LIMIT :limit
```

## Inspect all final assessments for one operation

```sql
SELECT
  oracle_assessments.primary_observation_id,
  oracle_assessments.replay_observation_id,
  oracle_assessments.is_bug,
  json_extract(oracle_assessments.assessment_json, '$.schema_version') AS schema_version,
  json_extract(oracle_assessments.assessment_json, '$.checks[0].status') AS check_status,
  oracle_assessments.completed_at
FROM oracle_assessments
JOIN observations
  ON observations.observation_id = oracle_assessments.primary_observation_id
WHERE observations.operation_id = :operation_key
ORDER BY oracle_assessments.completed_at DESC
LIMIT :limit
```

## Interpretation rules

- `no_candidate`, `not_reproduced`, and `replay_failed` are final assessments
  but not confirmed Bugs.
- `reproduced` with `is_bug = 1` means the deterministic Primary reason set was
  reproduced by the exact-request Replay.
- Current reasons are `server_error` and
  `invalid_input_unexpected_status`; quote stored values rather than inventing a
  broader bug taxonomy.
- An Observation status or Batch `bug_count` alone does not replace the final
  Oracle row.
