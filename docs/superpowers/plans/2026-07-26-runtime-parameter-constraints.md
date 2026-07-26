# Runtime Parameter Constraints Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Project rules require inline primary-Agent execution unless the user explicitly requests delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic, run-local same-request parameter constraints to the existing `restscope.testing` generation system and integrate them with Operation Smoke after its failure-scoped Patch prerequisite is merged.

**Architecture:** `restscope.testing.constraints` owns the typed AST, validation, classification, and evaluation. `restscope.testing.constraint_solver` converts existing Generator configurations into bounded candidate domains and returns input overrides. `generation.py` remains the single case-generation entry point, while Operation Smoke only infers constraints and owns their current-run lifetime.

**Tech Stack:** Python 3.11, Pydantic v2 discriminated unions, deterministic `random.Random`, pytest, existing RESTScope testing and Operation Smoke packages.

**Design:** `docs/superpowers/specs/2026-07-26-runtime-parameter-constraints-design.md`

**Git boundary:** No staging, commit, merge, rebase, or cleanup is authorized. The Operation Smoke tasks must not begin until `codex/fix-smoke-inconclusive-supervisor` is committed and merged into local `main`, then incorporated into this worktree with separate authorization.

---

## File Structure

- Create `restscope/testing/constraints.py`: immutable AST, assignments and overrides, validation, normalization, classification, and total evaluation.
- Create `restscope/testing/constraint_solver.py`: bounded Generator domains and deterministic joint search.
- Modify `restscope/testing/generation.py`: accept optional constraints and apply solver overrides without changing the unconstrained path.
- Modify `restscope/testing/execution.py`: accept constraints only on the Smoke path and pre-generate the whole batch before transport.
- Modify `restscope/testing/__init__.py`: export the contracts needed by Operation Smoke.
- Create `tests/test_testing_constraints.py`: AST and evaluator tests.
- Create `tests/test_testing_constraint_solver.py`: domain and search tests.
- Modify `tests/test_testing_generation.py`: constrained generation and unconstrained regression tests.
- Modify `tests/test_testing_execution.py`: zero-HTTP preflight and Smoke-only interface tests.
- Create `docs/tasks/runtime-parameter-constraints.md`: approved scope, dependency, progress, verification, and remaining risks.
- After the prerequisite merge, modify the existing Operation Smoke schemas, prompts, diagnosis, Agent runtime, and their focused tests to add constraint Patch attribution and run-local acceptance.

### Task 1: Constraint Contracts and Task Record

**Files:**
- Create: `docs/tasks/runtime-parameter-constraints.md`
- Create: `tests/test_testing_constraints.py`
- Create: `restscope/testing/constraints.py`
- Modify: `restscope/testing/__init__.py`

- [ ] **Step 1: Record the approved task and prerequisite**

Create a task record with status `In progress`, the approved same-request scope, the `restscope.testing` ownership decision, non-goals, the unmerged failure-scoped Patch dependency, and the baseline result `375 passed, 16 skipped`.

- [ ] **Step 2: Write failing AST construction tests**

Cover recursive parsing and frozen/forbid-extra behavior with public contracts shaped as:

```python
constraint = ConstraintSet(
    constraints=[
        ImplicationConstraint(
            type="implies",
            condition=PresentPredicate(type="present", input_node_id="query.mode"),
            consequence=ComparePredicate(
                type="compare",
                operator="==",
                left=InputValue(type="input_value", input_node_id="query.limit"),
                right=LiteralValue(type="literal", value=10),
            ),
        )
    ]
)
```

Also assert that `InputAssignment(present=True, has_value=True, value=None)` differs from omission and that an override can explicitly supply `null`.

- [ ] **Step 3: Run the focused test and confirm RED**

Run:

```bash
uv run pytest -q tests/test_testing_constraints.py
```

Expected: collection failure because `restscope.testing.constraints` does not exist.

- [ ] **Step 4: Implement the immutable contracts**

Define:

```python
class InputAssignment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    present: bool
    has_value: bool = False
    value: Any = None

class InputNodeOverride(InputAssignment):
    pass

class InputValue(BaseModel):
    type: Literal["input_value"]
    input_node_id: str

class LiteralValue(BaseModel):
    type: Literal["literal"]
    value: Any

class ArithmeticValue(BaseModel):
    type: Literal["arithmetic"]
    operator: Literal["+", "-", "*", "/"]
    left: ValueExpression
    right: ValueExpression
```

