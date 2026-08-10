# Applied-state review and decisions

The Patch child owns construction and semantic self-review. The parent checks
the runtime and target boundaries:

1. Re-read every affected input plus returned Constraint participants.
2. Require the reported current revision, state digest, and last-applied
   validation digest to match Store output.
3. Require final Generators and Constraints to match the child's reported
   reviewed state without extra or missing scope.
4. Confirm the child did not change the parent-fixed root cause, value
   predicates, or affected-input boundary.
5. Run a fresh complete Batch from the applied revision.
6. Compare its inline requests/outcomes with the original evidence and predicted
   target effect.

Do not use Apply success as HTTP proof. Do not use Batch success, a status
change, or disappearance of a failure message as proof of all value predicates.
Do not use compiler success or finite samples as proof of an entire domain.

Choose one conclusion:

- `resolved`: the Store application is confirmed and new Batch evidence
  supports the specific target effect.
- `no safe parameter patch`: a confirmed non-parameter cause or evidence shows
  no safe Generator/Constraint repair.
- `unresolved`: evidence is insufficient, the child/Profile is unavailable,
  validation/application failed, Store confirmation differs, or the new Batch
  reveals a new problem.

A failed new Batch does not trigger automatic rollback. Diagnose its current
revision as new evidence. Restoring old behavior requires another complete
Patch validated and applied against the current revision.
