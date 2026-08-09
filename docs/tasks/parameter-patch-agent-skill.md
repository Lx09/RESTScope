# Parameter Patch Agent Skill

Status: Implemented and verified; local Git delivery authorized

## Objective

Move the reusable Parameter Patch proposal method into one project-native
`SkillDefinition` while the current specialized Patch and Review Agents remain
the production execution path.

## User-approved scope

- Add and publicly export a `parameter-patch` Skill with version `1.0`.
- Require only the three existing read-only Resource and observed-response
  lookup Tools.
- Keep the current proposal-only Agent on the proposal portion of the Skill so
  the detailed method has one maintained source.
- Include the future unified Agent's semantic self-review method in the full
  Skill body without injecting that section into the current Patch Agent.
- Preserve the independent fresh-context Reviewer, deterministic compilation
  and sampling, shared output limit, and candidate Registry behavior.

## Non-goals

- Do not add a production Agent Profile, Patch submission Tool, persistence,
  script, database change, or new Agent class.
- Do not remove, bypass, or alter `ParameterPatchReviewAgent`.
- Do not call a real model, target API, MCP server, or Phoenix service.
- Do not create Codex `SKILL.md` or `agents/openai.yaml` files.
- Do not commit, merge, push, or remove the feature worktree without separate
  authorization.

## Decisions and assumptions

- RESTScope's `SkillDefinition`, `AgentProfile`, and Harness-owned `skill.read`
  contracts are authoritative; Codex's filesystem Skill format does not apply.
- The full Skill teaches proposal construction plus future self-review. The
  exported proposal-only instruction segment remains the temporary specialized
  Agent's exact system guidance.
- Compiler, sampling, and semantic-review feedback correct the current
  proposal; they never justify escalation to a different value source.
- The future generic Agent migration must add an independently approved Patch
  submission Tool or equivalent current consumer before selecting this Skill
  in production.

## Verification

Planned local verification:

```bash
uv run pytest -q tests/test_parameter_patch_skill.py tests/test_parameter_patch_agent.py tests/test_agent_prompt_session.py tests/test_agent_runtime.py tests/test_tools_catalog.py tests/test_workflow_package_boundaries.py
uv run pytest -q
uv run python -m compileall -q restscope tests
git diff --check
```

Initial TDD check:

- `uv run pytest -q tests/test_parameter_patch_skill.py
  tests/test_parameter_patch_agent.py -x` failed at the first new import, as
  expected, because the project Skill exports did not exist yet.
- The first implemented proposal segment was 7,168 characters, exceeding the
  existing Patch Agent's 7,000-character system limit. Repeated wording was
  compressed instead of widening the runtime budget.
- One new override test initially inherited the preceding prompt-line-length
  assertion because it was inserted at the wrong boundary. The assertion was
  restored to its original readable-card scenario; production code was not
  changed for that test mistake.
- Existing Patch contracts also require exact DSL and evidence-safety wording.
  The Skill retains those phrases. Its former 5,000-character prompt target is
  replaced only by the already-enforced 7,000-character system boundary so the
  approved detailed method can remain the current proposal source.

Focused verification after implementation:

- `uv run pytest -q tests/test_parameter_patch_skill.py
  tests/test_parameter_patch_agent.py tests/test_agent_prompt_session.py
  tests/test_agent_runtime.py tests/test_tools_catalog.py
  tests/test_workflow_package_boundaries.py` passed `111` tests.
- The proposal-only segment is 6,825 characters and the complete Skill body is
  8,465 characters, within their 7,000- and 24,000-character contracts.
- `uv run pytest -q` passed `739` tests with `18` skips.
- `uv run python -m compileall -q restscope tests` passed.
- `git diff --check` passed.
- Before local Git delivery, no real model, target API, MCP server, Phoenix
  service, commit, merge, push, branch deletion, or worktree cleanup was
  performed. The user later authorized commit, merge into local `main`, and
  cleanup; push remains unauthorized.
