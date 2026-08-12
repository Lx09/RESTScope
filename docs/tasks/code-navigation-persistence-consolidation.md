# Code Navigation and API Behavior Persistence Consolidation

Status: Implemented and verified on local `main`; Git delivery not authorized

## Objective

Make the runtime's main concepts visible from the package tree. Consolidate the
current OpenAPI audit and response-evidence persistence into API Behavior
Monitor, remove shallow internal packages, and narrow the Request Generation
facade without changing runtime behavior or the nine-table database schema.

## Approved scope

- Replace `OpenAPIAudit` and `ResponseMonitorCatalog` with one
  `APIBehaviorCatalog` and one private Repository/UoW seam.
- Use one SQLAlchemy API Behavior adapter and one ORM module while preserving
  every table, column, constraint, index, and transaction boundary.
- Flatten Contract Monitor and Resource Monitor internals into clearly named
  files and remove old import paths without compatibility aliases.
- Limit the Request Generation root facade to its four cross-Module integration
  entries; internal callers and tests import other definitions from their
  owners.
- Keep App OpenAPI export/change-event behavior, response stage ordering, Tool
  schemas, observation retention, resource merge, and staged Patch rollback
  unchanged.

## Non-goals

- No database migration or Schema change.
- No global ports layer or project-wide parent package restructuring.
- No feature behavior, external service call, Git staging, commit, or push.
- No modification to the pre-existing target transport work.

## Verification

- Baseline: `uv run pytest -q` -> 552 passed, 2 skipped.
- Complete behavior suite after implementation: `uv run pytest -q` -> 556
  passed, 2 skipped.
- Focused Catalog, Contract, Resource, response flow, Batch, Patch, package
  boundary, and `typing.Any` verification: 44 passed.
- Python compile, `git diff --check`, and unchanged Alembic baseline checks
  passed.
- The wheel contains all six new navigation owners and none of the seven
  retired package/Adapter/ORM paths.
