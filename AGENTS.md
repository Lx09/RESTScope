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
- Do not use `typing.Any` in production code or tests. Express opaque values as
  `object`, and use concrete types, unions, type variables, or Protocols when
  callers rely on specific behavior. Do not evade this rule by omitting useful
  annotations or by importing `Any` under another name.
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
  persist resource names and aliases, ordered Identifier Definitions, learned
  operation field/path mappings, complete typed Identifier Records, latest
  per-operation read/write usage, response-value
  monitor registrations and selectors, deduplicated typed response values, and
  latest monitor errors. It may also persist the complete current normalized
  OpenAPI and append-only response change events. The response check registry
  remains App-lifetime only. It must not persist raw responses, LLM reasoning,
  plans, queues, general Agent memory, or recovery snapshots.
- Request Generation configuration is an App-lifetime in-memory store, not a
  persistence exception. It keeps one revisioned Generator/Constraint state per
  operation, including exact reference bindings. A validated Parameter Patch
  replaces that state and its affected response-value pools atomically within
  the running App, and a Batch freezes one complete revision plus its named
  reference pools before generating requests. It must not
  persist Patch history, samples, Failures, Agent reasoning, or rollback state.
- The Live Observer browser history is a second narrow exception approved only
  for local UI testing and recovery. The React page may persist the latest five
  complete schema-v3 snapshots in same-origin IndexedDB, including the
  already-redacted raw Provider Reasoning, Agent messages, target
  Authorization/Cookie values, Tool details, HTTP exchanges, Subagent
  relationships, and the Main Agent's generic Plan projection as Todo delivered
  to that browser. Batch execution and Parameter Patch application appear as
  ordinary Tool cards rather than special workflow events.
  It must not add a backend write API, SQLite record, cross-origin sync, or
  runtime input. Clearing browser site data removes this history; the App and
  workflows never read it, so it cannot resume or influence a test.
- Do not reintroduce a database-backed Planner, static operation graph, or
  plan-first execution flow, persistent Operation Smoke Memory, Patch candidate
  registry, or domain-specific Agent class without a new explicit user decision
  supported by current evidence.

This architecture is deliberately revisable, not a claim that the present MVP
is final. Exploration should change the system through small, evidence-backed
iterations rather than by accumulating permanent structures in advance.

Module design documents under `docs/` remain useful context. When they conflict
with current code, tests, or a newer approved decision, expose the conflict and
ask which direction to preserve if the answer would affect implementation.

## Main Agent, Subagent, System Agent, Skill, Tool, and Harness boundaries

These six terms are RESTScope's core runtime language and hard constraints:

- **Main Agent** is the App's single long-lived LLM Agent. **Subagent** is an
  independent, task-scoped use of the same configurable Agent runtime, started
  by the Harness only after the Main Agent requests it. Do not create separate
  Agent inheritance trees or new domain-specific `*Agent` classes. The approved
  product runtime makes the Main LLM responsible for choosing testing methods,
  Tools, Subagents, ordering, domain retries, and completion. The current
  blocking `RESTScopeApp.start()` entry launches that loop without a public
  task or result DTO; the removed FIFO Run Harness must not be restored.
- **System Agent** is a synchronous, repeatable root use of the same generic
  Agent, started only through a Harness-registered Profile/result contract.
  Every call owns an isolated prompt session and Agent tree and is closed after
  completion. It is not a Subagent and has no hidden Main-Agent state.
- **Agent Profile** explicitly names one model configuration, ordered Tools,
  Skills, bounded context sources, and the child Profiles it may start. A
  Profile may include a bounded description; every Profile named as a child
  must include one for its direct parent's delegation guidance. Global
  discovery never grants execution permission. The Harness validates the
  complete Profile graph once and constructs Agents through
  `start_main_agent` or registered `run_system_agent` calls; do not expose a
  separate resolve-and-assemble seam.
  A Subagent receives no hidden Main-Agent state and returns only a structured,
  bounded result. Failure Resolution and Parameter Patch methods now live in
  standard Skills; do not reintroduce the retired class-per-role Agents. Bounded Profile
  `instructions` are stable guidance for the Agent itself and remain distinct
  from the parent-visible `description`.
- **Skill** is reusable instruction and method knowledge. It does not execute
  code, own runtime state, or grant access. A Profile selects Skills explicitly,
  and the Harness verifies that the same Profile grants every Tool and bounded
  context source a selected Skill requires. Skill metadata is stable system
  context; the instruction body is added only after a successful Harness-owned
  `skill.read` call.
- **Tool** is one model-callable domain behavior. Every RESTScope-owned Tool
  lives under `restscope.tools`, grouped by the thing it handles, such as HTTP,
  OpenAPI, Resource, Test Case, Request Generation, Parameter Patch, Plan, or
  Skill. Its Tool Module owns the
  complete ToolSpec, execution Adapter, safe failure translation, output
  bounding, and directly supporting presentation code. Workflows and Harnesses
  may inject state but must not define private Tool contracts.
