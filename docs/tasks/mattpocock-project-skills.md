# Matt Pocock Project Skills

Status: Completed

## Objective

Install Matt Pocock's stable Engineering and Productivity skills as
RESTScope-specific Codex workflows without making them available globally.

## Approved scope

- Install the 17 stable Engineering skills and 5 stable Productivity skills
  from `mattpocock/skills`.
- Pin the installed source to commit
  `ed37663cc5fbef691ddfecd080dff42f7e7e350d`.
- Store the skills under `.agents/skills/` so Codex discovers them only while
  working in this repository.
- Track the skill files on the current local `main` branch.
- Remove the previously installed global Superpowers plugin, skills, and stale
  cache only after the project-scoped replacement passes verification.

## Priority and safety constraints

- RESTScope's root `AGENTS.md` and its linked rules remain authoritative over
  every installed third-party skill.
- A skill cannot grant itself permission to commit, push, merge, delete Git
  state, mutate an external service, create a lasting architecture, or bypass
  the project's review limits.
- Skills that request multiple subagents must still follow the repository's
  one-review limit and the user's explicit delegation choices.
- Installing a skill does not run it. In particular,
  `setup-matt-pocock-skills` must not modify `AGENTS.md`, create domain
  documents, or configure an issue tracker as part of this task.

## Non-goals

- Do not install deprecated, in-progress, miscellaneous, or personal skills.
- Do not install Matt Pocock skills into a global user directory.
- Do not modify RESTScope production code, tests, application dependencies, or
  runtime behavior.
- Do not push the resulting commit or otherwise change remote Git state.

## Verification

- The project directory contains exactly 22 selected skill directories. Every
  directory has a `SKILL.md`, and every declared skill name matches its
  directory name.
- The installed Engineering-to-Productivity references have matching project
  skills, and the installed tree contains no symbolic links.
- None of the 22 Matt Pocock skills exists in either
  `/Users/lixin/.codex/skills/` or `/Users/lixin/.agents/skills/`.
- `git diff --check` passed before staging. The staged form of this check and
  exact-path inspection are run immediately before the authorized commit.
- Codex plugin management reported Superpowers was not registered as an
  installed plugin. The 13 confirmed global Superpowers skill directories and
  the stale `/Users/lixin/.codex/.tmp/plugins/plugins/superpowers` cache were
  deleted.
- No RESTScope application tests were run because this task changes only
  project-scoped Agent workflow files and its task record.
