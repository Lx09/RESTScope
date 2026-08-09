# Semantic review

## Self-review a compiled candidate

Review compiled final facts, not proposal intent. Require the normalized
requirement, exact affected inputs, before/after Generators, proposal,
reference provenance, active and candidate Constraints, and fresh samples.
Accept deterministic decisions about DTO shape, scope, schema compatibility,
reference validity, Constraint validity, compilation, and generation safety;
review semantic alignment instead of repeating them.

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
6. Treat samples as witnesses: pair `present` with `values` and reject any
   counterexample, but do not demand enumeration of the whole domain.
7. Reject unnecessary changes or weakening of unrelated behavior.

Do not replace value checks with HTTP success, an API status, or disappearance
of the Failure. A later real Smoke Batch measures target effects. Accept only
when final Generators and Constraints guarantee every predicate and samples
show no counterexample; otherwise report exact unmet predicates.

Keep root cause, affected boundary, and evidence authority fixed while
revising. Review feedback does not justify a new diagnosis or more
target-coupled source. The current production runtime keeps this review in an
independent fresh-context `ParameterPatchReviewAgent`; a future unified Agent
may use this checklist only after receiving the same normalized compiled facts.
