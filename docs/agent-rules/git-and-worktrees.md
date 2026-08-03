# Git and Worktree Rules

## Protect the current tree

Run `git status --short --branch` before editing. Treat every pre-existing
modification and untracked file as user work unless there is direct evidence
that it belongs to the current task.

- Do not discard, overwrite, stage, move, reformat, or include unrelated work.
- Inspect overlapping diffs before editing a file that is already modified.
- Keep generated files, local configuration, credentials, and secrets out of a
  checkpoint unless the user explicitly requests and reviews them.
- After implementation, inspect status again and report both task changes and
  preserved pre-existing changes.

Never use `git reset --hard`, destructive checkout/restore commands, history
rewrites, forced pushes, or broad clean commands unless the user explicitly
requests the exact operation after reviewing what will be affected.

## When to use a worktree

Every new feature must be built on its own branch in a dedicated Git worktree.
Use this lifecycle:

1. inspect the current worktrees and Git status;
2. create a feature branch and dedicated worktree from the current local
   `main`, without fetching, pulling, or otherwise changing `main` unless that
   action is separately authorized;
3. implement and verify the feature in that worktree;
4. obtain explicit authorization for the required commit, merge, and cleanup
   actions;
5. commit the scoped feature changes and merge the feature branch into local
   `main`;
6. run proportional verification on the merged `main` tree;
7. remove the feature worktree and delete the merged feature branch.

Once implementation and tests are complete and the user has explicitly
authorized the required Git operations, finish steps 5 through 7 as one
continuous delivery lifecycle. Do not stop after the feature commit or merge,
and do not leave a successfully merged and verified feature worktree or branch
behind. If the merge or merged-`main` verification fails, preserve the feature
worktree for diagnosis and report the failure; cleanup happens only after both
the merge and merged-result verification succeed.

This mandatory lifecycle applies to new features. Bug fixes, documentation
changes, and maintenance work may still use the risk-based guidance below.

Prefer a separate Git worktree when work is:

- risky or strongly experimental;
- likely to overlap current uncommitted changes;
- large enough to need an isolated branch or review cycle;
- intended to proceed in parallel with other work.

A worktree is not mandatory for a small, localized, approved edit when the user
has authorized work in the current tree and the changed files do not
meaningfully overlap unrelated changes. This exception never applies to a new
feature.

Before creating a worktree, inspect existing worktrees and choose a branch and
path that will not collide. Do not remove a worktree or branch without explicit
authorization.

## Authorization boundaries

Permission to edit files does not grant permission to stage or commit them.
Create a commit only after receiving explicit user authorization for that
commit. Before committing:

1. run the agreed verification;
2. show or summarize the exact scoped diff;
3. stage only the approved files;
4. verify the staged diff contains no unrelated or sensitive content;
5. use a concise message that describes the preserved change.

Each of these actions requires explicit scope and is not implied by commit
permission:

- pushing a branch;
- creating or updating a pull request;
- merging or rebasing;
- rewriting or squashing history;
- deleting a branch or worktree;
- modifying tags or releases.

When authorization is absent, leave the verified changes unstaged and report
their exact status.
