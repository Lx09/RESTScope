# API Response Monitor redesign

## Status

Implemented and freshly verified in the feature worktree. The internal
ownership optimization is complete. No commit, merge, push, or worktree
cleanup has been performed; those Git actions still require separate
authorization.

Superseded navigation note (2026-08-12): the later code-navigation refactor
renames `ResponseMonitorCatalog` to `APIBehaviorCatalog`, combines the OpenAPI
audit persistence seam with it, and flattens the Monitor's shallow internal
packages. The behavior and database decisions recorded here remain active.

## Objective

Replace the existing Resource Identifier and Response Value persistence model
with operation observations, resource instances, exact operation input sources,
and immutable abstract test-case snapshots. Successful JSON responses become
the factual input; response values are resolved on demand instead of being
materialized into shared pools.

## Approved scope

- Add `operations`, simplified `resources`, `operation_resource_edges`,
  `resource_instances`, `observations`, `operation_input_sources`, and
  `abstract_test_cases` to the fresh-database baseline.
- Keep only the latest 100 valid 2xx JSON observations per operation through
  logical SQL deletion.
- Store the actual request after sensitive request-header removal and the full
  original valid JSON response text.
- Derive composite resource instances with recursive object merging and
  `_deleted` lifecycle state.
- Replace response-value pools with exact `RESOURCE` and `VALUE_REUSE` source
  bindings and on-demand value resolution.
- Associate successful generated observations with one immutable abstract
  generation-state snapshot.
- Keep source `_alpha` and `_beta` at their Beta(1,1) defaults; evidence update
  semantics remain deliberately undefined.
- Update persistence governance and beginner-readable documentation to match
  the approved boundary.

## Non-goals

- Migrating an existing SQLite database.
- Persisting resource extraction rules, scheduler state, Agent reasoning, or
  restorable request-generation state.
- Adding a shared producer-value table, database encryption, secure deletion,
  automatic vacuuming, or response-size limits.
- Implementing confidence-based source selection or evidence update Tools.

## Public seams under test

- Fresh database bootstrap and schema catalog.
- API Behavior Monitor response-processing result and persisted read models.
- Parameter Patch semantic Generator/source contracts and atomic application.
- `test_case.run_batch` result and observation linkage.
- `resource.list_resources` and `resource.list_ids` Tool contracts.

## Implementation phases

1. **Schema and catalogs** — replace the baseline tables and introduce typed,
   transactional records for operations, observations, resources, sources, and
   abstract test cases.
2. **Response intake** — persist sanitized request/full JSON response facts,
   retain 100 observations, isolate Contract Monitor failures, and derive
   resource instances after observation commit.
3. **Reference generation** — compile exact sources, resolve values on demand,
   and preserve composite-resource correlation within each generated case.
4. **Batch snapshots and Tools** — register abstract cases before network side
   effects, link observations, and simplify resource reads.
5. **Documentation and verification** — update current architecture records,
   run focused/full tests, scan for `typing.Any`, and check diff hygiene.

## Internal ownership optimization

- `ResponseFieldReference.from_selector()` remains the sole selector parser,
  and the same value now traverses parsed JSON directly through
  `select_values()`.
- `ResponseMonitorCatalog` owns its Unit of Work privately and exposes one
  staged source transaction. The later navigation cleanup consolidated Patch
  reads and writes into concrete `BehaviorMonitorReferences`; the shared
  `ReferenceValueProvider` remains only for value-generation consumers.
- Patch application computes final bindings once, stages their operation/source
  rows, publishes the in-memory state, then commits; publication or commit
  failure restores both boundaries.
- Observation reads filter by exact operation, status, and normalized media
  type before loading JSON. VALUE_REUSE reads newest-first pages and stops at
  eight type-aware distinct values.
- Observed-field lookup enumerates only OpenAPI candidates for distinct retained
  response coordinates, then confirms those selectors against bounded exact
  observation pages. It does not enumerate arbitrary stored JSON fields.
- `ResponseSourceCoordinate` centralizes source validation for Generator,
  Generation State, and persistence values while their public flat shapes stay
  unchanged.
