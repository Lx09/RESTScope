# Task-focused Main-flow Prompts and Retrieval Removal

Status: Implemented and verified; uncommitted

## Objective

Make each default main-flow LLM call read like a small business task instead
of a serialization of RESTScope's Python models. Keep internal identity,
validation, Generator construction, selectors, and persistence decisions in
trusted code.

## Approved scope

- Give Operation Smoke request-local `P*`, `F*`, `C*`, and `R*` aliases.
- Let the model propose Generator intents and compile them deterministically
  into the existing Generator contracts.
- Give API Behavior identifier and response-source decisions request-local
  `G*`, `I*`, `P*`, and `S*` aliases.
- Use JSON mode plus a minimal example instead of sending Pydantic JSON Schema
  for these calls.
- Keep actual failed-case values as diagnosis evidence, while withholding
  internal IDs, prepared requests, target headers, config revisions, selectors,
  and observed identifier/response-pool values.
- Record only messages as the LLM trace input, request configuration as span
  attributes, and the bounded model reply as trace output.
- Delete the standalone OpenAPI Retrieval Agent, public capability, internal
  investigation tools, role, facades, and active tests without compatibility
  aliases.

## Implementation

- Added private prompt renderers and model-facing DTOs inside the Operation
  Smoke and API Behavior Agent packages. They return the task text and the
  read-only alias maps needed to resolve model output.
- Added a local Generator-intent compiler for exact values, samples, numeric
  ranges, random text, booleans, formats, arrays, variants, and observed-value
  sources. Existing Generator and result models remain the code-side boundary.
- Preserved one semantic repair for Smoke and identifier selection. Repair
  messages contain the previous reply plus plain actionable alias errors, not
  Pydantic locations or schemas. Response-source selection remains fail-closed
  without repair.
- Preserved deterministic API Behavior paths: exact identifier/source matches
  and existing resource rules still avoid LLM calls.
- Changed the DeepSeek adapter to avoid appending a duplicate JSON instruction
  when JSON mode messages already contain one. Existing `json_schema` behavior
  outside this main flow remains unchanged.
- Removed Generator revision data from Operation Smoke spans. Failed input
  values remain visible in prompt and trace.
- Deleted the Retrieval implementation and tests. Historical task records and
  Phoenix traces remain as historical evidence and are marked superseded.

## Compatibility and non-goals

- `RESTScopeApp`, Operation Smoke public results, Generator Catalog behavior,
  HTTP execution, API Behavior persistence, feedback round counts, and LLM call
  timing are unchanged.
- The older Schemathesis dependency analyzer prompt is unchanged.
- No database, migration, transport, redaction, or Phoenix data was changed.
- No live model, target API, or Phoenix contract run is part of this work.

## Verification

All verification was local and offline:

- Focused Smoke, API Behavior, DeepSeek, package-boundary, and App-context
  suites: `87 passed`.
- Focused observability integration suite with the tracing extra: `7 passed`.
- `uv run pytest -q`: `409 passed, 4 skipped`.
- `uv run --extra tracing pytest -q`: `409 passed, 4 skipped`.
- `uv run pytest -q tests/test_schemathesis_mcp_contract.py`: `1 passed`.
- `uv run --extra tracing python -m compileall -q restscope`: passed.
- `git diff --check`, trailing-whitespace checks for new files, and active-tree
  scans for the deleted Retrieval package, tools, role, and public names:
  passed.

No live LLM, target API, Phoenix contract, commit, merge, push, or worktree
cleanup was performed.
