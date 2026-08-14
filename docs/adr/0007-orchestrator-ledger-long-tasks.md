---
status: accepted
---

# Keep long-task memory in an Orchestrator-owned Ledger

This decision supersedes the App-lifetime taskless Main lifecycle in
[ADR 0001](0001-main-agent-skills-tools-harness.md),
[ADR 0002](0002-main-agent-owns-testing-decisions.md), and the launch split in
[ADR 0005](0005-system-agent-profile-monitor-decisions.md). Their generic Agent,
Profile authorization, Skill, Tool, Subagent, Prompt Session, and deterministic
Harness contracts remain accepted.

## Decision

`restscope.orchestration` is the sole owner of RESTScope's long-task loop. It
holds one immutable Goal Contract and an App-lifetime, in-memory Task Ledger.
The Ledger records revisioned Milestones, bounded Tasks, and append-only
Attempts. It is not persisted and cannot resume an App after process restart.

The outer Orchestrator and every Task Executor run through registered
`HarnessRuntime.run_system_agent()` calls. Each call receives bounded Markdown,
owns a fresh Prompt Session and Agent tree, returns one validated structured
result, and closes. The Orchestrator has no Tools, Skills, Context Sources, or
children. The Task Executor owns execution inside one Task, may use API-testing
Tools and Skills, and may delegate only Parameter Patch work to its child.

The Orchestrator may revise future work but cannot modify the fixed Goal or
rewrite prior Task and Attempt history. Its first decision must create a plan;
later decisions are exactly one of replan, dispatch one Task, or complete.
Task Execution Results report every Task criterion exactly once and never choose the
next Task. Lifecycle failures become Attempts before the Orchestrator decides
what follows. An unchanged failed Task requires both its failed Attempt and a
new retry reason.

Model context contains the fixed Goal, current rolling plan, immutable reasons
for accepted Plan Revisions, and a bounded causal projection joining each
recent Attempt to its Task and Milestone. Complete history stays in the Ledger,
while whole older records are omitted from prompts, so model input does not
grow linearly with hundreds of rounds. There is
no outer round or token ceiling; semantic completion belongs to the
Orchestrator, while cancellation and resource shutdown remain deterministic.

## Consequences

- `RESTScopeApp.start(focus=None)` appends an optional focus to the fixed Goal
  and blocks on `OrchestrationRuntime.run()`.
- The production taskless `Agent.start()` and `start_main_agent()` paths are
  removed. Generic Agent execution and Subagents remain.
- `plan.read` and `plan.update` are private intra-Task-Executor memory only. They
  are not the outer Ledger and never cross Task Executor sessions.
- Orchestrator output trusts validated Task Execution Results and Ledger state; v1 adds
  no independent verifier or behavior-database query.
- The Ledger is operational memory, not durable evidence. Existing Batches,
  Observations, Contracts, Resources, and Oracle Assessments remain the audit
  records owned by the API Behavior Monitor.
