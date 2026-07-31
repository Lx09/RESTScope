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
A single representative failed test case retained for one current-round
Failure.
_Avoid_: Failure, issue

**Failure**:
A current-round category whose observations have the same complete suspected
causal Parameter set. Attribution remains provisional until Investigation.
_Avoid_: Todo, error message

**Failure Fingerprint**:
The normalized error-message text used to remove exact duplicates before
semantic classification.
_Avoid_: Failure ID, case ID

**Representative Test Case**:
The earliest current-Batch case retained for a Failure and passed to Solve.
_Avoid_: Batch, case group

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

- One Failure has exactly one representative Observation.
- Every Batch round creates new Failure identities; Dedup does not reuse history.
- One Investigation belongs to one Failure and may attribute several
  Parameters as causes.
- One Parameter may appear in many Failures and Investigations.
- An Applied Patch belongs to one Investigation and stores its accepted
  Generator revision, before/after summaries, Constraints, and validation
  samples.

Models never receive database primary keys or Fingerprint references. Solve
sees semantic input handles such as
`body.project.startDate`; Patch candidates use Solve-session `P*` references.
Runtime code maps and validates each reference.

There is no permanent “resolved” state. A later complete Batch may show that a
previous Patch helped, did nothing, or interacted with another change; Memory
keeps the evidence and chronological attempts instead of rewriting history.
