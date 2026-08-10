# Evidence and diagnosis

## Evidence authority

Use current evidence in this order:

1. A failure message that states a concrete value or presence rule.
2. The actual inline Batch request and its HTTP/transport outcome.
3. A controlled Probe that changes only inputs in one explicit hypothesis.
4. Current Generator/Constraint state and current observed reference pools.
5. OpenAPI Schema, description, example, and response contract.
6. Model knowledge, which may form a hypothesis but never prove one.

OpenAPI declarations alone do not prove why a live request failed. HTTP success,
status change, or disappearance of one message is diagnostic evidence, not a
proof that every requested value predicate holds.

## Diagnose parameter causes

Inspect whether the failed request omitted, unexpectedly included, or supplied
an empty input. Then check type, format, enum, numeric/string/array bounds,
resource identity, and same-request relationships. Consider:

- an identifier that is syntactically valid but absent, stale, or belongs to a
  different canonical resource;
- a choice that is not valid for the current resource or lifecycle state;
- a response-derived value whose producer, response contract, selector, media
  type, or retained pool has drifted;
- equality, inequality, ordering, arithmetic, dependency, cardinality, and
  conditional-presence relationships;
- a value that is valid in isolation but incompatible with the HTTP method,
  concrete path, resource state, or another input.

A root cause must explain why the current value, presence state, possible
Generator domain, or cross-input relationship caused the observed failure. Do
not restate the message or describe the desired Patch as the cause.

## Recognize non-parameter causes

Authentication, authorization, unsupported methods, resource lifecycle/state,
server failure, transport failure, and response-contract changes may have no
safe request-generation Patch. State the evidence and leave uncertain causes
unresolved. Do not patch arbitrary inputs merely because a Batch failed.
