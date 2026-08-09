# Resolve Operation Failures Standard Skill

Status: Completed — merged into local `main`, verified, and cleaned up on
2026-08-09

## Objective

Record the complete reusable method for resolving all Failures from one API
operation as a packaged standard Skill. Keep semantic grouping, evidence-led
diagnosis, controlled Probes, Worklist decisions, and finish timing with the
parent Agent. Delegate confirmed Parameter Patch construction and self-review
to an authorized Subagent whose Profile selects `build-parameter-patch`.

This is a RESTScope runtime Skill under `restscope/builtin_skills/`, not a Codex
personal or project Skill.

## Approved decisions

- Name the parent method `resolve-operation-failures` because it acts on one
  operation but may group and resolve many Failure sources.
- Rename the existing `parameter-patch` Skill to the verb-led
  `build-parameter-patch`. Do not retain a compatibility alias.
- Give the parent Skill the current OpenAPI, Test Case, Worklist, Parameter
  history, HTTP Probe, file-read, and three Subagent lifecycle dependencies.
- Do not grant the parent `generate_parameter_patch` or
  `parameter_patch.read_candidate` as Skill dependencies.
- Require a child Profile description that explicitly identifies
  `build-parameter-patch`; the child Profile selects that Skill and grants its
  own lookup Tools.
- Treat the generic child's fixed `AgentCompletion` as advice. It is not a
  registered `P*` until a future deterministic bridge parses, compiles,
  samples, semantically reviews, and registers it.
- Keep a missing child, failed child, insufficient evidence, or missing
  candidate bridge as undecided rather than recording a false `no_patch`.

## Skill library

The parent core owns the ordered workflow, untrusted-data boundary, Reference
routing, and parent/child responsibility split. Its six References own:

- evidence authority, Parameter-cause classification, and causal root causes;
- stable reference-only Worklist grouping, merge/split, revision, and coverage;
- precise OpenAPI/Test Case/history Tool selection and controlled HTTP Probes;
- frozen-fact Subagent objectives, direct-child selection, waiting, and
  cancellation;
- value-predicate review and patch/no-patch/undecided decisions;
- final source, candidate, decision, and persistence checks.

The existing `build-parameter-patch` library remains the single source for
Generator and recursive Constraint DSL, reference-backed source selection,
compilation/sampling semantics, complete candidate correction, and semantic
self-review.

## Current compatibility boundary

The specialized `FailureResolutionAgent`, `ParameterPatchAgent`, and
`ParameterPatchReviewAgent` remain temporary migration exceptions. This change
does not alter their Prompts, Tool lists, fresh-context Review, candidate
registry, finalizer, output accounting, or persistence. The specialized Patch
Prompt reads the renamed Skill's unchanged `proposal-protocol.md` through the
generic built-in Catalog.

No production Profile selects either complete Skill. Creating the production
parent/child Profiles and the deterministic Subagent-result-to-candidate bridge
requires a later approved migration.

## Non-goals

- No production Profile, Agent class, Patch submission Tool, Context Source,
  DTO, database record, persistence, or compatibility alias.
- No removal or bypass of the current specialized Agents or Reviewer.
- No change to Generator, Constraint, compiler, sampling, HTTP, Worklist,
  finalization, or persistence behavior.
- No real model, target API, MCP, Phoenix, or other external-service call.
- No commit, merge, push, branch deletion, or worktree cleanup without new
  explicit authorization.

## Verification plan

```bash
uv run python /Users/lixin/.codex/skills/.system/skill-creator/scripts/quick_validate.py restscope/builtin_skills/build-parameter-patch
uv run python /Users/lixin/.codex/skills/.system/skill-creator/scripts/quick_validate.py restscope/builtin_skills/resolve-operation-failures
uv run pytest -q tests/test_builtin_skill_loader.py tests/test_parameter_patch_skill.py tests/test_resolve_operation_failures_skill.py tests/test_file_read_tool.py tests/test_parameter_patch_agent.py tests/test_agent_runtime.py tests/test_agent_prompt_session.py tests/test_subagent_runtime.py tests/test_tools_catalog.py tests/test_workflow_package_boundaries.py
uv build
uv run pytest -q
uv run python -m compileall -q restscope tests
git diff --check
```

Verification results:

- Both standard Skill validators reported `Skill is valid!`.
- Focused Skill loader, Build Patch, Resolution Skill, file-read, specialized
  Patch Agent, Agent/Profile, Prompt Session, Subagent, Tool Catalog, and
  package-boundary verification passed 160 tests.
- `uv build` succeeded. The wheel contains the Build Patch core, manifest, and
  five References plus the Resolution core, manifest, and six References.
- The complete offline suite passed 784 tests with 18 optional or live tests
  skipped.
- Python compilation, tracked-diff whitespace checking, and an explicit
  trailing-whitespace scan across the new untracked files passed.
- No real model, target API, MCP server, Phoenix service, or other external
  service was called.
- Fresh pre-delivery verification on 2026-08-09 reproduced all original
  worktree results above.
- Fresh post-merge verification on local `main` passed both Skill validators,
  165 focused tests, the package build, 815 full-suite tests with 3 skips,
  Python compilation, and `git diff --check`.
- The clean feature worktree and its merged branch were removed after Git
  confirmed the feature commit was an ancestor of `main`.
- No push or external-service call was performed.