Add predicate models for `present`, `compare`, `matches`, `implies`, `cardinality`, `and`, `or`, and `not`; use discriminated recursive unions and call `model_rebuild()` after all definitions. Define `ConstraintSet` with at most 20 top-level expressions and reject an empty set.

`InputAssignment` validation must enforce:

- absent means `has_value=False`;
- `has_value=True` may carry any value, including `None`;
- structural presence may be represented by `present=True, has_value=False`.

- [ ] **Step 5: Export the public contracts and confirm GREEN**

Export `ConstraintSet`, expression leaf types, `InputAssignment`, and `InputNodeOverride` from `restscope.testing`. Re-run the focused test and expect all contract tests to pass.

### Task 2: Validation, Classification, and Total Evaluation

**Files:**
- Modify: `tests/test_testing_constraints.py`
- Modify: `restscope/testing/constraints.py`

- [ ] **Step 1: Write failing validation and evaluation tests**

Add fixtures with parameter, request-body, object-property, and array-item snapshots. Test:

- unknown input references;
- value access to request-body/container nodes;
- array-item descendant rejection;
- invalid regex and cardinality bounds;
- absent versus explicit-null equality;
- implication, cardinality, nested AND/OR/NOT;
- numeric arithmetic and ordered comparison;
- division by zero and incompatible types returning false.

Assert classification produces Requires, Or, OnlyOne, AllOrNone, ZeroOrOne, Arithmetic/Relational, or Complex from normalized AST shape without a model-supplied label.

- [ ] **Step 2: Run the focused test and confirm RED**

Run the constraint test module and expect missing validator/evaluator functions.

- [ ] **Step 3: Implement snapshot validation**

Add:

```python
def validate_constraint_set(
    constraints: ConstraintSet,
    operation: OperationTestSnapshot,
) -> ConstraintSet:
    ...
```

Walk all input references, map them to `InputNodeSnapshot`, reject unsupported references, compile regexes, check cardinality `0 <= minimum <= maximum <= len(expressions)`, and verify ordered/arithmetic operands have compatible frozen scalar schema types. Raise `ConstraintValidationError(code, message, input_node_ids=...)`.

- [ ] **Step 4: Implement normalization and classification**

Canonicalize commutative child ordering and literal JSON representation. Add:

```python
def classify_constraint(expression: BooleanExpression) -> ConstraintKind:
    ...
```

Use AST shape only. Cardinality `[1, n]` maps to Or, `[1, 1]` to OnlyOne, `[0, 1]` to ZeroOrOne, and `[0, 0] | [n, n]` expressed through equivalent all-or-none shape maps to AllOrNone; nested or mixed shapes map to Complex.

- [ ] **Step 5: Implement total evaluation**

Add:

```python
def evaluate_constraint_set(
    constraints: ConstraintSet,
    assignments: Mapping[str, InputAssignment],
) -> bool:
    ...
```

Use an internal unavailable sentinel so absent input and explicit `None` remain distinct. Equality supports JSON scalar equality, ordered/arithmetic operations exclude booleans, matching uses `re.search`, and evaluation never leaks type or division exceptions.

- [ ] **Step 6: Run focused tests and confirm GREEN**

Run `tests/test_testing_constraints.py` and expect all cases to pass.

### Task 3: Deterministic Finite-Domain Solver

**Files:**
- Create: `tests/test_testing_constraint_solver.py`
- Create: `restscope/testing/constraint_solver.py`

- [ ] **Step 1: Write failing domain tests**

Build small frozen configs and assert:

- constants have one value;
- booleans expose baseline first and the opposite value second;
- choice/reference values are deterministic and deduplicated;
- integer/number ranges include baseline, valid boundaries, midpoint, and seeded samples;
- optional presence includes the baseline state and permitted opposite state;
- required presence never includes absent;
- no value domain exceeds eight entries.

- [ ] **Step 2: Write failing search tests**

Test implication, exactly-one, arithmetic order, multiple simultaneous constraints, structural ancestor inclusion, same-seed repeatability, unsatisfiable constraints, and a deliberately tiny search budget.

The public entry point is:

