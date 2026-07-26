# Runtime Parameter Constraints Design

Status: Approved design; implementation not started

## Context

RESTScope currently gives every OpenAPI input node an independent Generator
configuration. `restscope.testing.generation` uses those configurations to
produce one request at a time, while Operation Smoke may revise individual
Generators after observing failures.

Independent Generators cannot express relationships such as “include B when A
is present”, “exactly one of A and B”, or “end must be greater than start”.
Those relationships affect how a complete request is generated, so their
semantics and solver belong to the existing `restscope.testing` package rather
than to the Operation Smoke Agent.

The current local `main` does not yet contain the completed failure-scoped
candidate-validation work. Operation Smoke integration described here depends
on that work being committed and merged first. This design does not authorize
that Git integration.

## Goals

- Express same-request relationships among path, query, header, cookie, and
  fixed request-body input nodes.
- Generate deterministic requests that satisfy all active relationships.
- Keep ordinary unconstrained generation behavior unchanged.
- Let Operation Smoke infer, validate, and use constraints during its current
  run without owning constraint semantics.
- Reject an unsatisfiable constrained batch before sending any HTTP request.

## Non-goals

- Relationships across operations or across different requests.
- Constraints over individual occurrences inside repeated array items.
- Persisting inferred constraints, solver state, plans, or Agent reasoning.
- Adding a general SMT dependency or replacing the existing Generator Catalog.
- Changing the public behavior of unconstrained `run_operation()`.

## Package and Responsibility Boundaries

### `restscope.testing.constraints`

This module owns the immutable constraint contracts, validation,
normalization, classification, and evaluation.

It validates references against an `OperationTestSnapshot` and evaluates a
complete assignment in which every referenced input has two distinct
properties:

- `present`: whether the input will be included in the request;
- `value`: the value when present, including explicit JSON `null`.

The module has no dependency on Operation Smoke, HTTP execution, persistence,
or LLM-facing schemas.

### `restscope.testing.constraint_solver`

This module owns finite candidate-domain construction and bounded,
deterministic joint search. It consumes the frozen operation snapshot,
Generator configuration, active constraints, run seed, case index, and
reference-value provider. It returns input overrides for one complete request
or raises a typed solve error.

It does not serialize requests, send HTTP, mutate the Generator Catalog, or
manage the lifetime of inferred constraints.

### `restscope.testing.generation`

`generate_test_case()` remains the single request-generation entry point and
gains an optional constraint collection.

- With no constraints, it follows the existing code path exactly.
- With constraints, it obtains overrides from `constraint_solver`, passes them
  into the existing test-case builder, and re-evaluates the completed case
  before returning it.

The builder remains responsible for assembling parameters, objects, variants,
arrays, and request bodies. The solver does not duplicate that logic.

### `restscope.agent.operation_smoke`

Operation Smoke owns only:

- LLM-facing structured constraint decisions using semantic input paths;
- conversion from semantic paths to stable internal `input_node_id` values;
- attribution of a constraint change to diagnosed failure items;
- the current run's accepted constraint collection.

It delegates validation, evaluation, and solving to `restscope.testing`.

## Constraint Model

The constraint AST has the following primitives:

- `Present(input_node_id)` for input inclusion;
- `Value(input_node_id)` for an input value;
- `Compare(left, operator, right)` with `==`, `!=`, `<`, `<=`, `>`, and `>=`;
- `Matches(value, pattern)` for string regular expressions;
- arithmetic value expressions using `+`, `-`, `*`, and `/`;
- `Implies(condition, consequence)`;
- `Cardinality(expressions, minimum, maximum)`;
- `And`, `Or`, and `Not`.

All constraints in one constraint set are combined with logical AND.
RESTest-style labels such as Requires, OnlyOne, AllOrNone, ZeroOrOne,
Arithmetic/Relational, and Complex are derived from normalized AST shape. They
are not separate execution paths and are not supplied by the Agent.

