---
status: accepted
---

# Let the Main Agent own API-testing decisions

The ownership decision is superseded by
[ADR 0007](0007-orchestrator-ledger-long-tasks.md): cross-Task scheduling now
belongs to the outer Orchestrator, while each fresh Main Worker owns semantic
execution inside one bounded Task. Its final paragraph's run-local Test
Case/Probe description is superseded by
[ADR 0003](0003-retire-operation-smoke-and-apply-parameter-patches.md), which
uses inline Batch evidence and an App-lifetime generation Store.

Historically, RESTScope's product entry used its single long-lived Main Agent to decide which
Skills, Tools, or Subagents to use, including ordering, retries, and completion.
The Main Profile supplies the App-lifetime mission; startup does not create a
public task DTO. The Harness retains
only mechanical runtime responsibilities such as authorization, validation,
model and Tool execution, context and budget control, tracing, cancellation,
and child lifecycle; it must not choose or schedule testing work on the
model's behalf.

This decision supersedes the operation FIFO and retry assignment in
[ADR 0001](0001-main-agent-skills-tools-harness.md). The blocking Main loop now
replaces `RunHarness`; the initial Profile deliberately exposes no unfinished
testing Skills, domain Tools, Context Sources, or child Profiles.

Request Generation is a domain Module rather than a Harness namespace. It owns
Generator strategies, the recursive Constraint language, compilation and
solving, schema snapshots, request serialization, and current generation
configuration. The Harness owns deterministic operation execution, run-local
Test Cases, Probe evidence, and the mechanical runtime bindings that expose
those capabilities to an authorized Agent. Neither Module chooses which test
to run, whether evidence is sufficient, or when a semantic retry is useful;
those decisions remain with the Main LLM and its loaded Skills.
