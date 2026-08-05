# Task Plan: Reference-based Failure Resolution Agent

## Goal
Replace the separate Failure Dedup and Failure Solve Agents with one continuous, Agent-owned Resolution session whose mutable worklist stores only references and small semantic text while a minimal deterministic harness owns registries, safety, validation, and atomic persistence.

## Current Phase
Phase 7 complete

## Phases

### Phase 1: Discovery and executable seams
- [x] Capture the approved behavior and project constraints
- [x] Map the current Agent, Memory, Patch, evaluation, and public Interface seams
- [x] Record the replacement design and migration inventory
- **Status:** complete

### Phase 2: Resolution contracts and worklist
- [x] Add red tests for reference-only worklist validation and the shared output guard
- [x] Implement Failure Resolution schemas, worklist store, prompt, registries, and tools
- [x] Add the shared 1000-output guard and progressive active-item feedback
- **Status:** complete

### Phase 3: Continuous Agent and finalization
- [x] Add red tests at the Resolution Interface
- [x] Implement the continuous Agent loop and on-demand tool set
- [x] Stage Patch decisions and atomically finalize final worklist decisions
- **Status:** complete

### Phase 4: Workflow replacement
- [x] Replace Operation Smoke composition and public DTOs
- [x] Remove old Dedup/Solve packages, roles, tests, and compatibility names
- [x] Merge evaluation suites and update current documentation
- **Status:** complete

### Phase 5: Verification and delivery
- [x] Run focused tests and resolve failures
- [x] Run full, evaluation, compile, boundary, and diff checks
- [x] Review the scoped diff and report uncommitted delivery
- **Status:** complete

### Phase 6: Bounded GitLab live diagnosis
- [x] Run the five-operation GitLab scenario with a 600-second hard cutoff
- [x] Add regressions and fix live-found compatibility and safety bugs
- [x] Re-run the complete offline suite and one final bounded live scenario
- **Status:** complete; two operations finished before the final time cutoff

### Phase 7: Failure investigation tool refinement
- [x] Exclude the redundant Failure-message lookup from Resolution
- [x] Document and test OpenAPI field discovery followed by exact TC value lookup
- [x] Run final focused, full-suite, compilation, and diff verification
- **Status:** complete

## Key Questions
1. How can final worklist decisions be committed atomically without persisting provisional Agent state?
2. How can Patch candidates remain authoritative registry objects while worklist writes contain only opaque references?
3. Which old public names and tests must be removed rather than wrapped?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| One `FailureResolutionAgent` session per failed Batch | The Agent owns semantic grouping, investigation, worklist evolution, and finish timing. |
| Worklist contains references and bounded semantic strings only | Precise Patch/Test Case/Memory objects remain authoritative in session registries. |
| Harness validates only types, references, coverage, tool safety, final Patch compatibility, and persistence | Semantic scheduling and completion judgments remain model-owned. |
| Final worklist decisions commit together at round finish | Provisional decisions can be freely rewritten without persistence cleanup. |
| One Operation-level 1000-output guard | All other Dedup/Solve/Patch/Review output and repetition stops are removed. |
| Live calls require a separate explicit request | The user authorized a bounded follow-up run after offline implementation. |
| Retry missing DeepSeek reasoning at most twice | A rejected response is hidden before any tool executes; three total requests absorb transient omissions without inventing continuation state or looping forever. |
| HTTP Probe requires an active worklist item | A target action has an explicit investigation owner and participates in per-item round feedback. |
| Resolution has no Failure-message lookup | Exact messages are initial E evidence; unclear HTTP failures use OpenAPI field discovery followed by one concrete TC field read. |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| New worklist tests failed because `failure_resolution` did not yet exist | 1 | Expected TDD red state; implement the new package contracts next. |
| Candidate-registry patch used an outdated test-file anchor | 1 | No source change was applied; inspected the current tail and split the patch into exact edits. |
| Five continuous-Agent tests failed because the new Agent Interface did not exist | 1 | Expected TDD red state; implement the session, prompt, and finish contracts next. |
| Verification shell has no `python` command | 1 | Use the available `python3` executable for compile checks. |
| Planning-file update used an ambiguous status anchor | 1 | Re-applied the update with phase-specific context; no partial edit occurred. |
| Focused test command used a non-activated `pytest` executable | 1 | Run verification through `.venv/bin/pytest`. |
| Test-fixture rename expected one extra import occurrence | 1 | Re-applied against the exact imports; no partial edit occurred. |
| Optional tracing test found no `restscope.llm.role` on model spans | 1 | Add the bounded internal Agent role to shared LLM request attributes. |
| Live run rejected mixed strict/non-strict DeepSeek tools | 1 | Keep local validation exact while projecting the mixed model-facing tool set uniformly non-strict. |
| Live PUT Failure exceeded the registry message limit | 1 | Keep the authoritative exact message unbounded in-session and bound only its prompt projection. |
| Live Agent probed before establishing an active work item | 1 | Reject HTTP Probe until `active_item_id` exists; read-only discovery remains available first. |
| DeepSeek twice omitted required thinking continuation content | 1 | Reject incomplete responses and allow two bounded pre-tool retries; never synthesize `reasoning_content`. |

## Notes
- Work occurs only in `/Users/lixin/Workplace/RESTScope-worktrees/merge-failure-agents` on `codex/merge-failure-agents`.
- Keep changes unstaged and uncommitted; Git delivery operations require separate authorization.
- Re-read this plan before every major design or migration decision.
