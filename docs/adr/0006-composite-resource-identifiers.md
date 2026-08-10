---
status: accepted
---

# Preserve resource identifiers as ordered definitions and records

## Decision

Resource Identifier discovery examines only direct scalar fields of a response
root object or each object item in a root array. It never enters a nested object,
nested array, or wrapper array. OpenAPI Schema may enrich an observed field but
cannot introduce an absent candidate.

Every first identifier decision uses the Resource Identifier System Agent with
one complete bounded evidence set. That evidence includes full OpenAPI paths:
the current placeholder path and strict descendant paths whose added segments
are all placeholders. Harness validation binds selected response fields, in
order, to every placeholder in the selected full path. Learned rules remain a
deterministic reuse boundary.

The durable identity is split into an Identifier Definition and Identifier
Records. A Definition names one or more ordered components. A Record stores one
complete typed tuple observed in the same response element. Request generation
freezes and samples those records jointly, so composite components cannot be
mixed across observations.

## Consequences

- Nested response fields cannot accidentally become reusable identifiers.
- `/assignments/{employeeId}/{projectId}` may establish the ordered definition
  `employeeId/projectId`, while a no-path decision remains single-field only.
- Missing or invalid components discard a whole record rather than persisting a
  misleading partial identity.
- `resource.list_ids`, Parameter Patch references, Generation State, and Batch
  snapshots expose definition and component identity explicitly.
- The current baseline adds a definition table and intentionally provides no
  migration compatibility for older exploratory database files.
- Complete evidence is preferred over partial inference: an oversized prompt
  produces a Monitor warning and no classification.
