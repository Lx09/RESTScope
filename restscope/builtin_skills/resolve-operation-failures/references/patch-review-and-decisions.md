# Review Patch recommendations and decide

## Review values before outcomes

Turn every acceptance criterion into one atomic value or presence predicate and
check it separately against the recommended final state:

1. Confirm the affected-input set is minimal and complete.
2. Inspect each final Generator's entire possible domain, not just its type or
   samples.
3. Verify required presence, mandatory ancestors, shadowing container
   Generators, and enclosing variant selection.
4. Inspect the complete post-replacement Constraint set and every transitive
   participant.
5. Match every reference-backed Generator to the proven kind, canonical
   resource or producer, compatible type, and positive current value count.
6. Pair sample `present` and `values` fields. Reject every counterexample, but
   treat samples only as witnesses rather than exhaustive proof.
7. Reject unrelated edits, lost compatible relationships, or weakened behavior.

Do not replace these checks with HTTP success, an API status, disappearance of
the Failure, compilation success, a Reviewer verdict, or a few passing samples.

## Keep diagnosis and construction separate

Reject a child recommendation that changes the confirmed root cause or expands
the affected-input boundary. Return to parent investigation only when new
runtime evidence—not compiler, sampler, child, or Reviewer feedback—invalidates
those facts.

Treat protocol, scope, reference, variant, preview, Constraint, solver,
generation, and Reviewer failures as defects in the current candidate. Correct
one complete replacement. Do not escalate from ordinary generation to
`choice`, `resource_identifier`, or `response_value` without new runtime
evidence supporting that source.

## Record only defensible decisions

Use `apply_patch` only when a deterministic bridge has compiled, sampled,
semantically reviewed, and registered the exact recommendation as a real `P*`.
List that `P*` in `candidate_refs` and select the same reference.

Use `no_patch` only when evidence establishes a terminal reason, such as:

- a non-Parameter root cause;
- a target resource-state problem with no safe request-generation repair;
- an input requirement that current authorized value sources cannot safely
  satisfy;
- a confirmed repair outside Parameter Generator and Constraint ownership.

State the causal reason and select no candidate.

Leave the item undecided when evidence is insufficient, no authorized Patch
child exists, the child failed, or no deterministic candidate-registration
bridge exists. Missing runtime capability is not a business `no_patch` result.
