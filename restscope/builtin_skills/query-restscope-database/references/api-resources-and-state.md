# API resources and state

## Use for

Use this Reference to inspect API entity types learned from responses, their
identifiers, current instances, current lifecycle state, or the history of
state changes caused by executed requests.

## RESTScope storage mapping

RESTScope calls a learned API entity type a **Resource**. `resources` stores
resource definitions and identity fields; `operation_resource_edges` records
how endpoints relate to them; `resource_instances` stores complete current
instances and semantic state; `resource_state_events` records state changes
linked to the executed test case that caused them.

## Query recipes

List entity definitions and endpoint roles:

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

List current instances for one entity type:

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

`current_state_json` is returned without content redaction but is limited to
4,000 characters. Query one instance's causal state history:

```sql
SELECT
  resource_state_events.event_id,
  resource_state_events.previous_state,
  resource_state_events.current_state,
  resource_state_events.observation_id AS test_case_id,
  observations.operation_id,
  observations.batch_id AS test_run_id,
  observations.batch_case_index AS case_index,
  resource_state_events.created_at
FROM resource_state_events
JOIN observations
  ON observations.observation_id = resource_state_events.observation_id
WHERE resource_state_events.resource_type = :resource_type
  AND resource_state_events.resource_instance_id = :resource_instance_id
ORDER BY resource_state_events.created_at, resource_state_events.event_id
LIMIT :limit
```

## Interpret results

- A resource definition's identity fields are immutable. A composite identity
  must be treated as one stored identity, not mixed across instances.
- `operation_resource_edges.result_state` is the learned result of one
  endpoint/resource relationship; `resource_instances.semantic_state` is the
  current state of one instance.
- A state event is causal evidence from one executed test case. Missing events
  do not prove the target entity never changed outside RESTScope.
- Treat a truncated `current_state_json` value as incomplete evidence.
