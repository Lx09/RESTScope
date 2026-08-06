"""Behavioral contracts for the single Failure Resolution Phoenix suite.

These tests exercise the same suite Interface used by the developer CLI. They
use scripted provider-neutral responses and never call Phoenix, a real model,
a RESTScope database, or a target API.
"""

from __future__ import annotations

import pytest


pytest.importorskip(
    "phoenix.evals",
    reason="install the evaluation dependency group to run Phoenix Eval tests",
)


class ScriptedLLMClient:
    """Return prepared provider-neutral outputs without calling a real model."""

    def __init__(self, responses) -> None:
        """Copy responses because every test owns one finite conversation."""
        self.responses = list(responses)
        self.requests = []

    def invoke(self, request):
        """Record the exact Agent request and return the next response."""
        self.requests.append(request)
        return self.responses.pop(0)


def _evaluation_model():
    """Build the enabled Resolution model contract used by offline tasks."""
    from restscope.llm import LLMModelConfig

    return LLMModelConfig(
        role="operation_smoke_failure_resolution",
        provider="stub",
        model="stub-model",
        max_tokens=8192,
        context_window_tokens=131072,
    )


def test_registry_contains_only_the_continuous_resolution_suite() -> None:
    """Old Dedup, Solve, and Patch Dataset boundaries are no longer public."""
    from evaluations.registry import SUITES

    assert set(SUITES) == {"resolution"}
    suite = SUITES["resolution"]
    assert suite.dataset_name == "restscope-operation-smoke-resolution"
    scenarios = suite.load_scenarios()
    assert [item.scenario_id for item in scenarios] == [
        "resolution-merge-shared-parameter",
        "resolution-patch-bounded-identifier",
        "resolution-split-distinct-parameters",
    ]
    assert all(
        secret not in item.model_dump_json().lower()
        for item in scenarios
        for secret in ("authorization", "password", "api_key")
    )


def test_resolution_dataset_uses_stable_repository_ids() -> None:
    """Dataset sync mirrors exactly the new single-suite scenario set."""
    from evaluations.core import sync_suite
    from evaluations.registry import SUITES

    class Datasets:
        """Capture the Phoenix create call and return the same examples."""

        def __init__(self) -> None:
            self.calls = []

        def create_dataset(self, **kwargs):
            """Return an exact version so mirror validation can succeed."""
            self.calls.append(kwargs)
            return type(
                "Dataset",
                (),
                {"version_id": "v1", "examples": kwargs["examples"]},
            )()

    datasets = Datasets()
    dataset = sync_suite(
        type("Client", (), {"datasets": datasets})(),
        SUITES["resolution"],
    )

    assert dataset.version_id == "v1"
    assert datasets.calls[0]["name"] == "restscope-operation-smoke-resolution"
    ids = [item["id"] for item in datasets.calls[0]["examples"]]
    assert ids == [
        "resolution-merge-shared-parameter",
        "resolution-patch-bounded-identifier",
        "resolution-split-distinct-parameters",
    ]
    assert all(item["id"] in item["splits"] for item in datasets.calls[0]["examples"])


def test_dataset_sync_rejects_a_retired_dedup_example() -> None:
    """The Resolution Dataset cannot retain a row from an old suite."""
    from evaluations.core import sync_suite
    from evaluations.registry import SUITES

    class StaleDatasets:
        """Return current examples plus one row that should be deleted."""

        def create_dataset(self, **kwargs):
            """Model an incomplete server-side Dataset replacement."""
            stale = {
                "id": "dedup-retired-example",
                "input": {},
                "output": {},
                "metadata": {"agent": "dedup"},
            }
            return type(
                "Dataset",
                (),
                {"version_id": "stale", "examples": [*kwargs["examples"], stale]},
            )()

    with pytest.raises(RuntimeError, match="unexpected example IDs"):
        sync_suite(
            type("Client", (), {"datasets": StaleDatasets()})(),
            SUITES["resolution"],
        )


