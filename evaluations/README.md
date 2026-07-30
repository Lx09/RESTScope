# Operation Smoke Agent Evaluations

This directory evaluates the three LLM decision components independently:
`SmokePlanAgent`, `FailureSolveAgent`, and `ParameterPatchAgent`.

The repository YAML files are the source of truth. A run synchronizes them into
one Phoenix Dataset per Agent, starts a native Phoenix Experiment, invokes the
real configured DeepSeek model, and records independent Phoenix code-evaluator
scores plus linked traces. It does not connect to the RESTScope database or send
requests to a target API.

## Setup

Install the development group and start the local Phoenix service:

```bash
uv sync --group evaluation
docker compose -f compose.phoenix.yaml up -d
```

The existing `.env` supplies the THINK/FAST DeepSeek settings and Phoenix
endpoint. Experiment runs always enable RESTScope tracing and use the Phoenix
projects `restscope-evals-plan`, `restscope-evals-solve`, or
`restscope-evals-patch`.

## Commands

```bash
# Inspect stable scenario IDs and available complete prompts.
uv run --group evaluation python -m evaluations list

# Synchronize one Dataset, or use "all".
uv run --group evaluation python -m evaluations sync plan

# One cheap exploratory run.
uv run --group evaluation python -m evaluations run plan \
  --scenario plan-merge-duplicate-observations \
  --prompt current --repetitions 1 --seed 0

# A prompt comparison keeps all three repetitions.
uv run --group evaluation python -m evaluations run patch \
  --scenario patch-integer-range \
  --prompt candidate-name --repetitions 3 --seed 0
```

Place a complete candidate system prompt at
`evaluations/agents/<agent>/prompts/<candidate-name>.txt`. `current` always
means the production prompt and passes no override into production assembly.
Experiments never write a candidate prompt back to `restscope/`.

## Adding a Scenario or Agent

For an existing Agent, copy one YAML file in its `scenarios/` directory, give it
a stable unique `scenario_id`, replace all input facts, and declare only the
independent properties that should be scored. A missing expected property is
reported as `not_applicable` and does not receive a numeric score.

Plan Scenario `catalog` and `histories` describe the bounded candidate window
returned before the model call; Planner no longer queries them with a tool.
Solve still uses scripted Parameter Memory, HTTP Probe, and nested Patch tools.
All three production Agents render Scenario facts through `restscope.context`,
so prompt variants compare the same compact text path used by the App.

An old trace explains why a Scenario matters but is not an answer oracle.
Sanitize credentials, raw response bodies, and identifying target data before
committing a Scenario. Keep the original export under ignored
`artifacts/phoenix-exports/`.

Adding another Agent requires one suite Module with its DTO, fresh temporary
collaborators, task, and code evaluators; add exactly one line to
`evaluations/registry.py`. The shared runner does not need Agent-specific
branches.

Task output contains the native Agent result, a compact tool-call record, and a
separate `runtime_error`. A low score or a single-example runtime error remains
valid evaluation data and does not make the command fail. Invalid Scenario,
DeepSeek/Phoenix configuration, Dataset synchronization, or Experiment
infrastructure does make it fail.
