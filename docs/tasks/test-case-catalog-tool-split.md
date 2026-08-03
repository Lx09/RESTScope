# Test Case Catalog Tool Split

Status: Completed

> Superseded output-shape note (2026-08-03): Test Cases now retain structured
> request JSON, and Parameter/response tools return direct-name JSON fragments
> alongside unique handles or paths. See
> `test-case-structured-request-json.md`.

## Objective

Replace the action-dispatched `query_test_case_catalog` model tool with five
single-purpose `test_case.*` tools. Make request Parameters that were not used,
response fields that are absent, and response bodies that were not retained
explicitly distinguishable without boolean presence flags.

## Approved scope

- Expose get-Parameter, reverse-Parameter, get-response-field,
  reverse-response-field, and Failure-message queries as separate tools.
- Register all five tools for both Failure Dedup and Failure Solve.
- Preserve same-query batching for 1–20 unique `TC*` references.
- Remove the old tool name, action argument, action result envelope, and
  compatibility aliases.
- Update prompts, evaluations, current documentation, and project governance.
- Verify locally without a live model, target API, or Phoenix service.

## Decisions

- An unused Parameter returns `parameter_not_used_in_request`; a used one
  returns `parameter_used_in_request` and its value.
- A response lookup distinguishes `response_body_not_retained`,
  `response_field_not_present_in_retained_body`, and
  `response_field_present_in_retained_body`.
- RESTScope-owned LLM tools may batch one behavior and accept target/filter
  selectors, but must not use an input discriminator to select unrelated
  behaviors. Agent output DTOs, internal domain DTOs, and external MCP tools
  are outside that rule.
- Catalog storage, validation, typed comparison, response traversal, and
  bounding remain local to one deep workflow Module.

## Non-goals

- No change to retained evidence, persistence, Agent permissions, HTTP Probe,
  OpenAPI tools, or reasoning-loop budgets.
- No central tool registry, runtime heuristic enforcement, live external call,
  compatibility period, commit, merge, push, or worktree cleanup.

## Verification

- Final focused Catalog, Dedup, Solve, Coordinator, integration, tracing, and
  offline evaluation set: 45 passed, 2 environment-dependent skips.
- Full local suite: 519 passed, 18 skips. The skips require the optional
  tracing/evaluation dependencies or an explicitly enabled live GitLab,
  Swagger Validator, Phoenix, or DeepSeek run.
- Full Python compilation and `git diff --check`: passed.
- Residual production/prompt/test/evaluation search found no old Catalog tool
  name, action-dispatched query DTO, or Catalog presence boolean.

No live LLM, target API, Phoenix service, or external validator was called.
The user authorized the feature commit, local `main` merge, and feature
worktree/branch cleanup after verification. No push was authorized.
