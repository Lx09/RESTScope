# LLM-led Operation Smoke Simplification

Status: Implemented and offline-verified; awaiting user review

## Approved scope

- Replace the deterministic diagnosis/Group state machine with independent
  Plan, Failure Solve, Parameter Patch, and Effect Agent packages.
- Make Operation Smoke a thin complete-batch coordinator with fixed-round todo
  order, same-seed candidates, atomic acceptance, and App-lifetime history.
- Replace legacy public budgets and diagnosis/Group DTOs without compatibility.
- Add model context-window configuration and evidence-priority prompt fitting.
- Preserve testing generation, Constraints, serialization, complete preflight,
  Behavior Monitor integration, and Catalog candidate transactions.
- Add no database tables or migrations.

## Implementation record

- Added `smoke_plan`, `failure_solver`, and `smoke_effect` packages.
- Reworked `parameter_patch` around one Solve-owned `PatchRequirement` and
  dynamic `case_count` samples.
- Replaced `operation_smoke` schemas, evidence, history, factory, and
  coordinator; removed diagnosis, grouping, planning, and old prompt modules.
- Moved the current-operation HTTP probe into Failure Solve.
- Added `context_window_tokens`, 8192 default output tokens, and shared prompt
  context fitting.
- Updated Supervisor stop reasons and public Agent facades.
- Added complete Operation IR context, App-close memory clearing, and a
  separate unexecuted Live verification proposal.

## Non-goals

- No real model, target API, or Phoenix call in this implementation task.
- No database persistence for raw evidence, Constraints, or Agent history.
- No commit, merge, branch cleanup, or worktree removal without separate user
  authorization.

## Verification

Fresh offline verification on 2026-07-27:

- Focused command from the approved plan: `52 passed`.
- Complete suite: `467 passed, 22 skipped`.
- `uv run python -m compileall -q restscope tests`: passed.
- `git diff --check`: passed.

The extra skipped tests include the former diagnosis/Group Project Swagger
Live contract, which is now explicitly superseded. Its replacement scope is
recorded in `llm-led-operation-smoke-live-verification-proposal.md`; it was not
executed because model names and target authorization are absent.

No real model, target API, or Phoenix request was sent. No commit, merge, push,
branch deletion, or worktree cleanup was performed.
