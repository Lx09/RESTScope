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

**Failure Source**:
One exact Failure message folded deterministically across its original failed
Test Cases. A Resolution session gives it a short `E*` reference.
_Avoid_: Stable Failure, worklist item

**Stable Failure**:
A persisted semantic conclusion derived only from a decided final worklist
item. Its key combines operation, real source messages, and suspected input
node identities.
_Avoid_: Error message, worklist draft

**Test Case Catalog**:
An in-memory index shared by every Batch and Resolution Probe during one
operation Smoke run. It stores sent request inputs as structured JSON for
all cases and response bodies only for 4xx/5xx cases. It is never written to
the database.
_Avoid_: Batch report, persistent test history

**Current-operation HTTP Probe**:
A Failure Resolution request through the global HTTP capability, restricted to
the exact operation method and path template. It is available
for read and write operations; every attempt enters the Test Case Catalog, and
write effects are not rolled back.
_Avoid_: Read-only Probe, separate HTTP tool

**Resolution Worklist**:
The Agent-owned, revisioned list for one failed Batch. It contains only `E*`,
`TC*`, `P*`, semantic Parameter handles, and bounded diagnostic text. The Agent
may merge, split, overlap, reorder, reopen, or leave items undecided.
_Avoid_: Plan, queue, persistent Agent memory

**Resolution Session**:
One continuous Agent conversation for all exact Failure sources in a failed
Batch. It owns semantic grouping, investigation order, worklist changes, root
causes, candidate selection, and finish timing.
_Avoid_: Per-Failure Agent, fixed todo list

**Resolution Context Checkpoint**:
An ephemeral Markdown handoff produced when the next Resolution prompt reaches
80% of its configured input capacity. The FAST Compact Agent reads the same
system contract plus the complete saved conversation and a temporary summary
instruction. Runtime then keeps the original Failure prompt plus the summary;
the worklist and reference registries remain authoritative and unchanged.
_Avoid_: Persistent Agent memory, worklist snapshot

**Parameter**:
One operation input identified by the combination of operation key and
canonical input node identity.
_Avoid_: Field name

**Generator Requirement**:
A structured description of the values or relationships that a candidate
Generator or Constraint must produce.
_Avoid_: Patch

**Applied Patch**:
A reviewed registry candidate selected by the final worklist and atomically
written with every other compatible decision from that Resolution session.
_Avoid_: Candidate, resolved Patch

## Relationships and identity

- One exact source may appear in several overlapping worklist items, but every
  original `(E*, TC*)` association must remain covered before finalization.
- Worklist items without a decision are discarded rather than persisted.
- A selected `P*` resolves to one immutable, reviewed candidate held only by
  the session registry; worklist text cannot alter its executable content.
- One Parameter may appear in many stable Failures and Resolution Attempts.
- Compatible selected candidates and all decided Failures/Attempts commit in
  one transaction; any validation or write failure rolls the whole set back.

Models never receive database primary keys. Resolution sees semantic input
handles such as `body.project.startDate`, exact sources as `E1`, `E2`, …,
run-local Test Cases as `TC1`, `TC2`, …, and Patch candidates as `P1`, `P2`, ….
Runtime code maps and validates every reference.

A semantic handle is the unique cross-workflow name: `query.sort` identifies
the direct key `sort` at `request.query.sort`. Test Case JSON keeps direct keys
inside `path`, `query`, `header`, `cookie`, and optional `body` containers.

There is no permanent “resolved” state. A later complete Batch may show that a
previous Patch helped, did nothing, or interacted with another change; Memory
keeps the evidence and chronological attempts instead of rewriting history.
