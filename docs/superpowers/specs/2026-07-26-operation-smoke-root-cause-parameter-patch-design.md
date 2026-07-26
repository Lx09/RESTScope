# Operation Smoke Root-Cause and Parameter Patch Design

Status: User-approved; implemented in `codex/operation-smoke-root-cause-patch-agent`

## Problem

The former Operation Smoke loop asked one diagnosis Agent to maintain a
multi-failure PlanState and then directly compile one joint Generator/Constraint
Patch. That made two responsibilities indistinguishable:

- deciding why a real HTTP failure occurred; and
- constructing locally valid Generator and Constraint configuration.

It also treated patch syntax checks and generated samples as if they were
evidence that a real API failure had been solved.

## Approved runtime

Operation Smoke now has three sequential phases.

### 1. Investigate failures

`restscope.agent.operation_smoke` investigates at most ten first-seen,
deduplicated failures. One failure is active at a time.

A failure may be immediately `ready` when existing `F*`, `C*`, and `O*`
evidence identifies a root cause, target parameters, and desired changes.
Otherwise the Agent records one testable hypothesis. Only an active hypothesis
enables the current-operation HTTP Request Tool. All calls in one model output
are scope-checked before any call executes.

Probe results are evidence even when they are 4xx, 5xx, or transport failures.
New failure signatures join the FIFO queue and inherit the initial failures
that led to them. A hypothesis may be replaced until an observation supports
confirmation. Confirmation must cite an observation produced after the active
hypothesis and that observation must not reproduce the active failure.

Each failure permits at most twenty valid model outputs. Invalid output does not
consume that budget, but three consecutive invalid outputs defer the failure.
Investigation state, hypotheses, observations, and model messages remain
App-lifetime only.

### 2. Construct isolated Patch Groups

A FAST grouping decision may only partition confirmed `ActionableFailure`
inputs. It cannot add a parameter, change a root cause, or rewrite desired
behavior. Each input belongs to exactly one Group; inputs that require one
same-request Constraint share a Group.

Every Group creates a fresh `ParameterPatchAgent` from
`restscope.agent.parameter_patch`. Agent instances share immutable runtime
dependencies but no messages, proposal, samples, or attempt state.

The package owns:

- the skill-style Generator/Constraint instructions;
- Patch schemas and semantic compilation;
- Group input boundaries;
- pure Generator previews;
- Constraint validation, solving, and provisional compatibility;
- deterministic generation of exactly ten local parameter-value groups; and
- sample-guided self-review by the same FAST Agent.

`restscope.testing` continues to own Generator and Constraint contracts,
solving, and value generation. `object` and `request_body` are system-managed.
Observed generators may select only system-provided `R*` aliases.

The Agent may accept only after it receives ten locally generated samples.
Samples expose request-shaped `values` and explicit `present` flags, including
container values such as arrays. Every complete revision replaces the previous
candidate even if compilation or sampling rejects it. Every model output counts
toward the Group's attempt limit. Provider, database, and reference-provider
infrastructure exceptions remain technical errors.

### 3. Validate real effects

Successful Groups are combined into one candidate. Generator updates create at
most one candidate revision; Constraint-only candidates create no empty
revision. Accepted run-local Constraints are combined with the candidate.

The normal batch runner executes the same operation, case count, and seed once.
The effect validator sees baseline failures and cases, confirmed diagnoses,
candidate cases and responses, and Group provenance. It does not see concrete
Patch schemas or local Patch Agent samples.

Only initial failures are classified as `resolved`, `persisting`, or `unknown`.
A validator decision must classify the complete initial failure set, while
Group acceptance still uses only each Group's provenance mapping. A Group is
accepted atomically when any associated initial failure resolves.
It is rejected when none resolves. Reaching the global success threshold
accepts every successfully constructed Group. Only accepted Generator inputs
are finalized; rejected inputs are compensated out. Accepted Constraints exist
only for the current Smoke run.

Candidate revisions are compensated on technical errors when the catalog is
available. Run startup also recovers any interrupted candidate before it can be
used as a new baseline.

## Model roles

- `operation_smoke_root_cause_diagnosis`: THINK
- `operation_smoke_patch_grouping`: FAST
- `parameter_patch_agent`: FAST
- `operation_smoke_effect_validation`: THINK

## Persistence and privacy

No diagnosis plan, hypothesis, probe evidence, Patch conversation, local
samples, Constraints, or model reasoning is persisted. Local samples and
reference-pool values may appear in the same FAST request and Phoenix trace,
subject to the configured trace redactor. The Patch Agent sends no HTTP request
and writes no catalog state.

## Replaced design

This design supersedes the direct joint-Patch compiler, global planning-output
budget, four-call HTTP cap, persistent PlanState-style multi-failure snapshot,
and Patch-syntax-based effect validation described in
`docs/tasks/operation-smoke-plan-solve.md`.
