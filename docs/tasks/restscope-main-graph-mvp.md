# RESTScope Main Graph MVP

## Status

Completed.

## Objective

Add the first supervisor graph and Python startup API for running RESTScope as a standalone program.

## Scope

- Add `RESTScopeMainGraph` above `OperationTestAgent`.
- Add direct request and aggregate report schemas.
- Add `RESTScopeApp` as the Python runtime entrypoint.
- Support explicit selected-operation testing only.
- Keep the MVP read-only with respect to business fact tables.

## Verification

- `uv run pytest tests/test_main_graph_mvp.py -q` passed with 7 tests.
- `uv run pytest tests/test_operation_agent_mvp.py tests/test_operation_agent_policy.py tests/test_main_graph_mvp.py -q` passed with 15 tests.
- `uv run python -c "from restscope import RESTScopeApp, RESTScopeRunRequest"` passed.
