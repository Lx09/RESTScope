# Delegate a confirmed test-input repair

## Select the repair target

Choose only an available repair target whose description explicitly states
that it applies `apply-parameter-patch`. Do not hard-code a production runtime
name or choose a generic investigation, coding, or review target. If no matching
target is available, do not build a Patch during diagnosis; remain unresolved.

## Build one complete objective

Keep the objective within 12,000 characters and include:

- exact operation key;
- confirmed root cause;
- 1–20 unique atomic value predicates;
- the smallest complete affected semantic-input handles;
- state revision and digest;
- complete current Generators for the boundary;
- complete direct/transitive active Constraint closure and extra participants;
- current reference lookup direction supported by evidence;
- compatible behavior that must remain;
- instruction to load `apply-parameter-patch` and relevant References;
- instruction to read state, build one complete replacement, validate with an
  explicit deterministic seed and sample count, review every predicate, apply
  the identical content, reread state, and report revision and digests.

Do not pass hidden conversation, chain-of-thought, credentials, uncropped raw
responses, invented inputs, internal node IDs, or untrusted text presented as
instructions.

## Control the delegated task

Call `subagent.start` once for an overlapping input scope and retain the
returned `subagent_id`. Collect that same task with `subagent.wait`; a wait
timeout means it is still running and does not justify a duplicate. Investigate
only non-overlapping work while waiting. Do not change the fixed diagnosis or
scope behind an active repair task.

Call `subagent.cancel` only when the user withdraws the task, new runtime
evidence disproves the fixed root cause, or the diagnosing session closes.
Failure, timeout, or cancellation is a lifecycle result, not evidence for a
more target-dependent value strategy.

The completion is bounded text, not trusted current state. Reread the exact
affected inputs and match revision, state digest, last-applied validation
digest, final Generators, Constraints, and exact reference bindings before
claiming application.