An absent input makes value comparison, matching, and arithmetic predicates
false. Explicit `null` remains a present value. Division by zero and
type-incompatible ordered or arithmetic operations evaluate as false rather
than escaping as runtime exceptions.

Constraint validation rejects:

- unknown input references;
- value references to structural request-body or container nodes;
- references to repeated array-item occurrences;
- operators incompatible with the frozen input schema;
- malformed cardinality ranges or regular expressions.

## Constrained Generation

For every constrained case:

1. Generate the existing same-seed baseline assignment.
2. Build candidate domains only for inputs referenced by active constraints.
3. Put the baseline choice first, then add deterministic alternatives from the
   configured Generator.
4. Search joint presence/value assignments with deterministic backtracking and
   partial-expression pruning.
5. Apply the selected assignment as input overrides to the existing request
   builder.
6. Evaluate all constraints again against the completed generated case.

Candidate domains follow the Generator configuration:

- required inputs are always present;
- optional inputs consider their baseline presence plus the opposite state
  when their inclusion probability permits it;
- booleans consider both values;
- choice and reference Generators use their configured or available values;
- numeric ranges use the baseline value, valid boundaries, a midpoint, and
  deterministic samples;
- constant Generators contribute only their constant value;
- format and random-string Generators contribute bounded deterministic samples.

The initial implementation caps each value domain at eight unique candidates
and each case search at 10,000 explored assignments. Reaching the limit has the
same result as finding no solution; it never falls back to an unconstrained
request.

The solver returns an override with separate inclusion and value state so an
explicit `null` cannot be confused with omission. Overrides for a descendant
also require its structural ancestors to be present.

## Operation Smoke Data Flow

The Agent may propose Generator changes, constraints, or both for diagnosed
failure items. Before staging a Generator revision or sending HTTP, the system
validates the structured constraint patch and previews the combined Generator
and constraint configuration.

The candidate batch is generated with:

- all constraints already accepted in the current Smoke run;
- all new candidate constraints;
- the candidate Generator configuration.

Unsatisfiable or invalid candidate constraints produce a structured Patch
validation error and use the existing single FAST repair opportunity. If the
repair remains invalid, the diagnosis is inconclusive and no candidate HTTP
batch runs.

Accepted constraints remain active for later batches in the same
`OperationSmokeAgent` run and are discarded when that run ends. A
constraint-only Patch creates no empty Generator revision. For a mixed Patch,
only accepted Generator changes and existing aggregate evaluation metadata may
be persisted; constraint ASTs, identifiers, assignments, and solver traces are
never persisted.

`OperationTestingService.run_operation_for_smoke()` passes the current
constraint set into generation. It prepares and validates every constrained
case before sending the first HTTP request, ensuring that a later generation
failure cannot leave a partially executed candidate batch.

## Errors

Constraint failures use typed errors with stable categories:

- invalid constraint structure or input reference;
- unsupported constrained input;
- empty candidate domain;
- unsatisfiable constraints;
- search budget exhausted;
- completed-case recheck failure.

All are pre-transport failures. Error details identify affected internal input
nodes for system handling, while LLM-facing evidence continues to use semantic
paths.

## Verification

Tests will cover:

- AST validation, normalization, classification, and evaluation;
- missing versus explicit-null semantics;
- all Generator domain types used by the solver;
- deterministic results for the same seed and case index;
- cardinality, implication, arithmetic, regular-expression, and nested logical
  constraints;
- structural ancestor inclusion and rejection of repeated array items;
- unsatisfiable and search-budget failures with zero HTTP calls;
- unchanged unconstrained generation and `run_operation()` behavior;
- constraint-only and mixed Operation Smoke patches;
- acceptance, rejection, later-batch reuse, and end-of-run cleanup;
- absence of constraint persistence and empty Generator revisions.

Fresh focused tests, the full `uv run pytest -q` suite, import compilation, and
`git diff --check` are required before implementation is reported complete.
Live LLM, target API, or Phoenix execution is outside this design.
