# Generator construction and complete replacement

## Contents

- Configuration and Patch boundary
- Scalar strategies
- Containers and variants
- Reference-backed strategies
- Presence and minimal repair

## Configuration and Patch boundary

Every active input node owns one to eight positive Generator candidates. Each
candidate has a strategy plus an `inclusion_probability` from 0 through 1;
required and structural nodes must use 1. A proposal addresses a node with the
supplied semantic `input` handle, never runtime `input_node_id`. One or more
`changes` entries may repeat that handle, and together they replace its complete
positive candidate set. Include only inputs that actually change; validation
combines them with every unchanged input. Negative Generators are derived
deterministically from OpenAPI and cannot be changed by a Parameter Patch.

## Scalar strategies

Use these exact shapes:

```json
{"type":"constant","value":42}
{"type":"choice","values":["draft","published"],"weights":[1,2]}
{"type":"integer_range","minimum":1,"maximum":100}
{"type":"number_range","minimum":0.0,"maximum":1.0}
{"type":"random_string","min_length":3,"max_length":24,"alphabet":"abc123"}
{"type":"regex","pattern":"^[A-Z]{3}$","min_length":3,"max_length":3}
{"type":"boolean","true_probability":0.5}
{"type":"format","format":"uuid"}
```

- `constant` deep-copies one authoritative JSON value.
- `choice` needs a non-empty proven finite set. Optional weights must match the
  value count, be non-negative, and include a positive weight. Every
  positive-weight value remains possible and must be valid.
- Integer and number bounds are inclusive and must satisfy
  `minimum <= maximum`.
- `random_string` chooses an inclusive length then characters from `alphabet`.
  The alphabet may be empty only when `max_length` is zero. Do not use it for a
  schema pattern, format, enum, or constant.
- `regex` uses Python `re.search`; anchor whole-value rules. Its entire output
  must fit the length interval. It makes at most twenty bounded attempts and
  cannot generate every Python regex feature.
- `boolean` uses `true_probability`; 0 and 1 fix the value.
- `format` supports only `uuid`, `date`, `date-time`, and `email`.

Generated scalars are finally checked against frozen JSON type, nullability,
const/enum, numeric and exclusive bounds, `multipleOf`, string length, pattern,
and supported format. Preview proves structural buildability, not all possible
runtime values.

## Containers and variants

```json
{"type":"array","min_items":1,"max_items":3}
{"type":"variant","branch_weights":[0,1,0]}
```

`array` controls length; its item node owns the item Generator used for every
occurrence with different deterministic seeds. Keep bounds inside the schema.
The accepted configuration currently limits `uniqueItems` arrays to at most
one item.

`variant` selects a `oneOf` or `anyOf` branch and needs one weight per branch.
When changing a descendant, patch every enclosing variant to select that
descendant's branch exclusively: its weight is positive and all siblings are
zero. Otherwise another branch may be sampled. `allOf` object branches are all
generated and merged; overlapping properties must be equal.

Existing internal `object` and `request_body` strategies are structural and
are not proposal DSL options. If a container already uses `constant` or
`choice`, it supplies the complete value and shadows descendants; changing a
shadowed child has no effect.

## Reference-backed strategies

```json
{"type":"resource_identifier","source":{"operation_key":"GET /x","status_code":200,"media_type":"application/json","field":"body.userId"}}
{"type":"response_value","source":{"operation_key":"GET /x","status_code":200,"media_type":"application/json","field":"body.id"}}
```

For `resource_identifier`, first confirm the exact normalized resource with
`resource.list_resources` and a non-empty `resource.list_ids` result. Then use
`openapi.find_observed_response_fields` to copy the exact producer coordinates
for each identity field. The compiler resolves that source back to exactly one
operation-resource edge and requires current instances. A single identity field
may bind a compatible scalar input. Composite identity fields may bind only
path parameters, and the same Patch must bind every field exactly once. Each
field may have its own selector, but all must resolve to the same resource.
Generation selects one complete current instance and assigns all components
together; it never mixes components from different observations. JSON body
values must match the declared scalar type; integers may satisfy number. Other
parameters serialize scalars as text but still reject objects, arrays, or null.

For `response_value`, copy all four source fields exactly from a successful
`openapi.find_observed_response_fields` result. Compilation re-runs current
observation lookup, resolves the exact selector, checks complete provenance,
and requires non-empty type-compatible scalar values. Each selected source is
one candidate's complete source identity. The repeated entries for that input
define the final set, so omitted sources are removed. Later reads parse values
only from retained coordinates. Prefer `resource_identifier` when it represents
the same entity.

## Presence and minimal repair

Node inclusion is sampled independently. Making a nested descendant mandatory
therefore makes every ancestor mandatory too. Validation adds necessary
ancestor updates internally and rejects the Patch if those ancestors were
omitted from `affected_inputs`; an explicitly optional ancestor paired with a
mandatory descendant is invalid. A Constraint-required array item
forces at least one occurrence and fails if `max_items` is zero.

Build the Patch in this order:

1. Identify the node that controls the value, including shadowing ancestors and
   variant parents.
2. Preserve strategy when only presence is wrong and preserve presence when
   only the value domain is wrong.
3. Choose the least target-coupled strategy whose entire possible output domain
   satisfies the requirement and schema.
4. Add mandatory ancestors and exclusive branch selections needed to guarantee
   the value.
5. Add a reference strategy only after its current values were queried.
6. Re-read the complete final state for accidental old controls or unrelated
   behavior changes.
