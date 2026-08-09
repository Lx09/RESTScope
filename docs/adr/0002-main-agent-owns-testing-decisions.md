---
status: accepted
---

# Let the Main Agent own API-testing decisions

RESTScope's target product entry will use its single long-lived Main Agent to
interpret each testing objective and decide which Skills, Tools, or Subagents
to use, including ordering, retries, and completion. The Harness will retain
only mechanical runtime responsibilities such as authorization, validation,
model and Tool execution, context and budget control, tracing, cancellation,
and child lifecycle; it must not choose or schedule testing work on the
model's behalf.

This decision supersedes only the operation FIFO and retry assignment in
[ADR 0001](0001-main-agent-skills-tools-harness.md). The existing `RunHarness`
remains the current implementation until the Main Profile, testing Skills and
Tools, App task contract, and deterministic fact ledger can replace it
together.
