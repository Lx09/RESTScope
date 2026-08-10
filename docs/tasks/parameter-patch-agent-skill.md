# Apply Parameter Patch Standard Skill

Status: Implemented; not selected by a production Profile

## Outcome

`restscope/builtin_skills/apply-parameter-patch/` is the sole standard Skill for
building, validating, reviewing, and applying a Parameter Patch. It replaces
the retired names `parameter-patch` and `build-parameter-patch` without
compatibility aliases.

The Skill changes only RESTScope's App-lifetime request-generation state. It
does not send a target request, prove that a Failure is resolved, persist Patch
history, or create a candidate reference.

## Runtime contract

The Skill requires exactly these explicit ordinary Tool grants:

- `file.read`
- `resource.list_resources`
- `resource.list_ids`
- `openapi.find_observed_response_fields`
- `request_generation.get_input_state`
- `request_generation.validate_patch`
- `parameter_patch.apply`

Its Profile must select the Skill and grant all seven Tools. `skill.read` is the
only automatic loader exception; selecting the Skill does not authorize any
domain behavior.

## Method

The Skill fixes the parent-confirmed operation, root cause, atomic value
predicates, and minimum complete affected-input scope. It then:

1. reads the current revision, full Generators, and intersecting Constraint
   closure;
2. constructs a complete semantic replacement using the least
   target-dependent Generator supported by evidence;
3. validates scope, schema, references, Constraints, and deterministic samples;
4. reviews each predicate against the complete final value domain, presence,
   variants, references, Constraint closure, and sample witnesses;
5. applies only the exact validated request and digest;
6. reads state again to confirm the revision, digest, Generators, and
   Constraints, exact reference bindings, and intentional response-pool
   removals.

Compilation success and finite samples are necessary checks, not semantic
proof. HTTP success and Failure disappearance are also not substitutes for
value-level review. A stale revision requires a fresh read and validation.

## Standard Reference boundary

`SKILL.md` links six first-level Markdown References:

- `generators.md`
- `constraints.md`
- `compiler-and-sampling.md`
- `proposal-protocol.md`
- `review.md`
- `application.md`

The built-in loader validates and stores these files at startup. `file.read`
can return only a linked Reference of a Skill selected by that same Profile. It
cannot read `SKILL.md`, another Skill, source code, assets, absolute paths, or
path traversal.

## Retired compatibility paths

There is no dedicated `ParameterPatchAgent`, Patch Reviewer, proposal-only
Python adapter, `generate_parameter_patch`, `parameter_patch.read_candidate`,
candidate `P*` registry, Finalizer, or database-backed Generator state. The
generic Subagent using this Skill is responsible for the complete bounded
workflow, while deterministic Tools own compilation, sampling, locking,
exact response-pool replacement, and state mutation.

## Verification

Offline tests cover standard Skill discovery and progressive disclosure,
semantic state closure, deterministic validation and digests, no-op and stale
rejection, zero mutation on failure, concurrent apply conflicts, atomic
reference replacement, rollback after durable commit failure, and post-apply
state confirmation. No real model,
target API, MCP, or Phoenix service is used.