- Exact Resource Tool lookup uses the unique resource name instead of scanning
  all resource pages. Unused Tracker arguments and legacy compatibility aliases
  were removed.
- The existing target-request Module now owns shared media-type normalization
  and JSON recognition for transport, OpenAPI, request generation,
  observation, and UI projection.

## Material decisions

- Operation identity is the normalized IR operation key, not OpenAPI
  `operationId`.
- Resource names are normalized and unique; identity fields are direct,
  immutable response property names whose values are strings or integers.
- A composite resource's consumer inputs always draw components from the same
  resource instance within one generated case.
- Source status codes are concrete observed integers. Source registration
  requires a currently available compatible value.
- Contract changes are successful `updated` results. Only inability to perform
  the check is a warning, and it must not partially mutate the normalized
  contract or audit event.
- Observation persistence and resource derivation use separate transactions.
  A resource failure never removes the factual observation.
- Unmatched OpenAPI operations are not persisted.

## Verification log

- Initial Schema seam: `uv run pytest -q tests/test_schema_catalog.py::test_orm_metadata_contains_only_the_approved_response_monitor_tables`
  fails as expected because the fourteen legacy Monitor tables are still
  registered and the seven replacement tables do not exist yet.
- Schema vertical slice is green: `uv run pytest -q tests/test_schema_catalog.py`
  passes 3 tests for the exact nine-table topology, database constraints, and
  reversible fresh baseline migration.
- First composite-instance green attempt exposed duplicate pending edge rows
  when two derivations normalized to the same resource and role. The session
  deliberately disables autoflush, so repository reads could not see the first
  pending edge; the fix is to flush each newly materialized natural key before
  processing the next derivation.
- Unified Catalog slice is green: `uv run pytest -q
  tests/test_api_behavior_catalog.py tests/test_schema_catalog.py` passes 6
  tests covering exact response retention, latest-100 pruning, composite state
  merge, normalized resources, and distinct RESOURCE/VALUE_REUSE source keys.
- The first App bootstrap probe now fails at the expected replacement seam:
  `RESTScopeApp` still imports the retired Resource and Response Value unit of
  work classes. App composition will be switched only after the unified Catalog
  has the abstract-case and reference-value behaviors required by its current
  consumers.
- Exact Generator sources now carry producer operation, actual successful
  status, normalized media type, selector, and field name. OpenAPI observed
  field discovery separately returns `matched_status_code` for Schema context
  and integer `status_code` for persistence, so `2XX` and `default` contracts
  remain usable without losing the actual response coordinate.
- Retired Resource Catalog, learned-rule, scalar-observation, and shared
  response-value-pool Modules and their superseded tests were removed. Their
  current behaviors are covered through the unified Catalog, response intake,
  Resource Response Tracker, on-demand reference provider, Batch, and Resource
  Tool seams.
- Focused final verification: `uv run pytest -q tests/test_openapi_lookup_tool.py
  tests/test_parameter_patch_runtime.py tests/test_generic_batch_tool.py
  tests/test_testing_constraint_solver.py` passed 43 tests.
- Complete final verification after the actual-status source correction and
  documentation update: `uv run pytest -q` passed 516 tests with 13
  environment-dependent skips in 4.36 seconds.
- The explicit repository guard `uv run pytest -q tests/test_no_typing_any.py`
  passed, `git diff --check` produced no findings, and the main checkout still
  contains only the user's pre-existing Evidence Confidence changes.
- Internal optimization focused verification passed 57 tests across selector
  traversal, bounded observations, staged Patch application, observed OpenAPI
  queries, Resource Tools, response intake, and Harness composition.
- Fresh complete verification after the optimization passed 521 tests with 13
  environment-dependent skips in 4.40 seconds.
- The `typing.Any` guard passed again, `git diff --check` remained clean, and
  helper scans found one selector parser, one JSON selector traversal, and one
  media-type normalization/JSON-recognition implementation. Remaining names
  containing `resource_backend` or `identifier_records` are active local
  composition and constraint-matching code, not the deleted compatibility
  seams.
