# Atomic application and confirmation

## Apply only an exact validated replacement

Call `parameter_patch.apply` only after every value predicate passes semantic
self-review. Copy these fields exactly from the successful validation request
and result:

- `operation_key`
- `expected_revision`
- `affected_inputs` in the same order
- the complete semantic `patch`
- `seed`
- `sample_count`
- returned `validation_digest`

Apply obtains the operation write lock, checks revision and state digest,
recompiles the Patch, revalidates reference evidence, regenerates the same
samples, and recomputes the validation digest. It then registers all response
sources in one transaction and replaces Generator, Constraint, revision, state
digest, and last-applied digest as one in-memory state change. Any failure
leaves Generation Store state unchanged. Two concurrent calls using the same
old revision cannot both succeed.

## Handle conflicts and failures

- A state conflict means another Patch changed the operation. Call
  `request_generation.get_input_state`, compare the new final state with the
  fixed predicates and boundary, and validate a new complete Patch only if a
  change remains necessary.
- A digest mismatch means content, evidence, seed, sample count, or state no
  longer matches. Never substitute a new digest or retry blindly.
- Reference, compile, solve, or sample failure is a defect in the current
  replacement or changed evidence. It is not evidence for escalating to a more
  target-dependent Generator.
- A no-change rejection means current state already equals the proposed final
  state. Confirm whether the predicates are already satisfied instead of
  manufacturing a different change.

## Confirm the result

After success, call `request_generation.get_input_state` for the complete
affected boundary. Require:

1. `revision` equals the Apply result's `current_revision`.
2. `state_digest` equals the Apply result's `state_digest`.
3. `last_applied_validation_digest` equals the validated digest.
4. Every final Generator and active Constraint matches the reviewed state.

Report the applied revision and digests, complete semantic changes, predicates
satisfied, reference-source summary, and self-review findings. Do not report a
target API repair until a later `test_case.run_batch` supplies new HTTP
evidence. Application is App-lifetime only and disappears when RESTScope exits.
