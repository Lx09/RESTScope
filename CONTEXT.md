# RESTScope

RESTScope explores an API through generated requests, classifies observed
failures, and evolves input generators from evidence gathered during one App
lifetime.

## Agent Context

Agent Context is the short-lived, model-facing message view for one LLM
decision. It is not Operation Smoke Memory and is never stored. A workflow
first selects typed domain facts; `CompactTextWriter` encodes them as safe,
compact text, and `AgentContext` keeps the initial task plus bounded tool and
validation exchanges. Final Agent decisions still use strict JSON.

## Operation Smoke Language

**Failure Observation**:
A concrete failed case or error observed in one Batch Testing run.
_Avoid_: Failure, issue

**Failure**:
A stable semantic category maintained by Planner for one operation and linked
to one or more Failure Observations across rounds.
_Avoid_: Todo, error message

**Investigation**:
One Solve session that records its trigger conditions, parameter attribution,
root-cause conclusion, proposed resolution, and terminal outcome.
_Avoid_: Solve output, reasoning

**Parameter**:
One operation input identified by the combination of operation key and
canonical input node identity.
_Avoid_: Field name

**Generator Requirement**:
A structured description of the values or relationships that a candidate
Generator or Constraint must produce.
_Avoid_: Patch

**Applied Patch**:
A Patch accepted by Solve and atomically written as a new Generator revision
with its Investigation memory.
_Avoid_: Candidate, resolved Patch

## Relationships and identity

- One Failure may collect Observations from many Batch rounds.
- One Observation may support more than one Failure.
- One Investigation belongs to one Failure and may attribute several
  Parameters as causes.
- One Parameter may appear in many Failures and Investigations.
- An Applied Patch belongs to one Investigation and stores its accepted
  Generator revision, before/after summaries, Constraints, and validation
  samples.

Models never receive database primary keys. Planner sees request-local `F*`
Failure references; Solve sees semantic input handles such as
`body.project.startDate`; Patch candidates use Solve-session `P*` references.
Runtime code maps and validates each reference.

There is no permanent “resolved” state. A later complete Batch may show that a
previous Patch helped, did nothing, or interacted with another change; Memory
keeps the evidence and chronological attempts instead of rewriting history.
