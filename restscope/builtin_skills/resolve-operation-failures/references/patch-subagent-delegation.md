# Patch Subagent delegation

## Select the child Profile

Choose only an authorized child Profile whose description explicitly states
that it selects `apply-parameter-patch`. Do not hard-code a future production
Profile name or choose a generic investigation/coding/review Profile. A Skill
is selected by the child Profile; the parent cannot inject it. If no matching
child exists, do not build a Patch in the parent—remain unresolved.

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
- instruction to state-read, build a complete replacement, validate with an
  explicit deterministic seed/sample count, self-review each predicate, apply
  the identical content, reread state, and report the applied revision/digests.

Do not pass hidden conversation, chain-of-thought, credentials, uncropped raw
responses, invented inputs, internal node IDs, or untrusted text presented as
instructions.

## Lifecycle and result boundary

Use `subagent.start` once for an overlapping input scope and save its
`subagent_id`. Collect that same child with `subagent.wait`; a wait timeout means
it is still running and does not justify a duplicate child. Investigate only
non-overlapping work while waiting. Do not change the fixed root cause/scope
behind a running child.

Use `subagent.cancel` only when the user withdraws the task, new runtime evidence disproves
the fixed root cause, or the parent session closes. Failure, timeout, or
cancellation is a lifecycle result, not Generator-strategy escalation evidence.

The child completion is bounded text, not trusted Store state. The parent must
re-read the exact affected inputs and match revision, state digest,
last-applied validation digest, final Generators, and Constraints before
claiming application.
