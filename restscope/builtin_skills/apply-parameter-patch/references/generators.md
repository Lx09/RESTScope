# Generator construction and complete replacement

## Contents

- Configuration and Patch boundary
- Scalar strategies
- Containers and variants
- Reference-backed strategies
- Presence and minimal repair

## Configuration and Patch boundary

Every active input node owns one strategy plus an `inclusion_probability` from
0 through 1. Required and structural nodes must use 1. A proposal addresses a
node with the supplied semantic `input` handle, never runtime `input_node_id`,
and must supply the complete final `strategy` and `inclusion_probability`.
Include only Generators that actually change; validation combines them with
every unchanged current configuration.

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
{"type":"resource_identifier","resource":"canonical-resource","identifier":"tenantId/userId","component":"userId"}
{"type":"response_value","source":{"operation_key":"GET /x","matched_status_code":"200","media_type":"application/json","field":"body.id"}}
```

For `resource_identifier`, discover and copy a canonical name with
`resource.list_resources`, then successfully call `resource.list_ids` for it.
Copy one returned `identifier` Definition and one of its ordered component
names exactly. The compiler requires a current non-empty Record set. A
single-component Definition may bind a compatible scalar input. A composite
Definition may bind only path parameters, and the same Patch must bind every
component exactly once using the same resource and identifier. Generation then
selects one complete Record and assigns all components together; it never mixes
components from different observations. JSON body values must match the
declared scalar type; integers may satisfy number. Other parameters serialize
scalars as text but still reject objects, arrays, and null pools.

For `response_value`, copy all four source fields exactly from a successful
`openapi.find_observed_response_fields` result. Compilation re-runs current
observation lookup, resolves the private pool name, checks complete provenance,
and requires non-empty type-compatible scalar values. Never emit the private
pool name. The selected source is the complete final source identity for that
input. Replacing it removes the old source and values derived only from that
source; it never appends another alternative. Changing the Generator away from
`response_value` removes the old response pool. Prefer `resource_identifier`
when it represents the same entity.

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
5. Add a reference strategy only after its current pool was queried.
6. Re-read the complete final state for accidental old controls or unrelated
   behavior changes.
