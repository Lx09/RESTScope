# Completion checklist

Before requesting finalization, verify every item below.

## Source and worklist integrity

- Every initial `E* -> TC*` association appears in at least one worklist item.
- Every reference was issued by the current session.
- Stable `WI-*` identities survived merges, splits, reopening, and reordering.
- Every decided item has one causal root cause.
- Undecided items remain explicitly undecided and are not persisted as terminal
  results.

## Parameter repair integrity

- Every Parameter root cause has 1–20 unique atomic value or presence
  predicates.
- Every Patch delegation fixed the root cause and smallest complete affected
  input scope before the child started.
- Each affected input has relevant Parameter history evidence.
- Every selected Patch is a real registered `P*`, not Subagent text.
- Selected candidates are unique and their direct or transitive Constraint
  scopes do not overlap.
- Final Generator and Constraint state satisfies every predicate while
  preserving compatible unaffected behavior.
- Samples are only witnesses and contain no counterexample.

## Decision and persistence integrity

- Every `apply_patch` item lists and selects the same registered candidate.
- Every `no_patch` item selects no candidate and states a proven terminal
  reason.
- Lack of evidence, child access, or candidate-registration capability remains
  undecided.
- Worklist text contains no request, response, Schema, Generator, Constraint,
  candidate, sample, history DTO, Tool result, database row, or Subagent
  completion.
- Drafts, rejected or unselected candidates, samples, probes, and conversation
  history are not treated as persisted evidence.

The Harness owns final reference validation, candidate compatibility checks,
fresh compilation and sampling, and atomic persistence. If it rejects
finalization, continue in the same Agent session and revise the worklist; do not
assume any partial write occurred. Only a later complete Smoke Batch measures
the applied Patch against the target API.
