# Validation, compilation, generation, and sampling

## Validation Tool contract

Pass one complete semantic Patch to `request_generation.validate_patch`:

```json
{"operation_key":"GET /items","expected_revision":0,"affected_inputs":["query.limit"],"patch":{"changes":[{"input":"query.limit","inclusion_probability":1,"strategy":{"type":"integer_range","minimum":1,"maximum":50}}],"constraints":[]},"seed":0,"sample_count":5}
```

`changes` and `constraints` are the only Patch keys. A Generator change uses
`input`, not `input_handle` or `input_node_id`, appears at most once, and
supplies its complete final `strategy` and `inclusion_probability`. A
Constraint is a recursive object, never a string. A Constraint-only Patch may
have no Generator changes.

## Compilation pipeline

The deterministic validator:

1. Parses the strict DTO and builds semantic handles for the active media type.
2. Restricts all changes and Constraint references to exact `affected_inputs`.
3. Revalidates reference choices against the current canonical resource or
   observed response field, available current values, scalar types, and exact provenance.
4. Converts semantic changes to `InputGeneratorPatch` objects.
5. Requires exclusive selection of every enclosing changed variant branch.
6. Converts Constraint handles to stable node IDs, validates, normalizes,
   classifies, and derives an ID from operation plus normalized content.
7. Expands mandatory ancestors and rejects the Patch if `affected_inputs` did
   not include the expanded scope. No stored state changes.
8. Replaces every old Constraint directly or transitively intersecting
   `affected_inputs`, even when the new Constraint list is empty, while
   preserving unrelated Constraints.
9. Derives the complete final reference bindings. Exact response producer
   identity participates in final state identity, so a source-only replacement
   is a real revision change.
10. Generates deterministic samples and returns a digest binding the revision,
    state digest, semantic Patch, reference provenance, final state, seed,
    sample count, and witnesses.

## How values are generated

Each sample is deterministic. A node seed hashes run seed, case index, stable
node ID, concrete instance path, and purpose (`include`, `value`, `length`, or
`branch`). Identical complete inputs reproduce the same sample without shared
random state.

Generation first builds an unconstrained baseline: sample presence, recursively
build parameters and the active request body, choose container lengths and
variant branches, call scalar strategies, and validate frozen schema rules.

With Constraints, the baseline is only the first candidate. The finite-domain
solver derives values from the final Generators, preserves baseline choices
where possible, and finds one satisfying joint assignment. Generation runs
again with those presence/value overrides, projects the actual wire-shaped
request back into semantic assignments, and rechecks the complete Constraint
set. Validation returns affected inputs only:

```json
{"case_index":0,"values":{"query.limit":25},"presence":{"query.limit":true}}
```

Use `presence` to distinguish absence from generated JSON null. Samples are
run-local witnesses, not exhaustive proof. Universal guarantees come from the
complete Generator domains and Constraints.

## Interpret failures

- Protocol errors identify wrong keys or recursive shapes.
- Scope errors identify unknown or unauthorized semantic handles.
- Reference errors identify an unqueried, stale, empty, incompatible, or
  provenance-mismatched source.
- Variant errors mean the changed branch is not guaranteed.
- Preview errors mean the input tree cannot build structurally.
- Constraint errors identify bad references, repeated nodes, types, regex, or
  cardinality.
- Solver errors identify empty domains, bounded search exhaustion, no joint
  solution, or failed final projection/recheck.
- Generation errors identify an empty value source or schema/container
  violation.
- Self-review issues identify an unmet value predicate.

These are current-candidate defects. Keep root cause and scope fixed and return
one complete corrected replacement. Do not escalate to `choice`,
`resource_identifier`, or `response_value` without new runtime evidence.
