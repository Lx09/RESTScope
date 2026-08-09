# Main Agent Conversation Observer UI

Status: Implementation, automated verification, and desktop browser confirmation complete; exact 375 px confirmation pending

## Objective

Replace the read-only G6 canvas with a full-width Codex-style document
conversation for the explicit generic Main Agent. Preserve already-redacted raw
Provider Reasoning, keep operational calls inspectable without mixing every run
notification into prose, and expose the Main Agent's generic Plan as a floating
Todo.

## Final approved behavior

- Keep schema-v2 snapshots, cursor-addressed SSE, loopback-only GET routes,
  fail-open observation, and no backend writes.
- Select only `lifecycle=main`; never promote transitional legacy Agents.
- Remove the profile, `Main Agent`, and `线性会话` visual headings. The
  conversation occupies the complete page content width.
- Render incremental System, Developer, User, and ordinary Assistant message
  content as unlabelled document prose. Do not show `User Task`, `Commentary
  Update`, or `Final Answer` annotations.
- Render raw Provider Reasoning immediately before the corresponding response,
  expanded by default, muted and synthetic-oblique for Chinese text. It has no
  icon, heading, chevron, or copy action; clicking the content toggles it through
  one ellipsis.
- Exclude Assistant Tool Call messages and Tool Result messages from prose.
  Show ordinary Tools as collapsed no-chevron rows. Aggregate Subagent lifecycle
  calls by child session, display the child Profile name rather than
  `subagent.start`, and open the child conversation in a right Drawer with up to
  three breadcrumb levels.
- Exclude Smoke Batch and unrelated run notifications from the conversation.
- Align compact icons, Reasoning, and prose to the same left edge.
- Replace Resolution Worklist floating state with Todo. Only successful
  `plan.update` from the explicit Main Agent updates Todo; Resolution Worklist
  calls remain ordinary Tool details. Do not repeat the active step in a
  separate “当前” summary because its row already says “进行中”.
- Keep the latest five complete already-redacted schema-v2 snapshots in
  same-origin IndexedDB. Historical Todo is frozen and explicitly read only.
- Remove G6, use dynamic-height virtualization, and keep the build deterministic.

## Persistence and security boundary

Raw Reasoning and Todo are human observability evidence. They receive the
existing exact-value redaction and may enter browser IndexedDB. They never enter
SQLite, Phoenix output, Agent context, general memory, recovery input, or a
backend persistence API. Clearing browser site data deletes the history.

## Verification results

- Frontend: 9 Vitest files / 44 tests passed; ESLint passed; Ant Design v6 lint
  scanned 29 files with no issues; TypeScript/Vite production build passed.
- Backend: focused Observer/UI service checks passed 20 tests; complete suite
  passed 792 tests with 6 skips.
- The built UI is versioned under `restscope/ui/static/` and active source has
  no G6 dependency or canvas implementation.
- Browser control could not trigger the loopback reload, but the user refreshed
  the page and the built asset then passed desktop inspection. The page
  had no horizontal overflow; prose, Reasoning, and Tool rows shared one left
  edge; Todo and the named Subagent Drawer behaved as designed. The browser
  environment rescaled a requested 375 px viewport to an effective 169 px, so
  the exact 375 px visual check remains pending even though that narrower view
  also had no horizontal overflow.

## Git boundary

The scoped implementation remains unstaged and uncommitted in the dedicated
`codex/conversation-observer-ui` worktree. Commit, merge, push, branch deletion,
and worktree cleanup require separate explicit authorization.
