# Target API Request Foundation

Status: Complete

## Objective

Make every request to the tested API use one clearly named top-level Module.
Replace `target_http` with `target_api`, deepen its Client Interface, and keep
request preparation separate from network effects.

## Approved decisions

- `target_api` remains top-level; Harness is a consumer, not its owner.
- Use `TargetAPIClient`, `prepare_target_request()`, `TargetAPIError`, and
  `TargetAPITimeout` with no compatibility aliases.
- Keep response DTO names that accurately describe HTTP responses.
- Keep one direct `send()` Interface rather than adding a response-policy type.
- Client internally separates complete Monitor facts, a 1 MiB Observer view,
  and the caller-requested response body.
- Simplicity and code navigation take priority over preserving old names.

## Non-goals

- No Tool Schema, database, target network behavior, dependency, or response
  processing order change.
- No new factory, compatibility Module, or single-implementation Protocol.
- No Git staging, commit, or push.

## Verification

- Public request preparation, Client response projection, and package seams.
- HTTP Tool, Batch, API Behavior response flow, and Live Observer integration.
- Complete tests, `typing.Any` guard, Python compilation, wheel content, old
  path scan, and `git diff --check`.

Fresh results on 2026-08-12:

- `uv run pytest -q`: 560 passed, 2 skipped.
- Focused Client and integration suite: 101 passed.
- Python compilation and `typing.Any` guard passed.
- Wheel contains all six `restscope.target_api` files and no
  `restscope.target_http` path.
- Retired-name scan and `git diff --check` passed.
