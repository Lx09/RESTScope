# Resources and states

Use this Reference to inspect learned Resource definitions, operation roles,
complete current instances, semantic states, and causal state transitions.

## Definitions and operation roles

```sql
SELECT
  resources.name AS resource_name,
  resources.identity_fields,
  operation_resource_edges.operation_id,
  operation_resource_edges.role,
  operation_resource_edges.result_state
FROM resources
LEFT JOIN operation_resource_edges
  ON operation_resource_edges.resource_id = resources.resource_id
ORDER BY resources.name, operation_resource_edges.operation_id
LIMIT :limit
```

## Current instances

Read small instance metadata first:

```sql
SELECT
  resource_type,
  resource_instance_id,
  semantic_state
FROM resource_instances
WHERE resource_type = :resource_type
ORDER BY semantic_state, resource_instance_id
LIMIT :limit
```

Read one complete current instance only when its content is needed:

```sql
SELECT current_state_json
FROM resource_instances
WHERE resource_type = :resource_type
  AND resource_instance_id = :resource_instance_id
LIMIT 1
```

`current_state_json` is allowed without content redaction but is limited to a
4,000-character Tool cell. Treat any truncation as incomplete evidence.

## Causal state history

```sql
SELECT
  resource_state_events.event_id,
  resource_state_events.previous_state,
  resource_state_events.current_state,
  resource_state_events.observation_id,
  observations.operation_id,
  observations.batch_id,
  observations.batch_case_index,
  resource_state_events.created_at
FROM resource_state_events
JOIN observations
  ON observations.observation_id = resource_state_events.observation_id
WHERE resource_state_events.resource_type = :resource_type
  AND resource_state_events.resource_instance_id = :resource_instance_id
ORDER BY resource_state_events.created_at, resource_state_events.event_id
LIMIT :limit
```

## Interpretation rules

- A Resource definition's identity fields are immutable; an instance identity
  may be composite and must be treated as one stored value.
- `operation_resource_edges.result_state` is the immutable semantic result of
  that operation/Resource relationship. `resource_instances.semantic_state` is
  the current instance state.
- A state event is causal evidence linked to an Observation. Missing events do
  not prove that the target resource never changed outside RESTScope.
