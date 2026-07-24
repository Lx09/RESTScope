# OpenAPI IR Online Validator Acceptance Test

Status: Implemented; online validation pending

## Objective

Exercise every OpenAPI asset through the current parser IR and document builder,
retain human-readable before/after evidence, and validate the exact generated
OpenAPI 3.1 YAML with Swagger's public online validator.

## Approved scope

- Cover the four explicit files under `assets/openapi/`.
- Select every operation present in each parsed IR.
- Generate OpenAPI 3.1 YAML under the ignored
  `artifacts/openapi-ir-roundtrip/` directory.
- Retain source copies, canonical JSON, unified diffs, semantic comparisons,
  validator responses, and one Markdown summary.
- Keep the test opt-in through `RUN_OPENAPI_IR_VALIDATOR_LIVE=1` because it
  sends complete generated specifications to `validator.swagger.io`.
- Treat an HTTP 200 response with no `messages` and no
  `schemaValidationMessages` as a validator pass.

The user explicitly authorized sending the complete generated forms of these
four asset specifications to Swagger Validator.

## Non-goals

- Production parser or document-builder changes.
- New Python, Java, Maven, or container dependencies.
- Lossless source-document round trips.
- Persisting generated artifacts in Git.
- Running, enabling, or modifying GitHub CI/CD.
- Creating a commit or pushing changes.

## Verification

Observed locally on 2026-07-22:

- `uv run --no-sync pytest -q tests/test_openapi_ir_to_spec_live.py` — one
  test skipped as designed, with no network request.
- An offline execution of the generation and comparison path produced all four
  artifact sets. Generated documents reparsed without errors and preserved
  operation counts and keys: 73, 40, 20, and 67 respectively.
- The Validator request path was exercised with an in-process `urlopen`
  substitute: four requests used a 30-second timeout, `application/yaml`, the
  configured query flags, and bytes identical to each retained generated YAML.
  This verifies test wiring, not Swagger Validator acceptance.
- `uv run --no-sync pytest -q tests/test_openapi_document_builder.py` — 13
  passed.
- `uv run --no-sync pytest -q` — 163 passed, 3 skipped, 1 unrelated failure.
  `tests/test_schemathesis_mcp_contract.py` fails while its child `uv` process
  panics in the macOS `system-configuration` crate and closes the MCP stdio
  connection. An isolated rerun reproduced the same environment failure after
  removing `VIRTUAL_ENV`; this task does not modify that test or service.
- `uv run --no-sync pytest -q --ignore=tests/test_schemathesis_mcp_contract.py`
  — 163 passed, 3 skipped.
- `uv run --no-sync python -m compileall -q restscope`, direct compilation of
  the new test file, `git diff --check`, and trailing-whitespace checks all
  completed successfully.

The controlled execution environment rejected uploading the full generated
specifications to `validator.swagger.io`, so no official online response was
obtained. Final local artifacts truthfully record `not verified`.

Commands for the user-authorized online run:

```bash
uv run pytest -q tests/test_openapi_ir_to_spec_live.py
RUN_OPENAPI_IR_VALIDATOR_LIVE=1 uv run pytest -q -s tests/test_openapi_ir_to_spec_live.py
uv run pytest -q tests/test_openapi_document_builder.py
uv run pytest -q
uv run python -m compileall -q restscope
git diff --check
```

Online validation must remain recorded as unverified if the execution
environment prevents uploading workspace-derived specifications.
