# Exploratory Agent Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unusable rule hierarchy with a small, internally consistent governance system for conservative collaboration on an exploratory project.

**Architecture:** Keep `AGENTS.md` as the mandatory entrypoint and route detailed guidance into four focused files under `docs/agent-rules/`. The entrypoint defines project state and universal gates; the rule files separately own authority, workflow, code verification, and Git safety.

**Tech Stack:** Markdown, Git, `rg`, shell verification, existing Python/pytest environment.

---

### Task 1: Replace the root entrypoint

**Files:**
- Modify: `AGENTS.md`
- Reference: `docs/superpowers/specs/2026-07-14-agents-governance-design.md`

- [x] **Step 1: Replace stale assumptions with the exploratory-project context**

The new opening must state that RESTScope is exploratory, its final product
boundary and overall architecture are unsettled, and existing design documents
are evidence rather than automatic final authority.

- [x] **Step 2: Define the minimum operating rules**

Include all of these requirements:

```text
- inspect Git status and preserve unrelated changes;
- inspect relevant code, tests, and documentation before proposing work;
- distinguish facts, hypotheses, proposals, and approved decisions;
- request approval before architecture-affecting implementation;
- keep approved implementation inside its confirmed scope;
- maintain task records only for multi-step or cross-session work;
- run fresh, risk-proportional verification;
- request explicit permission before creating a commit;
- never infer push, merge, PR, destructive Git, or live-target authority.
```

- [x] **Step 3: Route agents to the four detailed rules**

Reference exactly these files and explain when each applies:

```text
docs/agent-rules/source-and-decisions.md
docs/agent-rules/exploration-workflow.md
docs/agent-rules/code-and-verification.md
docs/agent-rules/git-and-worktrees.md
```

- [x] **Step 4: Remove every mandatory reference to nonexistent governance files**

The resulting entrypoint must not require `PRD.md`, `ARCHITECTURE_SPEC.md`, an
active version plan, roadmap, release audit, or any rule file not created by
this plan.

### Task 2: Define evidence, decisions, and approval flow

**Files:**
- Create: `docs/agent-rules/source-and-decisions.md`
- Create: `docs/agent-rules/exploration-workflow.md`

- [x] **Step 1: Write the authority order**

`source-and-decisions.md` must use this order:

```text
1. Current explicit user instructions.
2. User-approved repository decisions, scopes, and plans.
3. Executable evidence from tests, code, schemas, and migrations.
4. Current project and module documentation.
5. Clearly labeled working assumptions.
```

It must require agents to expose conflicts and define fact, hypothesis,
proposal, and decision.

- [x] **Step 2: Write the autonomous investigation boundary**

`exploration-workflow.md` must allow read-only inspection, local
non-destructive diagnostics, evidence summaries, alternatives, and small
implementations whose exact scope is already approved.

- [x] **Step 3: Write the approval gates**

Require renewed user approval before introducing a product capability, module,
long-term abstraction, public API change, database boundary, lasting dependency
choice, broad refactor, compatibility break, major scope expansion, or live
external action.

- [x] **Step 4: Define proportionate task records**

Require `docs/tasks/` records only for approved work that is multi-step, spans
sessions, or crosses architectural areas. Require objective, approved scope,
non-goals, status, decisions, verification, and remaining risks. Do not require
task records for read-only investigations or small localized edits.

### Task 3: Define implementation quality and Git safety

**Files:**
- Create: `docs/agent-rules/code-and-verification.md`
- Create: `docs/agent-rules/git-and-worktrees.md`

- [x] **Step 1: Write exploratory implementation principles**

Require the smallest change that answers the current question, reuse where it
fits, evidence before new abstractions, focused Python modules, explicit public
interfaces, useful type hints/docstrings, reasoning-focused comments, and no
unrelated cleanup.

- [x] **Step 2: Write fresh verification rules**

Require focused tests for localized changes and focused plus full-suite tests
for cross-cutting work when practical. Require actual commands and outcomes,
and prohibit treating old task records as evidence about the current tree.

- [x] **Step 3: Write working-tree preservation rules**

Require status inspection before editing. Prohibit discarding, overwriting,
staging, or reformatting unrelated changes. Prefer a separate worktree for
risky, experimental, overlapping, or parallel work without requiring one for
every small edit.

- [x] **Step 4: Write explicit Git authorization boundaries**

Allow editing and verification after scope approval, but require explicit user
authorization for each commit. State that commit permission does not authorize
push, PR, merge, history rewriting, branch/worktree deletion, or destructive
Git commands.

### Task 4: Verify the governance system

**Files:**
- Verify: `AGENTS.md`
- Verify: `docs/agent-rules/source-and-decisions.md`
- Verify: `docs/agent-rules/exploration-workflow.md`
- Verify: `docs/agent-rules/code-and-verification.md`
- Verify: `docs/agent-rules/git-and-worktrees.md`

- [x] **Step 1: Verify every required rule file exists**

Run:

```bash
for file in \
  docs/agent-rules/source-and-decisions.md \
  docs/agent-rules/exploration-workflow.md \
  docs/agent-rules/code-and-verification.md \
  docs/agent-rules/git-and-worktrees.md; do
  test -f "$file" || exit 1
done
```

Expected: exit code `0` with no output.

- [x] **Step 2: Verify all routed rule paths resolve**

Run:

```bash
rg -o 'docs/agent-rules/[a-z-]+\.md' AGENTS.md \
  | sort -u \
  | while read -r file; do test -f "$file" || exit 1; done
```

Expected: exit code `0` with no output.

- [x] **Step 3: Verify stale mandatory governance references are gone**

Run:

```bash
if rg -n 'PRD\.md|ARCHITECTURE_SPEC\.md|active version plan|release-audit|roadmap-authoring' AGENTS.md docs/agent-rules; then
  exit 1
fi
```

Expected: exit code `0` with no matches.

- [x] **Step 4: Verify Markdown whitespace and the existing test suite**

Run:

```bash
git diff --check
uv run pytest -q
```

Expected: `git diff --check` exits `0`; pytest reports no failures.

- [x] **Step 5: Inspect the final scoped diff**

Run:

```bash
git status --short
git diff -- AGENTS.md docs/agent-rules
```

Expected: the governance diff is limited to the root entrypoint and the four
rule files; pre-existing working-tree changes remain present and untouched.

### Task 5: Preserve the result only after authorization

**Files:**
- Stage only after explicit user authorization: `AGENTS.md`
- Stage only after explicit user authorization: `docs/agent-rules/*.md`
- Stage only after explicit user authorization: `docs/superpowers/specs/2026-07-14-agents-governance-design.md`
- Stage only after explicit user authorization: `docs/superpowers/plans/2026-07-14-agents-governance.md`

- [x] **Step 1: Report verified results and request commit authorization**

Do not stage or commit as part of implementation. Report the exact files and
fresh verification output, then wait for the user to explicitly authorize a
checkpoint commit.