```python
def solve_input_overrides(
    *,
    operation: OperationTestSnapshot,
    config: OperationGeneratorConfig,
    constraints: ConstraintSet,
    baseline: GeneratedTestCase,
    run_seed: int,
    case_index: int,
    reference_values: ReferenceValueProvider | None = None,
    max_domain_size: int = 8,
    max_search_states: int = 10_000,
) -> dict[str, InputNodeOverride]:
    ...
```

- [ ] **Step 3: Run the solver tests and confirm RED**

Expected: collection failure because `constraint_solver.py` does not exist.

- [ ] **Step 4: Implement candidate-domain construction**

Derive baseline assignments from `GeneratedTestCase.generated_values` and `omitted_input_node_ids`. Generate alternatives with stable SHA-256-derived seeds rather than process-random hashes. Use only existing Generator strategies and `ReferenceValueProvider`; an empty required reference pool raises `ConstraintSolveError("constraint_empty_domain", ...)`.

- [ ] **Step 5: Implement bounded deterministic search**

Order referenced inputs by smallest domain, then descending reference count, then `input_node_id`. Search assignments in domain order, count every explored choice, prune only when a three-valued partial evaluation proves false, and raise:

```python
ConstraintSolveError("constraint_unsatisfiable", ...)
ConstraintSolveError("constraint_search_exhausted", ...)
```

Propagate presence to structural ancestors in returned overrides and validate the final complete assignment.

- [ ] **Step 6: Run constraint and solver tests and confirm GREEN**

Run both focused modules. Repeat the deterministic tests in a second process to ensure no hash-order dependency.

### Task 4: Integrate Constraints into Test-Case Generation

**Files:**
- Modify: `tests/test_testing_generation.py`
- Modify: `restscope/testing/generation.py`

- [ ] **Step 1: Write failing constrained-generation tests**

Add tests proving:

- `generate_test_case(..., constraints=None)` is byte-for-byte/model-dump identical to the previous path;
- implication can force an optional parameter present;
- a value override can supply explicit `None`;
- body-property overrides force request body and structural ancestors present;
- final generated metadata records overridden values and omissions correctly;
- final recheck failure raises `ConstraintSolveError("constraint_recheck_failed", ...)`.

- [ ] **Step 2: Run the focused test and confirm RED**

Expected: `generate_test_case()` rejects the new keyword.

- [ ] **Step 3: Add internal override support**

Extend `_TestCaseGenerator` with an override mapping. `_included()` uses an override before RNG. Scalar generation uses an override value before the configured strategy. Record overridden scalar values in `generated_values`, and record forced omission exactly once.

- [ ] **Step 4: Add the optional constraint path**

Change the entry point to:

```python
def generate_test_case(
    operation: OperationTestSnapshot,
    config: OperationGeneratorConfig,
    *,
    run_seed: int,
    case_index: int,
    reference_values: ReferenceValueProvider | None = None,
    constraints: ConstraintSet | None = None,
) -> GeneratedTestCase:
    ...
```

Generate the ordinary baseline first. If constraints are absent, return it immediately. Otherwise validate the set, solve overrides, rebuild through `_TestCaseGenerator`, reconstruct assignments from the completed case, and re-evaluate before returning.

- [ ] **Step 5: Run generation, constraint, and solver tests**

Expect all focused modules to pass with the existing unconstrained snapshots unchanged.

### Task 5: Smoke-Only Execution Interface and Zero-HTTP Preflight

**Files:**
- Modify: `tests/test_testing_execution.py`
- Modify: `restscope/testing/execution.py`

- [ ] **Step 1: Write failing execution tests**

Test that:

- `run_operation()` remains unchanged and unconstrained;
- `run_operation_for_smoke(..., constraints=...)` passes the set to every case;
- all cases are generated and serialized before the first transport request;
- an unsatisfiable second case results in zero transport calls;
- tracing input includes only constraint count, never AST values.

- [ ] **Step 2: Run the focused test and confirm RED**

Expected: `run_operation_for_smoke()` rejects `constraints`.

- [ ] **Step 3: Thread constraints through the private execution path**

Add an optional `constraints` keyword only to `run_operation_for_smoke`, `_run_operation_traced`, and `_run_operation`. `run_operation` always supplies `None`. Pass the set to `generate_test_case` during the existing preparation loop.

