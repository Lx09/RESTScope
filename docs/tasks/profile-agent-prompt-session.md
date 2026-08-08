# Profile Agent Prompt Session

Status: Implementation refined and verified; local Git delivery is authorized

## Objective

Give every generic Main Agent and Subagent started from an `AgentProfile` one
private, in-memory prompt session. The Module owns stable role assembly,
on-demand Skill instruction loading, bounded changing Context, Tool and output
protocol reservation, and compaction requests. The generic `Agent` remains the
model-and-Tool execution loop, while `AgentContext` remains the message-history
implementation inside the new Module.

## User-approved scope

- Add an optional 1–2000 character Profile description and require a
  description for any Profile named as a child.
- Add the `developer` LLM role and preserve stable system/developer messages
  across compaction.
- Add a Harness-owned `skill.read` Tool which is automatically available only
  when the current Profile selects Skills. Selected Skill names authorize only
  their own instruction bodies.
- Build system, developer, user, Tool, and output-schema request parts in one
  private Prompt Session per Agent.
- Track Context Source fingerprints only for that session and re-anchor all
  current sources after compaction.
- Protect the 24,000-character stable prefix according to the approved name and
  description priorities, and reserve immutable Tool/output schemas in the
  model input budget.
- Preserve OpenAI-compatible developer messages and fold them into DeepSeek's
  system content in order.

## Non-goals

- No changes to Failure Resolution, Parameter Patch, Patch Review, or Compact
  Agent prompt assembly.
- No Observer, SSE, frontend, database, Plan, Failure Worklist, real model, MCP,
  target HTTP, WorldState, field-level diff, dynamic Tool Router, or persistent
  Agent state changes.
- No public Prompt DTO, Prompt Registry, or `restscope.agent` facade export.

## Decisions and assumptions

- `skill_names` is the explicit narrow authorization for the Harness loader and
  corresponding Skill bodies. Ordinary executable Tools still require exact
  names in `tool_names`.
- Skill requirements are validated at startup even if the model never reads
  their instruction body.
- Child prompt state is independent; a parent sees only each direct child's
  name and description.
- Context Source adapters return safely rendered, bounded Markdown. The Harness
  redacts and validates it; the Prompt Session adds a controlled untrusted-data
  envelope without re-encoding the Markdown.
- The Harness is the sole owner of source type, redaction, and length checks.
  Prompt Session readers therefore expose only a name and already-validated
  bounded Markdown, without repeating source limits at the prompt seam.
- Fixed model configuration belongs to Prompt Session request assembly and is
  not duplicated on the generic Agent execution loop.
- The approved public TDD seams are `HarnessRuntime.start_main_agent`,
  `AgentContext`, and provider request conversion. The private Prompt Session is
  exercised through those Interfaces.

## Verification record

Fresh local verification used no real model, MCP server, target API, or browser:

- Focused Agent/Profile/Context/Subagent/Skill/Tool/provider/package-boundary
  command (`uv run pytest -q tests/test_agent_profile.py
  tests/test_agent_context.py tests/test_agent_prompt_session.py
  tests/test_agent_runtime.py tests/test_subagent_runtime.py
  tests/test_agent_plan.py tests/test_tools_catalog.py tests/test_llm_mvp.py
  tests/test_llm_deepseek.py tests/test_workflow_package_boundaries.py`):
  146 passed.
- `uv run pytest -q`: 735 passed, 18 skipped.
- `python3 -m compileall -q restscope tests`: passed.
- Lock-file status: no lock file changed.
- `git diff --check`: passed.

## Git state

Implementation uses branch `codex/profile-agent-prompt-session` in the
dedicated worktree
`/Users/lixin/Workplace/RESTScope-profile-agent-prompt-session`. Local commit,
merge, and cleanup are authorized; push remains unauthorized.
