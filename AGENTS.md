# AGENTS.md

RESTScope uses a thin entry file plus detailed rules under `docs/agent-rules/`.

For any non-trivial task, read these in order:

1. `PRD.md`
2. `ARCHITECTURE_SPEC.md`
3. the active version plan, if one exists
4. `docs/agent-rules/source-of-truth.md`
5. `docs/agent-rules/project-architecture-focus.md`
6. the most relevant process files under `docs/agent-rules/`

Minimum operating rules:

- changes must follow `PRD.md` and `ARCHITECTURE_SPEC.md`
- active execution work must follow the active version plan when one exists
- long-run development must follow the repository lifecycle rules
- changes must maintain persistent task files in `docs/tasks/`
- changes must be preserved with git checkpoints
- risky or parallel work should use `git worktree`
- prefer reuse over reinvention
- when modifying meaningful Python files, follow the dedicated readability and comment rules in `docs/agent-rules/code-comments-and-file-headers.md`
- long-run development must use one bounded active version plan at a time
- version plans, task files, audit documents, and roadmap documents must follow the repository authoring rules

Rule files:

- `docs/agent-rules/source-of-truth.md`: authority order and assumption handling
- `docs/agent-rules/project-architecture-focus.md`: graph-centered and closed-loop guardrails
- `docs/agent-rules/workflow.md`: bounded task execution flow and readability pass
- `docs/agent-rules/code-comments-and-file-headers.md`: Python file headers, docstrings, and concise comment rules
- `docs/agent-rules/checklists.md`: task-file persistence and update rules
- `docs/agent-rules/git-and-worktree.md`: commit and worktree rules
- `docs/agent-rules/reuse-policy.md`: reuse-first policy
- `docs/agent-rules/long-run-lifecycle.md`: version-level long-run lifecycle
- `docs/agent-rules/milestone-execution.md`: milestone and task execution rules inside the active version plan
- `docs/agent-rules/release-audit-and-next-cycle.md`: transition rules from execution into audit, roadmap, and the next version plan
- `docs/agent-rules/plan-files.md`: how version plans and `ROADMAP.md` relate and how version plans must be written
- `docs/agent-rules/task-authoring.md`: how `docs/tasks/*.md` must be structured and maintained
- `docs/agent-rules/audit-and-release-docs.md`: how audit, release, status, limitations, runbook, and validation documents must be written
- `docs/agent-rules/roadmap-authoring.md`: how `ROADMAP.md` must be written and how it relates to the next version plan
