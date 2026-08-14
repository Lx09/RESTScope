---
name: query-restscope-database
description: Choose and execute bounded read-only SQL against RESTScope's current evidence database. Use when the Orchestrator or Task Executor needs durable facts about schema, test progress, Batches and Observations, replay-confirmed Bugs, Resources and semantic states, input sources and Generator snapshots, or OpenAPI contract changes.
---

# Query RESTScope Database

Treat the database as durable evidence, not as a plan, scheduler, or permission
to act on the target API.

1. Classify the question into one category below. Call `file.read` for exactly
   that Reference before writing SQL. Load another Reference only when the
   question genuinely crosses categories.
2. Prefer an already supplied bounded Context or domain Tool when it directly
   answers the question. In particular, use `test-progress` as the
   Orchestrator's default coverage summary and use SQL only for needed detail.
3. Call `database.query` with explicit columns, named parameters, deterministic
   `ORDER BY`, and a narrow `LIMIT`. Query schema metadata first when a column or
   relationship is uncertain.
4. Read `observations.response_headers` only as the single complete selected
   column. Query its Observation ID and other metadata separately. The Tool
   rejects derived or mixed header projections so sensitive names remain
   available for deterministic redaction.
5. Distinguish stored facts from inference. A row proves only what its owning
   table records; a Batch summary is not an HTTP Observation, and an
   Observation is not a replay-confirmed Bug without its Oracle Assessment.
6. Refine or paginate when the Tool reports truncation. Never attempt a write,
   schema change, PRAGMA, attachment, transaction, or extension load.

## Reference routing

- Discover tables, columns, and declared relationships: [schema discovery](references/schema-discovery.md)
- Assess operation coverage and positive/negative attempts: [progress and coverage](references/progress-and-coverage.md)
- Inspect Batch membership, requests, responses, or transport failures: [Batches and Observations](references/batches-and-observations.md)
- Find replay-confirmed Bugs and Oracle reasons: [Bugs and Oracles](references/bugs-and-oracles.md)
- Inspect Resource definitions, instances, roles, and state transitions: [Resources and states](references/resources-and-states.md)
- Trace producer inputs or immutable Generator/Constraint snapshots: [inputs and generation](references/inputs-and-generation.md)
- Inspect normalized operations or response-contract evolution: [OpenAPI and contract changes](references/openapi-and-contract-changes.md)
