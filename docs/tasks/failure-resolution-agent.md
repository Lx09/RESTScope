# Failure Resolution Agent Merge

Status: Completed

## Objective

Replace separate Failure Dedup and Failure Solve Agents with one continuous
Failure Resolution Agent. The Agent owns semantic grouping, investigation,
reference-only worklist changes, Patch selection, and round completion. A small
deterministic harness owns registries, tool safety, the shared output limit,
final validation, and atomic persistence.

## Approved scope

- Initial model context contains only operation identity and exact Failure to
  run-local Test Case associations.
- Worklist entries contain references and bounded semantic text, never precise
  Patch, Test Case, Schema, Memory, Generator, Constraint, or Attempt objects.
- The Agent may freely merge, split, duplicate, reorder, and rewrite worklist
  items. Final coverage requires each original Failure/Test Case association at
  least once.
- Parameter Patch candidates remain session-registry objects and are applied
  only after the Agent finishes the round.
- Final Failure, Attempt, Generator/Constraint, and change-event writes commit
  atomically from the final worklist.
- All Operation Smoke model calls share one 1000-output hard limit. No other
  Agent output or repetition stop remains.
- Repeated HTTP probes execute again and retain their existing target-mutation
  reporting requirements.
- Resolution does not receive the redundant `test_case.get_failure_messages`
  tool because every exact message is already in its initial prompt. When an
  HTTP message is unclear, it discovers contract paths with
  `openapi.list_response_fields` and reads a selected failed response value
  with `test_case.get_response_field_value`.
- At 80% of configured Resolution input capacity, a nested FAST Compact Agent
  receives the unchanged system prompt `B`, complete saved history `H`, and a
  temporary instruction `C`. Its Markdown summary `S` replaces old dynamic
  exchanges as `H' = U + S`; worklists and registries remain unchanged.
- Compact retries one provider failure or empty summary, consumes the shared
  output guard, and falls back to mechanical history projection after a second
  failure without changing the original history.
- Remove the old packages, roles, public contracts, and evaluation split
  without compatibility aliases.
- Follow-up decision approved on 2026-08-05: remove Agent-authored
  `failure_summary` from each worklist item. Finalization derives a bounded
  stable display summary from authoritative E messages, while `root_cause`
  remains the Agent's Attempt-level diagnosis. The existing database summary
  column and public round summary remain unchanged.

## Non-goals

- Persisting worklists, Agent conversations, provisional root causes, rejected
  candidates, raw Test Cases, or response bodies.
- A general Agent memory or scheduler framework.
- Live model, Phoenix, or target API verification was outside the original
  implementation scope; the user separately authorized a bounded GitLab live
  diagnostic run after implementation.
- Git staging, commit, merge, push, or worktree/branch cleanup without separate
  authorization.

## Verification record

- Pre-change focused baseline: 52 passed.
- Core Resolution, worklist, finalization, output-limit, Coordinator, and
  Parameter Patch/Review checks: 91 passed.
- Single Resolution Phoenix Evaluation suite: 7 passed offline.
- Full optional-dependency test group after live-found fixes: 656 passed,
  5 skipped.
- Compilation, package-boundary, Evaluation registry, and `git diff --check`
  verification passed.
- Three bounded GitLab live attempts invoked the configured DeepSeek model and
  the local GitLab target. The first exposed mixed DeepSeek strictness and an
  over-bounded exact Failure registry message; both were fixed. The second
  exposed pre-worklist HTTP probing and an intermittent missing DeepSeek
  `reasoning_content`; both received regression coverage and bounded fixes.
- The final 600-second run did not crash. Before the hard cutoff it completed
  two operations, committed five Patch Attempts/change events, recorded 93
  model calls and 40 real Test Case executions, and had no recurrence of the
  three compatibility exceptions. It did not finish all five operations inside
  ten minutes, so full live-suite completion remains unverified.
- Failure-investigation tool refinement: 18 focused Catalog/Resolution tests
  and 24 Evaluation, package-boundary, and tracing tests passed. Final full
  verification passed with 657 tests and 5 environment-dependent skips.
- Resolution Local Compact verification: 124 focused Context, Resolution,
  Compact, DeepSeek, model-selection, package-boundary, Evaluation, tracing,
  and output-limit tests passed. The final optional-dependency suite passed
  with 671 tests and 5 environment-dependent skips. Python compilation and
  `git diff --check` also passed. No real model or target API was called.
- Post-Compact GitLab live verification ran for the full caller-enforced 600
  seconds. Phoenix flushed 452 OK spans with no ERROR spans, including 103 real
  model calls and 40 real Test Case executions. Two completed Resolution
  sessions persisted five Failures, five Attempts, and five matching Generator
  change events across GET/POST Projects evidence before the cutoff. Resolution
  reached at most 67,067 prompt tokens, so the 524,288-token Compact threshold
  was not reached in this run. The first launch also exposed and then gained a
  regression fix for sibling-worktree `.env` discovery in both GitLab harnesses.
- Worklist summary follow-up: 47 focused Resolution, finalization, Coordinator,
  and Evaluation tests passed. The complete optional-dependency suite passed
  with 675 tests and 5 environment-dependent skips. Python compilation and
  `git diff --check` also passed. No database migration, real model call, or
  target API request was performed.
