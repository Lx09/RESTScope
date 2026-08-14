# Review generated values and relationships

## Self-review validated final state

Review validated final facts, not Patch intent. Require the atomic value
predicates, exact affected inputs, final Generators and domains, reference
provenance, final Constraint closure, and deterministic witnesses.

Check in this order:

1. Turn every acceptance criterion into one value or presence predicate and
   check it independently.
2. Inspect each final Generator, including unchanged fields. Its entire possible
   domain—not just its type or samples—must meet the single-input predicate.
3. Inspect presence, mandatory ancestors, shadowing constant/choice ancestors,
   and variant selection. The intended input must actually be generated when
   required.
4. Inspect the post-replacement relationship set. Verify every cross-input
   rule and preservation of compatible old relationships.
5. Match every reference-backed input to its kind, canonical resource or
   producer identity, compatible type, and positive value count.
   For `response_value`, require exactly the reviewed source; do not accept an
   output that merely adds it beside a stale source.
6. Treat samples as witnesses: pair `presence` with `values` and reject any
   counterexample, but do not demand enumeration of the whole domain.
7. Reject unnecessary changes or weakening of unrelated behavior.

Do not replace value checks with compiler success, Apply success, HTTP success,
an API status, or disappearance of a failure. A later real test run measures
target effects. Apply only
when final Generators and Constraints guarantee every predicate and samples
show no counterexample; otherwise report exact unmet predicates.

Keep root cause, affected boundary, and evidence authority fixed while
revising. Review feedback does not justify a new diagnosis or more
target-coupled source. Perform this checklist before application; no separate
reviewer or finalizer compensates for skipped checks.
