# LLM-led Operation Smoke Design

Status: User-approved on 2026-07-27; implementation in progress on
`codex/llm-led-operation-smoke`

## Decision

Operation Smoke uses a complete-batch, LLM-led loop:

```text
complete batch
→ Plan manages a fixed failure todo snapshot
→ one continuous Solve conversation per todo
→ one fresh Patch Agent per requirement
→ complete same-seed candidate batch
→ independent Effect decision
→ accept the whole Patch or roll it all back
→ next todo, then next Plan round
```

Code owns HTTP scope and authentication injection, strict structured outputs,
Generator/Constraint compilation and satisfiability, reference-pool safety,
complete preflight, Catalog candidate transactions, output counting, and final
2xx calculation. LLM roles own semantic failure deduplication, investigation
direction, root cause, Patch requirements, continuation, and Effect meaning.

## Role boundaries

- `operation_smoke_plan` uses THINK. It reads a complete batch and the
  App-lifetime ledger, associates every failed case with one or more distinct
  todos, and determines order. It does not diagnose or propose a Patch.
- `operation_smoke_failure_solve` uses THINK. Each todo receives a fresh Agent
  and continuous conversation. It may probe only the current method/path
  template and returns either one Patch requirement or a terminal todo status.
- `parameter_patch_agent` uses FAST. It compiles and locally samples one Solve
  requirement, then accepts its own latest complete Patch or replaces it.
- `operation_smoke_effect_validation` uses THINK. It compares the latest
  accepted batch and candidate batch and returns
  `resolved_without_regression`, `unresolved`, `regression`, or `unknown`.

Temporary case codes are valid only at the Plan boundary. Plan output is
immediately expanded, and later prompts contain complete evidence rather than
failure/case/observation aliases.

## Budgets and continuation

The public request defaults to 50 Plan outputs, 50 Solve outputs per todo, 20
Patch outputs per attempt, two Effect outputs, and a continuation interval of
ten. Every model response consumes its role budget, including invalid responses
and tool-call responses. HTTP execution itself does not consume an output.

At default outputs 10, 20, 30, and 40, Solve receives a tool-free continuation
prompt. It must provide a genuinely new direction or end the todo. Patch
exhaustion returns full failure details to the same Solve conversation. Effect
gets one protocol correction; a second invalid result is `unknown`.

## Candidate and state rules

Every Patch is accepted or rejected atomically. Acceptance requires Effect to
report that the target failure is resolved without regressing earlier success
or resolved work. No global 2xx override or partial Group/input acceptance
exists.

The coordinator keeps complete raw evidence and accepted Constraints in an
operation-isolated App-memory ledger. Generator changes continue through
Catalog candidate revisions. Accepted Constraints apply to later todos, rounds,
and Supervisor retries in the same App, but are not persisted. No database
schema or migration is added.

## Context and privacy

Model input allowance is:

```text
context_window_tokens - max_tokens - 2048
```

Current evidence has priority. Older history is loaded newest first and
summarized only when the window requires it. Oversized current response values
retain structure, original size, and marked head/tail excerpts.

The in-memory ledger can contain sensitive target requests and responses.
Authentication values remain injected outside model control and sensitive
request headers are redacted from evidence. Raw ledger data is not written to
the database or exposed in the public Smoke result, and disappears with the
App.

## Replaced behavior

This design supersedes the deterministic diagnosis state machine,
`EvidenceJournal`, `ActionableFailure`, Patch Groups, root provenance, presence
ownership, Group-level partial acceptance, fixed ten-sample Patch review, and
the former diagnosis/Group public DTOs and budgets. No compatibility layer is
provided.
