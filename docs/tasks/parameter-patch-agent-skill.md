# Parameter Patch Agent Skill

Status: Standard Skill is an implemented and verified first-class runtime
format; Git delivery remains unauthorized

## Objective

Make the standard directory under `restscope/builtin_skills/` the only source
of truth for built-in RESTScope Skills. Parameter Patch must not need a
domain-specific Python adapter, and its detailed method library must remain
available through genuine progressive disclosure.

This is a RESTScope runtime Skill. It is not installed into Codex personal or
project Skill directories.

## Approved runtime design

- `restscope/builtin_skills/<skill-name>/SKILL.md` is the standard core. Its
  frontmatter contains exactly `name` and `description`.
- Optional `restscope.yaml` contains only `version`, `risk_level`,
  `required_tools`, and `required_context_sources`.
- `restscope.skills.builtin_skill_catalog()` discovers immediate packaged Skill
  directories in stable order and caches their immutable definitions.
- `AgentRuntimeDefinition.skills` contains only additional already-loaded
  caller or test definitions. Such definitions cannot replace a built-in.
- Discovery is not authorization. Each Agent Profile still explicitly selects
  Skill names and grants all required Tools and Context Sources.
- `skill.read` remains the sole automatically appended Tool exception. It adds
  only the selected Skill's core `SKILL.md` body.
- `file.read` is an ordinary explicit Tool grant. Its Harness-owned Binding
  contains only the current Profile's selected Skills and their startup-loaded
  References. Calls query that in-memory map and never resolve filesystem paths.

## Standard Reference boundary

Only Markdown files directly linked from `SKILL.md` with a one-level
`references/<filename>.md` path are registered. Startup rejects missing,
unlinked, duplicate-linked, nested, path-traversing, non-UTF-8, blank, or
over-24,000-character References. A Skill with any References must declare
`file.read` in `restscope.yaml`.

`file.read` returns the complete Markdown once as Tool content. Its structured
result contains only Skill name, Reference path, and character count. Requests
for an unselected Skill fail as `skill_file_not_authorized`; requests for an
unregistered path fail as `skill_file_not_found`; invalid path shapes are denied
by local JSON Schema validation before the Binding runs.

## Parameter Patch library

- `SKILL.md` owns the core trust boundary, authority order, staged Reference
  routing, and minimal complete candidate rule.
- `references/proposal-protocol.md` owns the exact structured proposal and
  correction protocol used by the current specialized Patch Agent.
- `references/generators.md` documents every model-constructible strategy,
  nested presence, containers, variants, reference pools, and minimal edits.
- `references/constraints.md` documents the recursive DSL, normalization,
  evaluation, finite candidate domains, bounded solver, common relationships,
  and transitive replacement scope.
- `references/compiler-and-sampling.md` documents semantic compilation,
  reference revalidation, Generator preview, two-pass constrained generation,
  sample interpretation, and failure classes.
- `references/review.md` documents criterion-by-criterion checks of final
  Generator domains, final relationships, source provenance, and sample
  witnesses. HTTP success or Failure disappearance cannot replace value checks.

The Parameter Patch runtime manifest is version `1.0`, low risk, and requires
`resource.list_resources`, `resource.list_ids`,
`openapi.find_observed_response_fields`, and `file.read`.

## Transitional behavior retained

The current `ParameterPatchAgent` remains a specialized migration exception.
Its deterministic prompt builder reads `proposal-protocol.md` through the
generic built-in Skill Catalog. Its model still receives only the existing
three lookup Tools and never receives `file.read`.

The independent `ParameterPatchReviewAgent`, fresh-context isolation,
deterministic compilation and sampling, shared output accounting, evaluation
system-prompt override, and candidate registration behavior remain unchanged.
A future generic Agent may perform the Skill's self-review only after receiving
the same normalized compiled candidate facts.

## Important implementation findings

- Patch preview proves structural buildability; generated scalar values receive
  the complete frozen-schema validation.
- A mandatory nested edit expands to mandatory ancestors. A changed variant
  descendant also needs exclusive selection at every enclosing variant.
- Constraints use bounded finite-domain search. Constraint literals do not add
  candidates, so the final Generator must expose every required literal.
- Constrained generation builds a baseline, solves overrides, rebuilds the
  request, and rechecks Constraints against the actual generated request.
- Candidate Constraints replace the transitively overlapping old ownership
  scope; Generator-only changes preserve every old Constraint.
- Samples are witnesses, not exhaustive guarantees. Review examines complete
  Generator domains and final relationships separately for every criterion.

## Non-goals

- No external/workspace Skill root, scripts or assets reader, production
  Profile, Patch submission Tool, persistence, database change, new Agent class,
  or Reviewer removal.
- No change to Generator, Constraint, compiler, sampling, or candidate runtime
  behavior.
- No real model, target API, MCP, Phoenix, or other external-service call.
- No commit, merge, push, branch deletion, or worktree cleanup without a new
  explicit authorization.

## Verification plan

```bash
uv run python /Users/lixin/.codex/skills/.system/skill-creator/scripts/quick_validate.py restscope/builtin_skills/parameter-patch
uv run pytest -q tests/test_builtin_skill_loader.py tests/test_parameter_patch_skill.py tests/test_file_read_tool.py tests/test_parameter_patch_agent.py tests/test_agent_prompt_session.py tests/test_agent_runtime.py tests/test_tools_catalog.py tests/test_workflow_package_boundaries.py
uv build
uv run pytest -q
uv run python -m compileall -q restscope tests
git diff --check
```

Verification results:

- The first focused run failed on all newly introduced loader, Catalog,
  Reference, and file Tool contracts, establishing the expected red state.
- The standard Skill validator reported `Skill is valid!`.
- Focused loader, Parameter Patch, Agent/Profile, Plan, Subagent, Tool Catalog,
  and package-boundary verification passed 144 tests.
- `uv build` succeeded. The wheel contains `SKILL.md`, `restscope.yaml`, all
  five References, the generic Skill loader, and the File Tool package.
- Loading the unpacked wheel's built-in Catalog returned `parameter-patch`, its
  2,086-character core body, and all five registered References.
- Full offline verification passed 763 tests with 18 optional/live skips.
- Python compilation and `git diff --check` passed.
- No real model, target API, MCP server, Phoenix service, or other external
  service was called.
- The work remains uncommitted in its feature worktree. No merge, push, branch
  deletion, or worktree cleanup has been performed.