def test_resolution_task_runs_real_worklist_loop_without_storage() -> None:
    """A scripted model can rewrite and finalize the production worklist."""
    from evaluations.registry import SUITES
    from restscope.llm import LLMResponse, ToolCall
    from restscope.observability import TracingRuntime

    scenario = next(
        item
        for item in SUITES["resolution"].load_scenarios()
        if item.scenario_id == "resolution-merge-shared-parameter"
    )
    client = ScriptedLLMClient(
        [
            LLMResponse(
                provider="stub",
                model="stub-model",
                tool_calls=[
                    ToolCall(
                        id="write-1",
                        name="failure_resolution.write_worklist",
                        arguments={
                            "expected_revision": 0,
                            "active_item_id": "WI-001",
                            "items": [
                                {
                                    "item_id": "WI-001",
                                    "source_failure_refs": ["E1", "E2"],
                                    "test_case_refs": ["TC1", "TC2"],
                                    "suspected_parameters": ["body.name"],
                                    "progress": "Both failures share one input.",
                                    "root_cause": "The names already exist.",
                                    "candidate_refs": [],
                                    "decision": {
                                        "outcome": "no_patch",
                                        "selected_candidate_ref": None,
                                        "reason": "No safe local domain is known.",
                                    },
                                }
                            ],
                        },
                    )
                ],
            ),
            LLMResponse(
                provider="stub",
                model="stub-model",
                parsed_json={"reason": "The reference-only worklist is complete."},
            ),
        ]
    )
    task = SUITES["resolution"].build_task(
        client=client,
        model=_evaluation_model(),
        task_models={},
        tracing_runtime=TracingRuntime.disabled(),
        system_prompt=None,
        seed=17,
    )

    output = task(scenario.input.model_dump(mode="json"))

    assert output["runtime_error"] is None
    assert output["result"]["status"] == "completed"
    assert output["result"]["commit"]["applied_candidate_refs"] == []
    assert output["result"]["worklist"]["items"][0]["source_failure_refs"] == [
        "E1",
        "E2",
    ]
    assert all(
        request.metadata["role"] == "operation_smoke_failure_resolution"
        for request in client.requests
    )


def test_resolution_evaluators_keep_zero_one_and_not_applicable_separate() -> None:
    """Independent code scores preserve Phoenix's explicit 0/1/N/A contract."""
    from evaluations.agents.resolution.suite import (
        item_count_evaluator,
        parameters_evaluator,
        status_evaluator,
    )

    output = {
        "result": {
            "status": "completed",
            "worklist": {
                "items": [{"suspected_parameters": ["body.name"]}],
            },
        },
        "runtime_error": None,
    }
    status = status_evaluator.evaluate(
        {"output": output, "expected": {"status": "completed"}}
    )[0]
    count = item_count_evaluator.evaluate(
        {"output": output, "expected": {"item_count": 2}}
    )[0]
    parameters = parameters_evaluator.evaluate(
        {"output": output, "expected": {}}
    )[0]

    assert (status.score, status.label) == (1, "satisfied")
    assert (count.score, count.label) == (0, "not_satisfied")
    assert parameters.score is None
    assert parameters.label == "not_applicable"


def test_resolution_evaluation_imports_no_database_or_http_transport() -> None:
    """Agent evaluation remains isolated from persistence and target requests."""
    from pathlib import Path

    source = Path("evaluations/agents/resolution/suite.py").read_text(encoding="utf-8")
    assert "restscope.db" not in source
    assert "HTTPProbe" not in source
    assert "requests." not in source


def test_cli_selects_only_the_resolution_model_role() -> None:
    """Developer experiments use the same unified role as production."""
    from evaluations.cli import _NESTED_ROLES, _ROLES, build_parser

    assert _ROLES == {"resolution": "operation_smoke_failure_resolution"}
    assert _NESTED_ROLES == (
        "operation_smoke_failure_resolution_compact",
        "parameter_patch_agent",
        "parameter_patch_review_agent",
    )
    args = build_parser().parse_args(["run", "resolution"])
    assert args.agent == "resolution"
