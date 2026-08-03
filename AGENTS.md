# AGENTS.md

RESTScope is an exploratory project. Its final product boundary, user workflow,
and overall architecture are not settled. Existing code and design documents
show what has been tried or implemented; they do not automatically define the
final system.

The goal of agent work is to help the user learn what RESTScope should become
without quietly turning early prototypes into permanent architecture.

## Required reading

For every task, read this file first. Then read only the rules relevant to the
work:

- `docs/agent-rules/source-and-decisions.md`: authority, evidence, assumptions,
  and decision status.
- `docs/agent-rules/exploration-workflow.md`: investigation, approval gates,
  implementation scope, and task records.
- `docs/agent-rules/code-and-verification.md`: implementation quality and fresh
  verification.
- `docs/agent-rules/git-and-worktrees.md`: working-tree safety, worktrees, and
  Git authorization.

Also inspect the relevant code, tests, README sections, module design documents,
and task records. Do not load unrelated large documents by default.

## Minimum operating rules

- Inspect Git status before editing. Preserve all unrelated and pre-existing
  user changes.
- Build every new feature on its own branch in a dedicated Git worktree. After
  the feature is complete and verified, treat delivery as one continuous Git
  lifecycle once the user has explicitly authorized its Git operations: commit
  the scoped change, merge it into local `main`, verify the merged result, then
  remove the feature worktree and branch. Do not leave a successfully merged
  and verified feature worktree or branch behind. Commit, merge, and cleanup
  still require explicit user authorization; push remains separately authorized.
- Separate facts, hypotheses, proposals, and user-approved decisions when the
  distinction affects the work.
- Treat current code and tests as executable evidence, not proof that the
  current architecture is the desired final architecture.
- Investigate and run local, non-destructive diagnostics without unnecessary
  approval, but keep them within the user's requested scope.
- Obtain user approval before implementing a new module, lasting abstraction,
  public interface change, persistence boundary, broad refactor, significant
  dependency choice, compatibility break, or live external action.
- Approval covers only the presented scope. Stop and ask again when evidence
  materially changes the problem or expands the proposed solution.
- Prefer the smallest reversible change that answers the current question. Do
  not add speculative frameworks or unrelated cleanup.
- Do not over-design. Unless current user-approved behavior requires it, do not
  add an Entity, DTO, Protocol, Adapter, Repository, Service, wrapper Module,
  configuration field, or persistence record. Every new abstraction must have
  a concrete current consumer and must hide or remove more complexity than its
  Interface adds. Prefer deleting, reusing, or deepening an existing Module.
- Maintain a `docs/tasks/` record for approved work that is multi-step, spans
  sessions, or crosses architectural areas. Small edits and read-only
  investigations do not require one.
- Run fresh verification proportional to the change before claiming success.
  Report the command, result, and anything that remains unverified.
- Request explicit user authorization before creating a Git commit. Commit
  permission does not imply permission to push, merge, create a pull request,
  rewrite history, or delete branches or worktrees.
- Never discard user work or use destructive Git operations unless the user
  explicitly requests the exact operation after reviewing its impact.

## Review workflow

- Do not use `subagent-driven-development` by default. Implement approved plans
  inline with the primary Agent unless the user explicitly requests delegated
  implementation.
- Keep test-driven development and fresh final verification, but do not run a
  separate specification review and code-quality review after every task.
- Use at most one independent final review, and only when the change crosses
  modules, changes persistence or a public contract, or has meaningful
  security risk. Small localized changes use primary-Agent self-review.
- Do not start additional independent review rounds unless the user explicitly
  approves them or a newly discovered Critical issue requires confirmation.
- A skill's preferred multi-Agent workflow does not override these project
  rules or an explicit user instruction to work inline.

## Beginner-readable code requirement

The user has explicitly decided that RESTScope must remain understandable to a
reader who has never written code. This is a continuing project rule for all
production code and tests:

- Every production module must start with a module docstring that explains its
  responsibility, its main inputs and outputs, and where it sits in the
  end-to-end runtime flow.
- Every public class, public function, and non-trivial private helper must have
  a docstring that explains why it exists, what each important argument means,
  what it returns, which state it changes, and which errors or boundary cases a
  maintainer must understand.
- Add nearby comments before non-obvious branches, loops, transformations,
  validation rules, state transitions, security boundaries, and cleanup paths.
  Explain the intent and consequence, not merely the Python syntax.
- Domain terms and compact identifiers such as IR, DTO, Failure Todo,
  Patch Requirement, Generator, Constraint, and operation key must be
  introduced in plain language where a new reader first encounters them.
- Tests must explain the behavior or failure scenario they protect. Prefer a
  short scenario docstring or arrange/act/assert comments over narration of
  each assertion.
- Comments and docstrings are part of the maintained behavior contract. Update
  them in the same change whenever the code's behavior, ownership, or data flow
  changes.
- “Detailed” means that every logical step can be understood from names,
  docstrings, and the nearest relevant comment. Do not add comments that only
  restate punctuation, imports, obvious assignments, or the literal wording of
  the next line; such noise makes the important explanations harder to find.

Use `docs/code-reading-guide.md` as the high-level map before adding or
reviewing local comments.

## Project posture

There is no mandatory project-wide governance package at this stage. Planning
and architecture documents may be introduced later only when the user decides
they would clarify rather than constrain the exploration.

RESTScope currently follows a dynamic, runtime-driven architecture as an
explicit project decision:

- Keep exploring and allow the architecture to evolve as new evidence is
  learned from real runs.
