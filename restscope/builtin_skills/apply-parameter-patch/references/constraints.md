# Constraint replacement and solving

## Contents

- Boundary and expressions
- Validation and finite-domain solving
- Common relationships
- Replacement scope

## Boundary and expressions

Use Constraints only for same-request relationships among affected inputs.
Put a single input's enum, constant, range, length, pattern, format, or source
in its Generator. Each `patch.constraints` item contains one recursive
`expression`; all top-level items must be true. Only supplied affected semantic
handles are legal.

Value expressions are:

```json
{"type":"input_value","input":"query.limit"}
{"type":"literal","value":10}
{"type":"arithmetic","operator":"+","left":VALUE,"right":VALUE}
```

`input_value` requires a present fixed scalar. Containers and repeated array
items cannot be value operands. Literals should be JSON scalars. Arithmetic
supports `+`, `-`, `*`, `/` with numeric operands; unavailable values or
division by zero make a surrounding comparison false.

Boolean expressions are:

```json
{"type":"present","input":"query.cursor"}
{"type":"compare","operator":"<=","left":VALUE,"right":VALUE}
{"type":"matches","value":VALUE,"pattern":"^[a-z]+$"}
{"type":"implies","condition":BOOLEAN,"consequence":BOOLEAN}
{"type":"cardinality","expressions":[BOOLEAN],"minimum":0,"maximum":1}
{"type":"and","expressions":[BOOLEAN]}
{"type":"or","expressions":[BOOLEAN]}
{"type":"not","expression":BOOLEAN}
```

Comparisons support `==`, `!=`, `<`, `<=`, `>`, `>=`. Numbers compare
numerically; other equality operands need the same JSON type. Ordering accepts
number-number or string-string. `matches` uses Python `re.search` and needs a
string. Implication is false only when its condition is true and consequence
false. Cardinality counts true children with inclusive bounds.

Use `expressions` for `and`, `or`, and `cardinality`; singular `expression` for
`not`; and `condition` plus `consequence` only for `implies`. Groups must be
non-empty and have at most 100 children. The Patch has at most 20 top-level
Constraints.

## Validation and finite-domain solving

After strict shape validation, semantic validation proves that inputs exist,
are not under repeated array items, and have compatible scalar operand types.
It validates regexes and cardinality bounds. Normalization sorts commutative
children, canonicalizes equality and `+`/`*` operands, then derives a stable
Constraint ID and classification.

Constraints do not generate arbitrary new values. The solver builds at most
eight candidates per referenced input:

- baseline first; then constant or all choice values;
- false and true for Boolean;
- range minimum, midpoint, maximum, then deterministic samples;
- deterministic samples for random string, regex, and format;
- current reference values, truncated to the domain limit;
- only presence states permitted by requiredness and inclusion probability.

Despite an older code comment, Constraint literals do not inject values into
the domain. Thus `x == 7` is solvable only if x's Generator exposes 7 among its
bounded candidates; patch the Generator when necessary.

The solver searches small, frequently referenced domains first, prunes with
three-valued partial evaluation, and stops after 10,000 complete assignments.
It returns the first deterministic solution. Generation rebuilds the request
with its overrides and re-evaluates all Constraints against the actual request.
Empty domains, exhaustion, unsatisfiability, projection errors, and final
recheck failures reject the candidate.

## Common relationships

Requires B when A is present:

```json
{"type":"implies","condition":{"type":"present","input":"query.a"},"consequence":{"type":"present","input":"query.b"}}
```

Exactly one of A and B uses a cardinality of `1..1`; at most one uses `0..1`;
at least one uses `1..N`. All-or-none is an `or` between cardinalities `0..0`
and `N..N` over the same expressions.

Guard a value relationship when absence is allowed:

```json
{"type":"implies","condition":{"type":"and","expressions":[{"type":"present","input":"query.start"},{"type":"present","input":"query.end"}]},"consequence":{"type":"compare","operator":"<=","left":{"type":"input_value","input":"query.start"},"right":{"type":"input_value","input":"query.end"}}}
```

## Replacement scope

`affected_inputs` seeds an ownership frontier. Runtime replaces every old
Constraint overlapping that frontier and expands transitively through other
old owners, even when the replacement list is empty. Unrelated old Constraints
remain. Include every participant in the affected boundary and submit the
complete corrected relationship set for that connected scope. Omitting one
compatible old relationship in the scope removes it.
