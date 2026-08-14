# Verify the input repair and decide the outcome

The delegated repair task owns Patch construction and semantic self-review.
The diagnosing session verifies current configuration and target behavior:

1. Re-read every affected input plus returned Constraint participants.
2. Require the reported current revision, state digest, and last-applied
   validation digest to match `request_generation.get_input_state`.
3. Require final Generators, Constraints, and exact reference bindings to match
   the reported reviewed state without extra or missing scope.
4. Confirm the fixed root cause, value predicates, and affected-input boundary
   did not change during repair.
5. Run a fresh complete grouped test run from the applied revision.
6. Compare its requests and outcomes with the original evidence and predicted
   target effect.

Do not use Apply success as HTTP proof. Do not use test-run success, a status
change, or disappearance of a failure message as proof of every value
predicate. Compiler success and finite samples do not prove an entire domain.

Choose one conclusion:

- `resolved`: current configuration is confirmed and new test-case evidence
  supports the specific predicted target effect.
- `no safe parameter patch`: a confirmed non-input cause or evidence shows no
  safe Generator/Constraint repair.
- `unresolved`: evidence is insufficient, no repair target is available,
  validation or application failed, current state differs, or the new test run
  reveals another problem.

A failed new test run does not trigger automatic rollback. Diagnose the current
revision as new evidence. Restoring old behavior requires another complete
Patch validated and applied against the current revision.
