# Compilation, generation, and sampling

## Proposal contract

Submit exactly one structured result:

```json
{"action":"propose","patch":{"changes":[{"input":"query.limit","strategy":{"type":"integer_range","minimum":1,"maximum":50}}],"constraints":[{"expression":{"type":"present","input":"query.limit"}}]}}
```

`changes` and `constraints` are the only Patch keys. A change uses `input`, not
`input_handle` or `input_node_id`, appears at most once, and changes strategy,
inclusion probability, or both. A Constraint is a recursive object, never a
string. The Patch cannot be empty.

## Compilation pipeline

The deterministic Coordinator:

1. Parses the strict DTO and builds semantic handles for the active media type.
2. Restricts all changes and Constraint references to exact `affected_inputs`.
3. Revalidates reference choices against successful lookups from this Patch
   session, current non-empty pools, scalar types, and exact provenance.
4. Converts semantic changes to `InputGeneratorPatch` objects.
5. Requires exclusive selection of every enclosing changed variant branch.
6. Converts Constraint handles to stable node IDs, validates, normalizes,
   classifies, and derives an ID from operation plus normalized content.
7. Previews the complete Generator state; mandatory-descendant presence may add
   ancestor updates. No stored state changes.
8. Preserves all old Constraints for a Generator-only Patch, or replaces the
   transitively overlapping owner scope when candidate Constraints exist.
9. Generates fresh samples from that complete state and sends normalized
   before/after facts, provenance, relationships, and samples to fresh-context
   semantic Review.

Only an accepted candidate enters Failure Resolution's run-local candidate
registry. Compilation, sampling, and Review do not persist Generator state.

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
set. The sample shown to Review contains affected inputs only:

```json
{"values":{"query.limit":25,"query.cursor":null},"present":{"query.limit":true,"query.cursor":false}}
```

Use `present` to distinguish absence from generated JSON null. Samples are
run-local witnesses, not exhaustive proof. Universal guarantees come from the
complete Generator domains and Constraints.

## Interpret failures

- Protocol errors identify wrong keys or recursive shapes.
- Scope errors identify unknown or unauthorized semantic handles.
- Reference errors identify an unqueried, stale, empty, incompatible, or
  provenance-mismatched pool.
- Variant errors mean the changed branch is not guaranteed.
- Preview errors mean the input tree cannot build structurally.
- Constraint errors identify bad references, repeated nodes, types, regex, or
  cardinality.
- Solver errors identify empty domains, bounded search exhaustion, no joint
  solution, or failed final projection/recheck.
- Generation errors identify an empty value source or schema/container
  violation.
- Reviewer issues identify an unmet value requirement or acceptance criterion.

These are current-candidate defects. Keep root cause and scope fixed and return
one complete corrected replacement. Do not escalate to `choice`,
`resource_identifier`, or `response_value` without new runtime evidence.
