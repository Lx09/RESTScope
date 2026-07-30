"""Behavioral contracts for the developer-facing Operation Smoke evaluations.

These tests exercise the same suite Interface used by the command-line runner.
They never call DeepSeek, Phoenix, a RESTScope database, or a target API.
"""

from __future__ import annotations

import pytest


# The evaluation stack is intentionally a separate development dependency
# group. The ordinary runtime suite skips this Module in a fresh minimal
# environment; the dedicated verification command installs the group and runs
# every contract below.
pytest.importorskip(
    "phoenix.evals",
    reason="install the evaluation dependency group to run Phoenix Eval tests",
)


class ScriptedLLMClient:
    """Return prepared provider-neutral outputs without calling DeepSeek."""

    def __init__(self, responses):
        """Copy responses because every test owns one finite conversation."""
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Record the exact Agent request and return the next response."""
        self.requests.append(request)
        return self.responses.pop(0)


def _evaluation_model(role: str):
    """Build one enabled model contract accepted by every evaluation task."""
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role=role,
        provider="stub",
        model="stub-model",
        max_tokens=8192,
        context_window_tokens=131072,
    )


def test_registry_loads_three_sanitized_scenarios_for_each_agent() -> None:
    """Scenario: one registry exposes the three approved Agent evaluations."""
    from evaluations.registry import SUITES

    assert set(SUITES) == {"plan", "solve", "patch"}

    for agent_name, suite in SUITES.items():
        scenarios = suite.load_scenarios()

        assert len(scenarios) == 3
        assert len({scenario.scenario_id for scenario in scenarios}) == 3
        assert all(scenario.provenance.kind == "trace" for scenario in scenarios)
        assert all(
            "restscope-project-swagger-smoke-20260727T010238Z-9712a1cf"
            in scenario.provenance.source
            for scenario in scenarios
        )
        assert all(
            secret not in scenario.model_dump_json().lower()
            for scenario in scenarios
            for secret in ("authorization", "password", "api_key")
        ), agent_name


def test_dataset_sync_uses_stable_ids_references_and_scenario_splits() -> None:
    """Scenario: a repo suite mirrors directly into one Phoenix Dataset."""
    from evaluations.core import sync_suite
    from evaluations.registry import SUITES

    class RecordingDatasets:
        """Capture the one Phoenix dataset call made by the shared runner."""

        def __init__(self) -> None:
            self.calls = []

        def create_dataset(self, **kwargs):
            """Return a compact dataset-like object after recording arguments."""
            self.calls.append(kwargs)
            return type(
                "Dataset",
                (),
                {
                    "name": kwargs["name"],
                    "version_id": "version-1",
                    "examples": kwargs["examples"],
                },
            )()

    datasets = RecordingDatasets()
    client = type("Client", (), {"datasets": datasets})()

    dataset = sync_suite(client, SUITES["plan"])

    assert dataset.version_id == "version-1"
    assert len(datasets.calls) == 1
    call = datasets.calls[0]
    assert call["name"] == "restscope-operation-smoke-plan"
    assert [item["id"] for item in call["examples"]] == [
        "plan-merge-duplicate-observations",
        "plan-reuse-history-and-transport",
        "plan-split-independent-failures",
    ]
    first = call["examples"][0]
    assert first["output"] == (
        SUITES["plan"]
        .load_scenarios()[0]
        .expected.model_dump(mode="json", exclude_none=True)
    )
    assert first["id"] in first["splits"]
    assert "acceptance" in first["splits"]


def test_dataset_sync_rejects_a_result_that_still_contains_old_examples() -> None:
    """Scenario: a full mirror cannot silently retain a pre-refactor example."""
    from evaluations.core import sync_suite
    from evaluations.registry import SUITES

    class StaleDatasets:
        """Return the requested examples plus one old row Phoenix failed to delete."""

        def create_dataset(self, **kwargs):
            """Model an incomplete server-side update of the current version."""
            stale_example = {
                "id": "plan-retired-memory-tool-scenario",
                "input": {"operation": "GET /retired"},
                "output": {"memory_failure_ids": ["old-failure"]},
                "metadata": {"source": "before-project-agent-context"},
            }
            return type(
                "Dataset",
                (),
                {
                    "version_id": "version-stale",
                    "examples": [*kwargs["examples"], stale_example],
                },
            )()

    client = type("Client", (), {"datasets": StaleDatasets()})()

    with pytest.raises(RuntimeError, match="unexpected example IDs"):
        sync_suite(client, SUITES["plan"])


def test_dataset_sync_rejects_old_content_under_a_current_scenario_id() -> None:
    """Scenario: a stable ID cannot hide a pre-refactor expected output."""
    from copy import deepcopy

    from evaluations.core import sync_suite
    from evaluations.registry import SUITES

    class StaleDatasets:
        """Return current IDs while preserving one obsolete Planner field."""

        def create_dataset(self, **kwargs):
            """Model the stale Plan Dataset observed before this repair."""
            examples = deepcopy(kwargs["examples"])
            examples[0]["output"] = {
                "status": "planned",
                "memory_failure_ids": [],
                "case_groups": [["missing-project-a", "missing-project-b"]],
            }
            return type(
                "Dataset",
                (),
                {"version_id": "version-stale", "examples": examples},
            )()

    client = type("Client", (), {"datasets": StaleDatasets()})()

    with pytest.raises(RuntimeError, match="stale example content"):
        sync_suite(client, SUITES["plan"])


def test_plan_code_evaluators_report_one_zero_and_not_applicable() -> None:
    """Scenario: each declared property is scored without an aggregate result."""
    from evaluations.agents.plan.suite import (
        plan_candidate_retrieval_evaluator,
        plan_case_groups_evaluator,
        plan_status_evaluator,
    )

    output = {
        "result": {
            "status": "planned",
            "todos": [
                {
                    "failure_id": "eval-new-1",
                    "cases": [{"case_id": "case-a"}, {"case_id": "case-b"}],
                }
            ],
            "non_debuggable": [],
        },
        "tool_calls": [],
        "runtime_error": None,
    }

    status = plan_status_evaluator.evaluate(
        {"output": output, "expected": {"status": "planned"}}
    )[0]
    groups = plan_case_groups_evaluator.evaluate(
        {
            "output": output,
            "expected": {"case_groups": [["case-a"], ["case-b"]]},
        }
    )[0]
    lookup = plan_candidate_retrieval_evaluator.evaluate(
        {"output": output, "expected": {}}
    )[0]

    assert (status.score, status.label) == (1, "satisfied")
    assert (groups.score, groups.label) == (0, "not_satisfied")
    assert lookup.score is None
    assert lookup.label == "not_applicable"


def test_solve_task_uses_fresh_scripted_tools_and_applies_only_selected_patch() -> None:
    """Scenario: Phoenix task runs real Solve logic with no DB or target HTTP."""
    from evaluations.registry import SUITES
    from restscope.llm import LLMResponse, ToolCall
    from restscope.observability import TracingRuntime

    scenario = next(
        item
        for item in SUITES["solve"].load_scenarios()
        if item.scenario_id == "solve-memory-patch-apply"
    )
    client = ScriptedLLMClient(
        [
            LLMResponse(
                provider="stub",
                model="stub-model",
                tool_calls=[
                    ToolCall(
                        id="memory-1",
                        name="lookup_parameter_history",
                        arguments={"input_handles": ["path.projectId"]},
                    )
                ],
            ),
            LLMResponse(
                provider="stub",
                model="stub-model",
                tool_calls=[
                    ToolCall(
                        id="patch-1",
                        name="generate_parameter_patch",
                        arguments={
                            "root_cause": "The range is too broad.",
                            "affected_inputs": ["path.projectId"],
                            "desired_behavior": "Generate integers from 3 to 100.",
                            "acceptance_criteria": "Every value is in 3..100.",
                        },
                    )
                ],
            ),
            LLMResponse(
                provider="stub",
                model="stub-model",
                parsed_json={
                    "action": "apply_patch",
                    "candidate_ref": "P1",
                    "trigger_conditions": "projectId is outside 3..100",
                    "root_cause": "path.projectId has an overly broad Generator",
                    "solution": "Apply the validated 3..100 Generator",
                    "evidence_source": "memory",
                    "parameters": [
                        {
                            "input_handle": "path.projectId",
                            "cause_summary": "The generated identifier causes 404.",
                        }
                    ],
                    "conflict_reason": None,
                    "reason": None,
                    "next_step": None,
                },
            ),
        ]
    )

    task = SUITES["solve"].build_task(
        client=client,
        model=_evaluation_model("operation_smoke_failure_solve"),
        tracing_runtime=TracingRuntime.disabled(),
        system_prompt=None,
        seed=17,
    )
    output = task(scenario.input.model_dump(mode="json"))

    assert output["runtime_error"] is None
    assert output["result"]["status"] == "applied_patch"
    assert [
        call["tool"] for call in output["tool_calls"]
    ].count("apply_patch") == 1
    assert not any(
        call["tool"] == "restscope.http.request"
        for call in output["tool_calls"]
    )
    assert "path.projectId" in client.requests[0].messages[1].content
    assert "path/projectId" not in client.requests[0].messages[1].content


def test_patch_task_runs_real_compile_sampling_and_review() -> None:
    """Scenario: Patch evaluation uses production validation before acceptance."""
    from evaluations.registry import SUITES
    from restscope.llm import LLMResponse
    from restscope.observability import TracingRuntime

    scenario = next(
        item
        for item in SUITES["patch"].load_scenarios()
        if item.scenario_id == "patch-integer-range"
    )
    client = ScriptedLLMClient(
        [
            LLMResponse(
                provider="stub",
                model="stub-model",
                parsed_json={
                    "action": "propose",
                    "patch": {
                        "changes": [
                            {
                                "input": "path.projectId",
                                "strategy": {
                                    "type": "integer_range",
                                    "minimum": 3,
                                    "maximum": 100,
                                },
                            }
                        ],
                        "constraints": [],
                    },
                },
            ),
            LLMResponse(
                provider="stub",
                model="stub-model",
                parsed_json={"action": "accept", "patch": None},
            ),
        ]
    )

    task = SUITES["patch"].build_task(
        client=client,
        model=_evaluation_model("operation_smoke_parameter_patch"),
        tracing_runtime=TracingRuntime.disabled(),
        system_prompt=None,
        seed=23,
    )
    output = task(scenario.input.model_dump(mode="json"))

    assert output["runtime_error"] is None
    assert output["result"]["status"] == "validated"
    strategy = output["result"]["patch"]["updates"][0]["strategy"]
    assert strategy == {
        "type": "integer_range",
        "minimum": 3,
        "maximum": 100,
    }
    assert len(output["result"]["samples"]) == scenario.input.case_count


def test_run_syncs_filters_and_records_reproducible_experiment_metadata() -> None:
    """Scenario: one selected Scenario becomes one native Phoenix Experiment."""
    from evaluations.core import run_suite
    from evaluations.registry import SUITES
    from restscope.observability import TracingRuntime

    class RecordingDatasets:
        """Model the two official Dataset calls used by a filtered run."""

        def __init__(self) -> None:
            self.created = []
            self.filtered = []

        def create_dataset(self, **kwargs):
            """Return the synchronized version after retaining all examples."""
            self.created.append(kwargs)
            return type(
                "Dataset",
                (),
                {
                    "version_id": "version-9",
                    "examples": kwargs["examples"],
                },
            )()

        def get_dataset(self, **kwargs):
            """Return a one-scenario marker and retain the split filter."""
            self.filtered.append(kwargs)
            return type("Dataset", (), {"version_id": "version-9"})()

    class RecordingExperiments:
        """Capture the native Phoenix run call without executing a task."""

        def __init__(self) -> None:
            self.calls = []

        def run_experiment(self, **kwargs):
            """Return the same compact keys printed by the CLI."""
            self.calls.append(kwargs)
            return {
                "experiment_id": "experiment-1",
                "dataset_version_id": "version-9",
            }

    datasets = RecordingDatasets()
    experiments = RecordingExperiments()
    phoenix = type(
        "Client",
        (),
        {"datasets": datasets, "experiments": experiments},
    )()
    suite = SUITES["plan"]

    result = run_suite(
        phoenix_client=phoenix,
        suite=suite,
        llm_client=ScriptedLLMClient([]),
        model=_evaluation_model("operation_smoke_plan"),
        tracing_runtime=TracingRuntime.disabled(),
        prompt_name="current",
        repetitions=3,
        seed=41,
        git_revision="1234567890abcdef",
        scenario_id="plan-merge-duplicate-observations",
    )

    assert result["experiment_id"] == "experiment-1"
    assert datasets.filtered == [
        {
            "dataset": suite.dataset_name,
            "version_id": "version-9",
            "splits": ["plan-merge-duplicate-observations"],
        }
    ]
    call = experiments.calls[0]
    assert call["repetitions"] == 3
    assert call["experiment_metadata"]["seed"] == 41
    assert call["experiment_metadata"]["dataset_version"] == "version-9"
    assert call["experiment_metadata"]["prompt_name"] == "current"
    assert len(call["experiment_metadata"]["prompt_sha256"]) == 64
    assert call["experiment_metadata"]["git_revision"] == "1234567890abcdef"


def test_solve_and_patch_evaluators_keep_na_separate_from_zero() -> None:
    """Scenario: later suites use the same explicit 1/0/N/A score semantics."""
    from evaluations.agents.patch.suite import generators_evaluator
    from evaluations.agents.solve.suite import (
        application_evaluator,
        status_evaluator,
    )

    solve_output = {
        "result": {"status": "conflict"},
        "tool_calls": [],
        "runtime_error": None,
    }
    solve_pass = status_evaluator.evaluate(
        {"output": solve_output, "expected": {"status": "conflict"}}
    )[0]
    solve_na = application_evaluator.evaluate(
        {"output": solve_output, "expected": {}}
    )[0]

    patch_output = {
        "result": {
            "status": "validated",
            "patch": {
                "updates": [
                    {
                        "input_node_id": "path/projectId",
                        "strategy": {
                            "type": "integer_range",
                            "minimum": 1,
                            "maximum": 100,
                        },
                    }
                ]
            },
        },
        "input_handle_by_node": {"path/projectId": "path.projectId"},
    }
    patch_fail = generators_evaluator.evaluate(
        {
            "output": patch_output,
            "expected": {
                "generators": [
                    {
                        "input_handle": "path.projectId",
                        "strategy_type": "integer_range",
                        "minimum": 3,
                        "maximum": 100,
                    }
                ]
            },
        }
    )[0]

    assert (solve_pass.score, solve_pass.label) == (1, "satisfied")
    assert solve_na.score is None
    assert solve_na.label == "not_applicable"
    assert (patch_fail.score, patch_fail.label) == (0, "not_satisfied")


def test_evaluation_modules_do_not_import_database_or_target_http_clients() -> None:
    """Scenario: isolated Agent Evals cannot silently acquire live side effects."""
    from pathlib import Path

    evaluation_root = Path(__file__).parents[1] / "evaluations"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in evaluation_root.rglob("*.py")
    )

    assert "restscope.db" not in source
    assert "requests." not in source


def test_loopback_phoenix_client_ignores_environment_http_proxy() -> None:
    """Scenario: local Dataset traffic reaches Phoenix instead of a proxy."""
    from evaluations.cli import _client
    from restscope.restscope_config import TracingConfig

    config = type(
        "Config",
        (),
        {
            "tracing": TracingConfig(
                collector_endpoint="http://localhost:6006"
            )
        },
    )()

    client = _client(config)

    assert client._client._trust_env is False


def test_missing_llm_role_configuration_fails_before_an_experiment() -> None:
    """Scenario: a globally unusable model is not mislabeled as one bad sample."""
    from evaluations.cli import _require_configured_model
    from restscope.llm import LLMModelConfig

    model = LLMModelConfig(
        role="operation_smoke_plan",
        provider="deepseek",
        model="",
        enabled=False,
    )
    client = type("Client", (), {"registry": object()})()

    with pytest.raises(RuntimeError, match="not configured"):
        _require_configured_model(client, model)
