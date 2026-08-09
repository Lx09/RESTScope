---
status: accepted
---

# Let the Main Agent own API-testing decisions

RESTScope's product entry uses its single long-lived Main Agent to decide which
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
