# Generic Model Catalog and Profile Reasoning Effort

Status: Complete and verified

## Objective

Replace the fixed THINK/FAST environment slots with a validated TOML catalog
of arbitrarily many named models. Keep exact model selection and thinking
effort visible in each Agent Profile while shortening the rest of RESTScope's
environment Interface.

## Approved scope

- Load zero or more named models from one optional `MODELS_FILE`.
- Configure the existing DeepSeek and OpenAI-compatible Provider adapters once
  each and resolve their API keys from named environment variables.
- Require every `AgentProfile` to select one model and one of
  `none`, `low`, `high`, or `max` reasoning effort.
- Reject every retired environment name with a direct migration message.
- Migrate the ignored local configuration without exposing its secrets.
- Update current examples, navigation, and tests without rewriting historical
  task records.

## Non-goals

- Multiple connections for one Provider adapter type.
- Task-level effort overrides or runtime model selection.
- Provider capability registries, compatibility aliases, persistence, or live
  DeepSeek/Phoenix/target calls.
- Git staging, commits, merges, pushes, branches, or worktree operations.

## Decisions

- TOML owns provider connection metadata and model capacity; `.env` owns the
  model-file path and secret values only.
- Agent Profiles own reasoning effort. Model configurations contain no
  reasoning defaults.
- Current production Profiles share the `default` model. The Orchestrator and
  Task Executor use `high`, Parameter Patch uses `low`, and resource identity
  and state Profiles use `none`. RESTScope does not expose `medium` as a synonym
  for Provider-side `high`.
- Old environment names fail fast and are never read as configuration.

## Verification

- `uv run pytest -q`: 730 passed, 2 skipped.
- `uv run ruff check restscope tests`: passed.
- `uv run python -m compileall -q restscope tests`: passed.
- `uv run pytest -q tests/test_no_typing_any.py`: passed.
- `git diff --check`: passed.
- Runtime-read scan found old environment names only in explicit rejection
  guards; current examples and current source terminology contain no fixed
  THINK/FAST slot behavior.
- `.env` and local `models.toml` are ignored. The migration retained the local
  secret without printing it or adding it to the tracked diff.
- No real DeepSeek, Phoenix, MCP, or target API call was made.
