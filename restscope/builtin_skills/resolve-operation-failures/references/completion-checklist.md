# Completion checklist

Before completing, confirm:

- every original failed test case is accounted for;
- every decided cause is explicit and evidence-backed;
- every parameter cause has 1–20 atomic value predicates;
- every repair delegation fixed the operation, root cause, predicates, and
  complete affected-input boundary before the delegated task started;
- no overlapping input-repair tasks ran concurrently;
- no delegated completion was accepted without a fresh state read;
- a confirmed Apply was followed by a new complete grouped test run;
- Apply success was not described as target success;
- test-run success was not substituted for Generator-domain/Constraint proof;
- no Test Case, Failure, Worklist, candidate, sample, Probe, Plan, conversation,
  or request-generation history was persisted;
- insufficient capability or evidence remains `unresolved`, not disguised as
  `no safe parameter patch`;
- mutating Probes and grouped test runs are reported as possible target state
  changes.
