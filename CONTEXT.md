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
The earliest current-Batch case retained for a Failure. Solve receives its
run-local `TC*` reference and queries exact facts only when needed.
_Avoid_: Batch, case group

**Test Case Catalog**:
An in-memory index shared by every Batch, Dedup call, and Solve Probe during
one operation Smoke run. It stores sent request inputs as structured JSON for
all cases and response bodies only for 4xx/5xx cases. It is never written to
the database.
_Avoid_: Batch report, persistent test history

**Current-operation HTTP Probe**:
A Failure Solve request through the global HTTP capability, restricted to the
exact operation method and path template under Investigation. It is available
for read and write operations; every attempt enters the Test Case Catalog, and
write effects are not rolled back.
_Avoid_: Read-only Probe, separate HTTP tool

**Investigation**:
One Solve session that can gather evidence and create reviewed Patch candidates
before ending with apply-Patch or no-Patch. Its model terminal output contains
only action, candidate reference, and reason.
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
- One Investigation belongs to one Failure. Its applied Patch or runtime
  conflict may attribute several Parameters from the selected candidate; a
  no-Patch conclusion attributes none.
- One Parameter may appear in many Failures and Investigations.
- An Applied Patch belongs to one Investigation and stores its accepted
  Generator revision, before/after summaries, Constraints, and validation
  samples.

Models never receive database primary keys or Fingerprint references. Solve
sees semantic input handles such as `body.project.startDate`; Dedup and Solve
use run-local `TC1`, `TC2`, … references; Patch candidates use Solve-session
`P*` references. Runtime code maps and validates each reference.

A semantic handle is the unique cross-workflow name: `query.sort` identifies
the direct key `sort` at `request.query.sort`. Test Case JSON keeps direct keys
inside `path`, `query`, `header`, `cookie`, and optional `body` containers.

There is no permanent “resolved” state. A later complete Batch may show that a
previous Patch helped, did nothing, or interacted with another change; Memory
keeps the evidence and chronological attempts instead of rewriting history.
