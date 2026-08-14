# Apply the reviewed change and confirm current state

Application atomically replaces RESTScope's future test-input configuration.
It changes no target API state and supplies no HTTP evidence by itself.

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
samples, and recomputes the validation digest. It stages complete response
source replacements, publishes Generator, Constraint, reference-binding,
revision, state-digest, and last-applied-digest state, then commits the durable
source transaction while still holding the Operation lock. If that commit fails,
the previous in-memory state is restored before the lock is released. Any
failure therefore leaves both visible Generation State and durable source state
unchanged. Two concurrent calls using the same old revision cannot both
succeed.

For every changed `response_value` input, the submitted source is the entire
final source set. The runtime removes its previous sources and rebuilds values
only from retained observations matching the new source. Changing away from
`response_value` deletes that input's source; mentioning an input only through a
Constraint leaves its source unchanged. An exact no-op is rejected before the
durable write transaction opens.

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
5. Every final reference binding matches the reviewed canonical resource or
   exact response producer, and `removed_response_value_inputs` contains only
   inputs intentionally changed away from a response source.

Report the applied revision and digests, complete semantic changes, predicates
satisfied, final reference bindings, removals, and self-review findings. Do not report a
target API repair until a later `test_case.run_batch` supplies new HTTP
evidence. Application is App-lifetime only and disappears when RESTScope exits.
