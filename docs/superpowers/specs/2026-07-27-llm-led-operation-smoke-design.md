# LLM-led Operation Smoke Design

Status: Superseded on 2026-08-05 by
`docs/tasks/failure-resolution-agent.md`

The Plan/Effect, separate Dedup/Solve, and per-Agent budget designs below are
historical. Current runtime behavior is described by
`docs/tasks/failure-resolution-agent.md` and `README.md`.

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
direction, root cause, Patch requirements, and Effect meaning.

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

## Budgets

The current public request gives Failure Dedup 50 outputs, gives each Solve 50
outputs, and caps one nested Patch/Review run at 20 outputs. Every successful
model response consumes the owning budget, including invalid output and tool
calls. Nested Patch and Review responses also consume the parent Solve budget;
provider failures do not. HTTP execution itself does not consume an output.

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

## 2026-08-03 Failure Solve terminal amendment

The checkpoint/continue protocol and model-selected conflict outcome are
removed. Every Solve model request offers the same tools with automatic tool
choice; a tool call itself continues investigation until the shared output
budget ends.

Terminal output is one flat object with only `action`, `candidate_ref`, and
`reason`. `apply_patch` must name a reviewed candidate from the current Solve
session and ignores reason. `no_patch` requires a non-blank reason and ignores
candidate reference. Runtime conflict exists only when atomic application of a
selected candidate detects changed Generator or Constraint state.

Each reviewed candidate carries the Patch task's root cause, value
requirements, independently checkable acceptance criteria, and affected-input
attribution. Applied and runtime-conflict Solve Attempts derive durable facts
from that candidate. A no-Patch Attempt stores only its reason and creates no
Parameter attribution.
