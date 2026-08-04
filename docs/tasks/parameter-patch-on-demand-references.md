# Parameter Patch On-Demand References

## Status

Implemented and verified in `codex/patch-on-demand-references`; waiting for
separate Git commit, merge, and cleanup authorization.

## Objective

Let the Parameter Patch Agent discover resource identifiers and observed
response fields through three bounded read-only tools, then submit direct
Generator strategies without preloaded `R*` aliases.

## Approved behavior

- Only Parameter Patch receives `resource.list_resources`,
  `resource.list_ids`, and `openapi.find_observed_response_fields`.
- A resource Generator names a canonical resource returned with non-empty,
  type-compatible IDs during the current Patch session.
- A model-facing response-value Generator names one observed producer field;
  deterministic runtime validates and samples it, then creates the internal
  value pool only if Failure Solve applies the candidate.
- Generator selection follows the approved resource → generative strategy →
  evidence-backed choice → observed response escalation policy across Smoke
  rounds. Constraints express cross-input relationships only.
- Existing Failure Solve evidence gates remain authoritative.
- Observed-field lookup starts with the consumer leaf and property path. When
  those exact names are empty, the model may try a small evidence-informed
  synonym set (for example `commit_id` → `sha` or `hash`). A guessed synonym is
  only a query; only an exact identity returned by the tool may enter a Patch.

## Implemented design

- Patch Proposal now owns a bounded tool-or-final-JSON conversation. Lookup
  turns and final/revision turns share the existing output budget; independent
  reads may run together, while Resource ID lookup requires a prior canonical
  resource listing.
- The Patch session retains only successful canonical resource/type summaries
  and complete response-field identities. Compilation then re-reads current
  resource pools or current observed response evidence and rejects missing,
  modified, empty, non-scalar, or incompatible sources.
- Response candidates receive a short-lived preview-value overlay for local
  samples. The internal `response_<digest>` name is absent from Patch, Review,
  and Solve-readable text. Apply revalidates the current IR and values before
  registering the selected producer field for the consumer input.
- The old `reference_options`, `R*`, `reference`, and eager cross-product
  enumeration paths were removed. Internal selected-source provenance has no
  alias or option ID.
- Failure Solve and every other Agent keep their existing tool sets; only the
  nested Patch Proposal Agent receives the three lookup tools.

## Non-goals

- No lookup-tool output change, database migration, configuration field,
  compatibility alias, live provider call, HTTP probe, or GitLab request.
- No tools are added to Failure Solve, Patch Review, Dedup, or another Agent.
- No commit, merge, cleanup, or push without separate authorization.

## Verification

- Focused Parameter Patch, Failure Solve, reference, response-value, App
  bootstrap, evaluation, and package-boundary tests: `190 passed`.
- Complete evaluation-enabled suite:
  `uv run --group evaluation pytest -q` → `679 passed, 5 skipped`.
- `uv run python -m compileall -q restscope tests evaluations` passed.
- `git diff --check` passed.
- No real LLM, HTTP Probe, or GitLab live request was run.