- [ ] **Step 4: Preserve preflight error boundaries**

Catch `ConstraintValidationError` and `ConstraintSolveError` before transport and raise `TestingExecutionError` with the same stable code and a bounded message. Preserve the existing behavior for unconstrained `GenerationError`.

- [ ] **Step 5: Run focused execution and testing regressions**

Run:

```bash
uv run pytest -q tests/test_testing_constraints.py tests/test_testing_constraint_solver.py tests/test_testing_generation.py tests/test_testing_execution.py tests/test_testing_serialization.py
```

Expected: all pass.

### Task 6: Operation Smoke Constraint Patch Integration

**Prerequisite:** The completed `codex/fix-smoke-inconclusive-supervisor` work must be committed, merged into local `main`, and incorporated into this branch with explicit authorization. Stop before this task if that has not happened.

**Files:**
- Modify: `restscope/agent/operation_smoke/schemas.py`
- Modify: `restscope/agent/operation_smoke/prompts.py`
- Modify: `restscope/agent/operation_smoke/diagnosis.py`
- Modify: `restscope/agent/operation_smoke/agent.py`
- Modify: `restscope/agent/operation_smoke/__init__.py`
- Modify: `tests/test_operation_smoke_plan_solve.py`
- Modify: `tests/test_operation_smoke_agent.py`

- [ ] **Step 1: Write failing structured Patch tests**

Add semantic-path LLM schemas mirroring the testing AST without exposing internal IDs. Each top-level constraint carries unique `item_ids`. Test unknown paths, paths outside each item's `affected_inputs`, duplicate normalized constraints, constraint-only patches, and mixed generator/constraint patches.

- [ ] **Step 2: Compile semantic paths into testing constraints**

Use the existing evidence input registry to translate semantic paths to `input_node_id`. Derive stable constraint IDs from canonical JSON and expose derived RESTest kinds only as system metadata.

- [ ] **Step 3: Add side-effect-free Patch preflight and one repair**

Preview Generator updates without creating a revision, combine accepted run constraints with candidate constraints, and solve every candidate case using the same seed. Feed validation/solve errors into the existing single FAST repair. A second failure returns `inconclusive` before reference registration, revision staging, or HTTP.

- [ ] **Step 4: Extend failure-scoped validation**

Record accepted and rejected constraint IDs beside generator input IDs. A candidate constraint is exercised only when every candidate case satisfies it and at least one same-seed baseline case violates it. Preserve the prerequisite branch's item-attribution and partial-acceptance semantics.

- [ ] **Step 5: Maintain run-local accepted constraints**

Pass active plus candidate constraints to `_run_smoke_batch`. After validation, retain only accepted candidate constraints. Support a constraint-only pending diagnosis without creating an empty catalog revision. Clear all active constraints on every Agent return and exception path.

- [ ] **Step 6: Run Operation Smoke focused regressions**

Cover constraint-only success, mixed partial acceptance, cumulative later rounds, rejection, global-threshold acceptance, technical-error cleanup, and a second Smoke invocation starting empty.

### Task 7: Verification and Truthful Handoff

**Files:**
- Modify: `docs/tasks/runtime-parameter-constraints.md`

- [ ] **Step 1: Run package-boundary and focused tests**

Run all testing and Operation Smoke focused modules plus `tests/test_agent_package_boundaries.py`.

- [ ] **Step 2: Run complete verification**

Run:

```bash
uv run pytest -q
uv run --extra tracing pytest -q
uv run python -m compileall -q restscope
git diff --check
```

Record exact observed results. Do not infer live target, LLM, or Phoenix behavior from offline tests.

- [ ] **Step 3: Self-review the scoped diff**

Confirm no constraint AST, IDs, assignments, solver traces, or Agent state enters database models, repositories, migrations, public reports, or LangGraph state. Confirm all unrelated main-worktree files remain untouched.

- [ ] **Step 4: Update the task record**

Mark only completed phases as completed. If Task 6 remains blocked by its prerequisite, leave the overall task `In progress` and state the exact blocker.

- [ ] **Step 5: Stop at the Git authorization gate**

Summarize the exact unstaged diff and verification. Request explicit authorization before any `git add` or `git commit`; do not merge, push, or remove worktrees without separate authorization.