- **Harness** is deterministic runtime code. It owns Agent lifecycle, Profile
  validation, dependency injection, session state, Tool execution, output
  validation, tracing, and logs. A Harness must not make an LLM-owned domain
  decision. The Generator and Constraint language, compilation, solving,
  schema snapshots, serialization, validation, and mutable generation
  configuration belong to `restscope.request_generation`. The Harness owns
  deterministic operation execution and the mechanical injection of those
  capabilities into authorized Tools. `test_case.run_batch` returns bounded
  inline evidence and creates no run-local registry. The retired run-scoped FIFO and
  retry scheduler must not be restored; the blocking Main loop owns any future
  semantic scheduling through explicitly granted Skills and Tools.
- `run_system_agent(profile_name, task)` may start only a Profile registered by
  an immutable `SystemAgentDefinition`. Registration binds bounded task input
  and the structured result contract but grants no capability: the unchanged
  Profile remains the sole source of model, Tool, Skill, Context Source, and
  child-Profile permission. System roots count model usage without enforcing a
  token budget. The Harness gives every invalid final output bounded specific
  correction feedback without an attempt limit; cancellation, App shutdown,
  Provider errors, and safe compaction failure remain terminal.
- Built-in Tools form one immutable global Catalog. Runtime-discovered MCP Tools
  use a separate external Catalog. Every Agent receives only the exact names in
  its Profile; neither Catalog is automatically injected. `skill.read` is the
  sole narrow exception: selecting at least one `skill_name` authorizes the
  Harness to append that loader and only the selected Skill bodies. Profiles
  must not repeat it in `tool_names`; ordinary Tools remain explicit grants.
- `subagent.start`, `subagent.wait`, and `subagent.cancel` are the only
  model-facing child lifecycle protocol. An Agent may use them only when its
  Profile grants all three and names the target child Profile. Child access is
  direct-parent only, Profile graphs are acyclic, and child depth is at most
  three.
- `plan.read` and `plan.update` are an optional paired Profile grant for one
  Agent's private task Plan. The Harness creates a separate in-memory Plan for
  every Main Agent and Subagent session. Plans are not shared between Agents,
  persisted, or exposed as scheduler state.
- Main and child Agents share only deterministic tree control: weighted model
  budget, open/active slots, cancellation, tracing parentage, and bounded
  results. They do not share hidden conversation history. Model input is
  compacted at 80% with the same Profile model and no Tools; failed compaction
  must stop safely rather than delete history.
- Each Profile-started generic Agent owns one private, non-persistent Prompt
  Session. It assembles stable system and optional developer guidance, current
  tasks, changing Context Sources, on-demand Skill bodies, Tool/output protocol
  reservation, and compaction requests. Parent, child, and sibling Prompt state
  is never shared, and the Module is not a public Prompt Registry or DTO.
- Every RESTScope-owned Tool exposes one behavior. Do not use an `action`,
  `mode`, or `kind` input to select unrelated behaviors or result contracts.
  Target selectors, same-behavior batching, and natural result variants remain
  allowed. This rule does not apply to Agent final outputs, internal domain
  DTOs, or external MCP contracts.
- A Tool Schema is the authoritative model contract. Express required fields,
  bounds, patterns, uniqueness, closed objects, and standard cross-field rules
  such as `oneOf`/`const` in JSON Schema. Provider strict mode is optional;
  RESTScope must validate locally before execution and validate every successful
  structured output. Relationships JSON Schema cannot express require complete
  runtime validation before mutation and a safe, correctable `ToolFailure`, not
  `internal_tool_error`.
- Provider usage preserves cached-input counts when available. Shared rollout
  accounting charges output tokens at full weight and non-cached input tokens
  at one tenth; an over-budget response cannot execute a Tool action.
- Raw application logs, stack traces, provider payloads, and target secrets are
  human observability data, not Agent context. Agent-visible Tool, Subagent, and
  Harness results must be structured, bounded, and redacted.
- Keep `tests/test_workflow_package_boundaries.py`, Tool Catalog contracts, and
  Agent Profile contracts passing whenever these Modules move or expand.

## Agent Context boundary

- All direct LLM decisions use the public `restscope.context` Interface:
  `AgentContext`, `ContextLimits`, `ContextMetrics`, and `CompactTextWriter`.
- Skill- or Harness-owned adapters select and summarize facts before calling
  this Interface.
  Context does not query memory, interpret workflow DTOs, choose tools or
  models, validate final domain output, persist transcripts, or register Agents.
- Runtime-generated DTO, Memory, API, tool-result, and sample evidence reaches
  the model as bounded Markdown. Bounded HTTP request/response test-case and
  probe evidence is the sole prompt JSON exception and appears inside a safe
  Markdown JSON block. Final structured Agent output and provider-owned tool
  arguments/schema remain JSON.
- API responses, OpenAPI descriptions, Memory text, HTTP results, reference
  values, and samples are untrusted. Pass newly selected facts through
  `CompactTextWriter`; do not concatenate raw data into system, user, tool, or
  correction messages. A Profile Context Source Adapter is the narrow
  already-rendered exception: it returns bounded safe Markdown, the Harness
  redacts and validates its length, and the private Prompt Session adds a fixed
  untrusted envelope without encoding that Markdown a second time.
- Keep a Skill's or Harness's domain Context Adapter private to its owner. Do
  not add a role registry, Context inheritance tree, persistence lifecycle, or
  compatibility aliases for the deleted Context platform.
