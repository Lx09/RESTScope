# How to read the RESTScope tests

The test suite is executable documentation for decisions already represented in
the code. It does not prove that the current product design is final. Start with
the production-code overview in [`docs/code-reading-guide.md`](../docs/code-reading-guide.md),
then use this file to locate evidence for a particular behavior.

## What a test function means

Each `test_...` function is one scenario. Its docstring states the behavior being
protected, and its name normally follows this pattern:

```text
test_<starting condition>_<action>_<expected result>
```

A test usually has three conceptual parts even when the code does not label
them:

1. **Arrange:** construct the smallest OpenAPI contract, configuration, fake
   model response, database, or HTTP transport needed by the scenario.
2. **Act:** call one public operation or one intentionally isolated helper.
3. **Assert:** compare the observable result, stored record, emitted trace, or
   raised error with the contract under test.

Repeated assertions are often different facets of one outcome. Comments explain
unusual fixtures or safety boundaries; they do not narrate every assignment and
assertion.

## Important test families

| Files | What they protect |
| --- | --- |
| `test_openapi_*` | Parsing multiple OpenAPI versions into normalized IR, operation matching, input-node construction, and document projection. |
| `test_testing_generation.py` | Seeded Generator behavior and construction of path, query, header, cookie, and request-body values. |
| `test_testing_constraints.py`, `test_testing_constraint_solver.py` | Constraint schema, semantic validation, normalization, partial evaluation, bounded solving, and request-tree consistency. |
| `test_generic_batch_tool.py` | Frozen generation revisions, preflight, bounded inline request/outcome evidence, and absence of Test Case registries. |
| `test_parameter_patch_runtime.py` | State closure, deterministic validation, digests, zero-mutation failures, atomic Apply, and revision conflicts. |
| `test_workflow_package_boundaries.py` | Sole Orchestration owner, removal of taskless Main startup, global Tool locality, Harness ownership, and deliberately small public facades. |
| `test_builtin_skill_loader.py`, `test_parameter_patch_skill.py`, `test_resolve_operation_failures_skill.py`, `test_database_query_skill.py`, `test_file_read_tool.py` | Strict built-in Skill discovery, Apply Patch and query methods, lazy Profile-scoped References, parent/child authorization, and removal of retired Tool names. |
| `test_api_behavior_*`, `test_resource_*` | The narrow persistent behavior-monitor catalog and App-lifetime contract learning. |
| `test_orchestration_runtime.py`, `test_task_ledger.py` | Immutable Goal, rolling Replan, fresh Worker roots, failure Attempts, criterion validation, completion, and bounded hundred-round projection. |
| `test_app_tool_context.py`, `test_agent_profile.py`, `test_agent_runtime.py`, `test_agent_plan.py` | App Orchestration startup, bounded Profile instructions, private per-task Agent Plans, exact Provider payloads, correction loops, shared budget, and Context compaction. |
| `test_subagent_runtime.py` | Asynchronous direct-child start/wait/cancel, Profile DAG/depth rules, slot release, timeout, and cooperative cancellation. |
| `test_http_request_tool.py`, `test_database_query_tool.py`, `test_tool_*`, `test_mcp_*` | Tool validation, bounded read-only SQLite execution, operation scope, separate external Catalogs, and MCP adaptation. |
| `test_observability*`, `test_phoenix_tracing_contract.py` | Redaction and trace hierarchy/attributes without changing business behavior. |
| `test_*_live.py`, `test_project_swagger_smoke_e2e_live.py` | Opt-in checks against real local services or providers; these are not ordinary offline tests. |

## Fakes, stubs, and fixtures

- A **fake** implements a real boundary in memory and records calls so the test
  can inspect them.
- A **stub** returns predetermined values and intentionally does not model the
  full dependency.
- A **fixture** is reusable setup supplied by `pytest`; `tests/conftest.py`
  contains suite-wide environment isolation.
- Temporary databases and directories belong to the test that creates them.
  They are not examples of RESTScope's intended persistence architecture.

Model outputs in tests are deliberately both valid and invalid. Invalid JSON or
wrong fields are inputs used to prove repair, rejection, or bounded retry
behavior—not accidental broken test data.

## Offline versus live evidence

Most tests make no external network calls. Live tests are guarded by explicit
environment variables because they may send API schemas, requests, responses,
and prompts to configured services. Passing offline tests does not claim that a
real model provider, target API, or Phoenix deployment was tested.

When changing behavior, read the nearby production docstring first, update the
scenario docstring if its meaning changes, and keep the implementation comment,
test expectation, and task record consistent.
