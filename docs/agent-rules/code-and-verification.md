# Code and Verification Rules

## Exploratory implementation

Choose the smallest change that can answer the current question or validate the
approved hypothesis. Reuse existing code and project boundaries when they fit,
but do not preserve an unsuitable abstraction merely because it exists.
Challenge it with evidence and obtain approval before replacing it with a
lasting design.

Avoid:

- speculative abstractions without a current consumer;
- compatibility layers for unconfirmed future requirements;
- unrelated refactoring, formatting, or dependency updates;
- silent changes to public behavior;
- making tests pass by weakening meaningful assertions.

## Python readability

For meaningful Python changes:

- keep modules and functions focused on one clear responsibility;
- make public interfaces and dependency boundaries explicit;
- use type hints for contracts and non-obvious structures;
- use concise module, class, and function docstrings where they clarify purpose
  or constraints;
- comment non-obvious reasoning, safety boundaries, and design constraints
  rather than narrating individual statements;
- keep names concrete and consistent with the domain language already used;
- split a file when the approved change would otherwise mix distinct
  responsibilities.

Do not add headers, docstrings, or comments mechanically. They must improve a
reader's ability to understand or safely change the code.

## Verification

Run verification after the final edit, not just before it:

- localized behavior change: focused tests covering the changed contract;
- cross-cutting change: focused tests plus the full suite when practical;
- documentation or governance change: link/path checks, content checks, and
  `git diff --check`;
- import or packaging change: relevant import/build smoke checks;
- Agent/Profile change: verify launch-time name resolution, exact provider Tool
  payloads, Context/Skill exclusion, child ownership, and unchanged state after
  rejected model or Tool input;
- Tool contract change: validate both JSON Schemas, local argument rejection,
  complete Pydantic cross-field rejection, successful output validation, and a
  correctable retry in the same Agent session;
- external integration: local contract tests where possible and a clear report
  of any live behavior that was not exercised.

The normal full-suite command is:

```bash
uv run pytest -q
```

Report the exact command and observed result. Do not infer full-suite success
from a focused test, use an old task record as current evidence, or claim an
external integration works when only a fake or mock was exercised.

## Completion standard

A change is complete only when:

- the approved scope is implemented without known unrelated changes;
- relevant verification has freshly passed;
- documentation and task status reflect actual behavior;
- unresolved risks and unverified behavior are reported;
- preservation requirements, including any user-authorized Git checkpoint,
  have been satisfied.

If commit authorization has not been given, report the implementation as
verified but uncommitted rather than treating preservation as complete.