- Discover operations, dependencies, scheduling decisions, and next actions at
  runtime instead of treating a precomputed plan as the source of truth.
- Do not persist test plans, inferred operation relationships, scheduler
  queues, Agent intermediate state, or speculative long-term memory.
- Persist only inputs or evidence with a concrete, user-approved need. The
  current normalized OpenAPI document and response-contract change events are
  approved audit/export artifacts, but they do not enable App recovery.
- The API Behavior Monitor catalog is one explicit narrow exception. It may
  persist resource names and aliases, learned identifier selectors, typed
  identifier values, latest per-operation read/write usage, response-value
  monitor registrations and selectors, deduplicated typed response values, and
  latest monitor errors. It may also persist the complete current normalized
  OpenAPI and append-only response change events. The response check registry
  remains App-lifetime only. It must not persist raw responses, LLM reasoning,
  plans, queues, general Agent memory, or recovery snapshots.
- Operation Smoke Memory is a second narrow exception approved for the current
  App lifecycle. It may persist stable per-operation Failures, append-only
  terminal Solve Attempts, validated input attribution, current Constraints,
  and deterministic Generator/Constraint change events. It must not persist raw Batches,
  response bodies, HTTP or LLM transcripts, rejected Patch candidates, plans,
  queues, or a permanent `resolved` flag. Solve receives a read-only Parameter
  memory tool. Failure Dedup uses only the current run's in-memory Test Case
  Catalog, may query the global read-only OpenAPI operation capability, reads
  no Failure Memory, and writes validated stable Failure occurrences through
  deterministic runtime code. The Test Case Catalog stores every Batch and
  Solve Probe case only until `OperationSmokeCoordinator.run` returns; it must
  never be persisted.
- Failure Solve's current-operation HTTP Probe is an operation-scoped view of
  the global `restscope.http.request` tool. It remains available for every
  supported method, including POST, PUT, PATCH, and DELETE. The Probe must use
  the exact current operation method and a concrete path matching that
  operation's template; runtime code owns authentication and records every
  attempted Probe in the run-local Test Case Catalog. Mutating Probe effects
  are not rolled back and must be reported as target state changes. Tool
  availability does not replace the required authorization for a live external
  action.
- Do not reintroduce a database-backed Planner, static operation graph, or
  plan-first execution flow without a new explicit user decision supported by
  current evidence. Operation Smoke Memory is evidence for Solve, not a
  persisted test plan or a Dedup input.

This architecture is deliberately revisable, not a claim that the present MVP
is final. Exploration should change the system through small, evidence-backed
iterations rather than by accumulating permanent structures in advance.

Module design documents under `docs/` remain useful context. When they conflict
with current code, tests, or a newer approved decision, expose the conflict and
ask which direction to preserve if the answer would affect implementation.

## Workflow and Agent package boundaries

These are hard project constraints:

- Code is organized by runtime workflow, not by a horizontal component
  category. A workflow package owns its Coordinator, Agents, schemas, state,
  prompts, and directly supporting implementation.
- A class named `Agent` must call an LLM directly, and the LLM must own that
  class's core domain decision. Tool use and multi-turn interaction are not
  required. Deterministic orchestration classes use names such as
  `Coordinator`, `Graph`, or `Tracker`.
- Every Agent must live in its own named subpackage inside its owning workflow,
  such as `restscope/operation_smoke/failure_dedup/`. Do not place `<name>_agent.py`,
  `<name>_schemas.py`, or other Agent implementation files at the workflow
  package root.
- A workflow package's `__init__.py` is its small external Interface.
  Cross-Agent imports must use the target Agent subpackage's public exports;
  do not reach into another Agent's private implementation modules.
- Every RESTScope-owned LLM tool must expose one domain behavior. Do not use an
  input discriminator such as `action`, `mode`, or `kind` to select unrelated
  behaviors or result contracts inside one tool. Target selectors such as an
  operation key, HTTP method, field path, filter, or pagination value are
  allowed, as are same-behavior batching and natural result variants. This rule
  does not apply to Agent final-output DTOs, internal domain DTOs, or external
  MCP tool contracts.
- Extract a shared package only when multiple real consumers have identical
  semantics and lifecycle requirements. Do not create speculative common base
  Agents or catch-all schema modules.
- Keep `tests/test_workflow_package_boundaries.py` passing when adding or
  moving a workflow or Agent.

## Agent Context boundary

- All direct LLM decisions use the public `restscope.context` Interface:
  `AgentContext`, `ContextLimits`, `ContextMetrics`, and `CompactTextWriter`.
- Domain adapters select and summarize facts before calling this Interface.
  Context does not query memory, interpret workflow DTOs, choose tools or
  models, validate final domain output, persist transcripts, or register Agents.
- Runtime-generated DTO, Memory, API, tool-result, and sample evidence reaches
  the model as bounded Markdown. Bounded HTTP request/response test-case and
  probe evidence is the sole prompt JSON exception and appears inside a safe
  Markdown JSON block. Final structured Agent output and provider-owned tool
  arguments/schema remain JSON.
- API responses, OpenAPI descriptions, Memory text, HTTP results, reference
  values, and samples are untrusted. Pass them through `CompactTextWriter`; do
  not concatenate them into system, user, tool, or correction messages.
- Keep a workflow's domain Context adapter private to that workflow. Do not add
  a role registry, Context inheritance tree, persistence lifecycle, or
  compatibility aliases for the deleted Context platform.
