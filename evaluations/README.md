# Failure Resolution Agent Evaluation

This directory evaluates the continuous `FailureResolutionAgent` boundary as
one Phoenix suite. The Agent owns semantic grouping, investigation, worklist
rewrites, candidate selection, and finish timing. Long sessions may invoke the
real nested Compact Agent, and a Patch scenario invokes the real nested
Parameter Patch and Review Agents under the same output guard.

Repository YAML files are the source of truth. A run synchronizes them into the
single `restscope-operation-smoke-resolution` Dataset, starts a native Phoenix
Experiment, invokes the configured models, and records independent code scores
plus linked traces. Each example receives fresh session registries and a
storage-free finalizer; it never connects to the RESTScope database or sends a
request to a target API.

## Setup

Install the evaluation dependency group and start the local Phoenix service:

```bash
uv sync --group evaluation
docker compose -f compose.phoenix.yaml up -d
```

The existing `.env` supplies model settings and the Phoenix endpoint.
Experiment runs enable RESTScope tracing in the project
`restscope-evals-resolution`.

## Commands

```bash
# Inspect stable scenario IDs and available complete prompts.
uv run --group evaluation python -m evaluations list

# Synchronize the one Dataset. "all" is accepted for CLI consistency.
uv run --group evaluation python -m evaluations sync resolution

# One exploratory run of semantic grouping.
uv run --group evaluation python -m evaluations run resolution \
  --scenario resolution-merge-shared-parameter \
  --prompt current --repetitions 1 --seed 0

# Exercise Resolution plus nested Patch and Review.
uv run --group evaluation python -m evaluations run resolution \
  --scenario resolution-patch-bounded-identifier \
  --prompt current --repetitions 3 --seed 0
```

Place a complete candidate Resolution system prompt at
`evaluations/agents/resolution/prompts/<candidate-name>.txt`. `current` always
means the production prompt and passes no override into production assembly.
Experiments never write a candidate prompt back to `restscope/`.

## Adding a Scenario

Copy one YAML file in `evaluations/agents/resolution/scenarios/`, give it a
stable unique `resolution-*` ID, replace every input fact, and declare only the
independent properties that should be scored. A missing expected property is
reported as `not_applicable` and receives no numeric score.

Scenarios contain a failed Batch's request identity, sanitized OpenAPI source,
run-local Test Case drafts, and an optional executable Generator baseline. The
baseline enables the real Patch/Review path; omitting it leaves candidate and
HTTP mutation tools unavailable. Production code renders initial model context,
so prompt variants compare the same minimal operation/message/E-to-TC input and
on-demand tool path used by the App.

Trace provenance explains why a Scenario matters but is not an answer oracle.
Sanitize credentials and identifying target data before committing a Scenario.
Keep original exports under ignored `artifacts/phoenix-exports/`.

Task output contains the native Resolution result and a separate
`runtime_error`. A low score or one example's runtime error remains valid
evaluation data. Invalid Scenarios, model/Phoenix configuration, Dataset sync,
or Experiment infrastructure still make the command fail.
