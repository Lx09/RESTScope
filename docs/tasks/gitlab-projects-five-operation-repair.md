# GitLab Projects Five-Operation Repair

Status: Blocked

## Objective

Make the five GitLab Projects collection/item operations reach real Batch
Testing without unsupported contracts, technical errors, or Agent budget
exhaustion. The live acceptance covers GET, POST, GET-by-id, PUT, and DELETE
under `/api/v4/projects`.

## Approved scope

- Give `ParameterPatchAgent` the complete `ParameterPatchDecision` JSON Schema.
- Clarify the top-level propose/accept and response-reference shapes in compact
  Markdown prompt and correction feedback.
- Stop one Patch session after three semantically identical invalid structures.
- Support deterministic `multipart/form-data` object bodies containing ordinary
  scalar, array, and object fields.
- Exclude optional binary/byte inputs and continue rejecting required files,
  explicit multipart encoding, and non-object multipart schemas.
- Retain an opt-in five-operation GitLab/DeepSeek/Phoenix acceptance test with a
  caller-enforced ten-minute deadline.

## Non-goals

- File upload, multipart encoding rules, or a public request DTO change.
- A database migration or a change to Failure Dedup, Patch application, or the
  80% Smoke threshold.
- Requiring every live operation to reach 80%; each must complete a Batch and
  terminate without a technical classification.
- Running the complete offline test suite.

## Evidence

- The 2026-07-31 live run returned `request_body_media_type_unsupported` for
  GitLab POST/PUT because their only request media type was multipart.
- DeepSeek repeatedly returned `{"propose": {...}}` although
  `ParameterPatchDecision` requires top-level `action` and `patch`, consuming
  Patch and then Solve output budgets.
- The same trace placed `reference` inside a `response_value` strategy even
  though the executable Patch contract expects the supplied `R*` alias beside
  the semantic input.

## Verification

- Focused command:
  `uv run pytest -q tests/test_parameter_patch_agent.py
  tests/test_testing_serialization.py tests/test_testing_config_catalog.py
  -k 'not object_cardinality_requires_a_generator_set_that_always_conforms'`
  passed with `52 passed, 1 deselected in 1.01s`. The deselected case is the
  previously recorded unrelated object-cardinality failure.
- A fresh parse of the full local GitLab schema enabled all five operations.
  POST and PUT selected `multipart/form-data` and retained no binary/byte input
  nodes.
- The live command was launched under a process-group deadline and was killed
  after 600 seconds. Artifacts retained at
  `artifacts/gitlab-projects-five-live/gitlab-projects-five-20260731T060425Z-3c271afd`.
- Phoenix retained 500 completed spans, including 110 TestCase executions and
  eleven complete Batches. GET collection and GET-by-id passed. POST and PUT
  each entered Batch but ended with `operation_error`; DELETE was in progress
  when the deadline fired.
- The original Patch wrapper/reference bug did not recur. Model decisions used
  top-level `action`/`patch`, reference aliases used the sibling `reference`
  field, and several Patch sessions validated in two or four outputs.

## Newly discovered blockers

- POST and PUT produced enough distinct failed cases that
  `FailureDeduplicator` attempted to render required JSON evidence beyond the
  Context character budget. `CompactTextWriter` raised
  `required JSON evidence exceeds the Context character budget`, which the
  Coordinator returned as `operation_error`.
- Four Patch sessions repeated a DTO-valid but compile-invalid proposal for all
  twenty outputs. The approved repeated-output guard covers malformed decision
  structures only, so it correctly did not classify these candidates as
  `repeated_invalid_output`; extending the guard to executable validation
  failures is a separate behavior decision.
- These failures are outside the approved two-cause repair. No additional
  algorithm or budget behavior was changed after the live evidence.
