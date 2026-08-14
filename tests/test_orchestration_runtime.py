"""Protect the public long-task Orchestration Interface and its Ledger rules."""

from __future__ import annotations


class _ScriptedSystemAgentRunner:
    """Return complete local decisions while recording every fresh root task."""

    def __init__(self, outputs: list[dict[str, object]]) -> None:
        """Keep ordered structured outputs for one deterministic scenario."""
        self.outputs = list(outputs)
        self.calls: list[tuple[str, object]] = []

    def run_system_agent(self, profile_name: str, task: object):
        """Return the next completed result through the production runner shape."""
        from restscope.agent import AgentUsage, SystemAgentResult

        self.calls.append((profile_name, task))
        return SystemAgentResult(
            session_id=f"agent_{len(self.calls)}",
            profile_name=profile_name,
            status="completed",
            output=self.outputs.pop(0),
            usage=AgentUsage(model_outputs=1),
        )


def test_runtime_replans_dispatches_one_fresh_executor_and_completes() -> None:
    """Each accepted transition publishes exact root-to-Ledger relationships."""
    from restscope.orchestration import OrchestrationRuntime

    runner = _ScriptedSystemAgentRunner(
        [
            {
                "kind": "replan",
                "expected_plan_revision": 0,
                "reason": "Start with one safe discovery milestone.",
                "milestones": [
                    {
                        "title": "Discover pets",
                        "purpose": "Find one reproducible read-only happy path.",
                        "success_criteria": ["A GET operation returns 2xx evidence."],
                    }
                ],
            },
            {
                "kind": "dispatch_task",
                "expected_plan_revision": 1,
                "task": {
                    "milestone_id": "milestone_1",
                    "objective": "Run a happy-path Batch for GET /pets.",
                    "purpose": "Complete the current discovery milestone.",
                    "success_criteria": [
                        {
                            "criterion_id": "criterion_1",
                            "description": "A GET /pets case returns 2xx evidence.",
                        }
                    ],
                    "related_attempt_ids": [],
                },
            },
            {
                "task_id": "task_1",
                "outcome": "completed",
                "criteria": [
                    {
                        "criterion_id": "criterion_1",
                        "status": "met",
                        "explanation": "The Batch returned HTTP 200.",
                        "evidence_refs": ["batch_1"],
                    }
                ],
                "findings": [],
                "unresolved_issues": [],
                "target_state_changes": [],
            },
            {
                "kind": "complete",
                "expected_plan_revision": 1,
                "goal_criteria": [
                    {
                        "criterion_id": "goal_1",
                        "status": "met",
                        "explanation": "The focused read-only exploration completed.",
                    },
                    {
                        "criterion_id": "goal_2",
                        "status": "not_met",
                        "explanation": "Exceptional testing was outside this focus.",
                    },
                    {
                        "criterion_id": "goal_3",
                        "status": "met",
                        "explanation": "No unconfirmed response was reported as a Bug.",
                    },
                ],
                "summary": "The focused happy path was confirmed.",
                "unresolved": ["Exceptional coverage remains."],
            },
        ]
    )

    observations = []
    result = OrchestrationRuntime(runner, observe=observations.append).run(
        focus="Prefer read-only operations."
    )

    assert [profile for profile, _task in runner.calls] == [
        "orchestrator",
        "orchestrator",
        "task-executor",
        "orchestrator",
    ]
    assert result.summary == "The focused happy path was confirmed."
    assert result.goal.focus == "Prefer read-only operations."
    assert result.ledger.plan_revision == 1
    assert result.ledger.run_status == "completed"
    assert len(result.ledger.attempts) == 1
    assert result.ledger.attempts[0].task_id == "task_1"
    assert result.ledger.attempts[0].outcome == "completed"
    assert [item.revision for item in observations] == [1, 2, 3, 4, 5]
    assert observations[-1].goal == result.goal
    assert observations[-1].ledger == result.ledger
    assert [
        (
            item.session_id,
            item.profile_name,
            item.role,
            item.sequence,
            item.decision_kind,
            item.task_id,
            item.attempt_id,
        )
        for item in observations[-1].sessions
    ] == [
        ("agent_1", "orchestrator", "orchestrator", 1, "replan", None, None),
        (
            "agent_2",
            "orchestrator",
            "orchestrator",
            2,
            "dispatch_task",
            "task_1",
            None,
        ),
        (
            "agent_3",
            "task-executor",
            "task_executor",
            1,
            None,
            "task_1",
            "attempt_1",
        ),
        ("agent_4", "orchestrator", "orchestrator", 3, "complete", None, None),
    ]

    # Every call owns a fresh registered root; the outer runtime never feeds a
    # previous Agent transcript into a later task.
    assert len({call[1].objective for call in runner.calls}) == 4


def test_executor_lifecycle_failure_is_recorded_before_replan() -> None:
    """A failed Task Executor root returns through an immutable Attempt."""
    from restscope.agent import AgentError, AgentUsage, SystemAgentResult
    from restscope.orchestration import OrchestrationRuntime

    class Runner(_ScriptedSystemAgentRunner):
        """Return one lifecycle failure between valid outer decisions."""

        def run_system_agent(self, profile_name: str, task: object):
            """Inject the failure without bypassing normal call recording."""
            if profile_name == "task-executor":
                self.calls.append((profile_name, task))
                return SystemAgentResult(
                    session_id="failed_executor",
                    profile_name=profile_name,
                    status="failed",
                    error=AgentError(code="provider_failed", message="Provider stopped."),
                    usage=AgentUsage(),
                )
            return super().run_system_agent(profile_name, task)

    runner = Runner(
        [
            {
                "kind": "replan",
                "expected_plan_revision": 0,
                "reason": "Create work.",
                "milestones": [
                    {
                        "title": "Probe pets",
                        "purpose": "Collect evidence.",
                        "success_criteria": ["One result is recorded."],
                    }
                ],
            },
            {
                "kind": "dispatch_task",
                "expected_plan_revision": 1,
                "task": {
                    "milestone_id": "milestone_1",
                    "objective": "Probe GET /pets.",
                    "purpose": "Collect evidence.",
                    "success_criteria": [
                        {
                            "criterion_id": "criterion_1",
                            "description": "One result is recorded.",
                        }
                    ],
                },
            },
            {
                "kind": "complete",
                "expected_plan_revision": 1,
                "goal_criteria": [
                    {
                        "criterion_id": f"goal_{index}",
                        "status": "unknown",
                        "explanation": "Task Executor lifecycle failed.",
                    }
                    for index in range(1, 4)
                ],
                "summary": "The run stopped with explicit unresolved work.",
                "unresolved": ["Retry with a recovered provider."],
            },
        ]
    )

    observations = []
    result = OrchestrationRuntime(runner, observe=observations.append).run()

    assert result.ledger.attempts[0].outcome == "failed"
    assert result.ledger.attempts[0].failure_code == "provider_failed"
    assert [name for name, _task in runner.calls] == [
        "orchestrator",
        "orchestrator",
        "task-executor",
        "orchestrator",
    ]
    failed_session = observations[-2].sessions[-1]
    assert failed_session.session_id == "failed_executor"
    assert failed_session.task_id == "task_1"
    assert failed_session.attempt_id == "attempt_1"
    assert failed_session.status == "failed"


def test_observation_failure_never_changes_orchestration_result() -> None:
    """The optional UI projection remains fail-open for the testing runtime."""
    from restscope.orchestration import OrchestrationRuntime

    runner = _ScriptedSystemAgentRunner(
        [
            {
                "kind": "replan",
                "expected_plan_revision": 0,
                "reason": "Create one bounded milestone.",
                "milestones": [
                    {
                        "title": "Inspect health",
                        "purpose": "Keep the run valid while observation fails.",
                        "success_criteria": ["The milestone is explicit."],
                    }
                ],
            },
            {
                "kind": "complete",
                "expected_plan_revision": 1,
                "goal_criteria": [
                    {
                        "criterion_id": f"goal_{index}",
                        "status": "unknown",
                        "explanation": "This scenario protects observation isolation.",
                    }
                    for index in range(1, 4)
                ],
                "summary": "Observation failure did not stop orchestration.",
            },
        ]
    )

    def fail_observation(_observation: object) -> None:
        raise RuntimeError("observer unavailable")

    result = OrchestrationRuntime(runner, observe=fail_observation).run()

    assert result.summary == "Observation failure did not stop orchestration."
    assert result.ledger.run_status == "completed"


def test_next_orchestrator_call_receives_the_task_attempt_causal_chain() -> None:
    """The next decision sees why work ran and what it actually established."""
    from restscope.orchestration import OrchestrationRuntime

    runner = _ScriptedSystemAgentRunner(
        [
            {
                "kind": "replan",
                "expected_plan_revision": 0,
                "reason": "Prioritize one known pet happy path before errors.",
                "milestones": [
                    {
                        "title": "Confirm pets",
                        "purpose": "Establish a reproducible happy path.",
                        "success_criteria": ["GET /pets returns 2xx."],
                    }
                ],
            },
            {
                "kind": "dispatch_task",
                "expected_plan_revision": 1,
                "task": {
                    "milestone_id": "milestone_1",
                    "objective": "Probe GET /pets with a known identifier.",
                    "purpose": "Confirm the pet milestone before exceptional testing.",
                    "success_criteria": [
                        {
                            "criterion_id": "criterion_1",
                            "description": "The known pet request returns 2xx.",
                        }
                    ],
                },
            },
            {
                "task_id": "task_1",
                "outcome": "partial",
                "criteria": [
                    {
                        "criterion_id": "criterion_1",
                        "status": "unknown",
                        "explanation": "The identifier source was empty.",
                    }
                ],
                "unresolved_issues": ["A valid pet identifier is still required."],
            },
            {
                "kind": "complete",
                "expected_plan_revision": 1,
                "goal_criteria": [
                    {
                        "criterion_id": f"goal_{index}",
                        "status": "unknown",
                        "explanation": "The focused attempt remained unresolved.",
                    }
                    for index in range(1, 4)
                ],
                "summary": "The run retained its unresolved causal evidence.",
            },
        ]
    )

    OrchestrationRuntime(runner).run()

    next_decision = runner.calls[-1][1].objective
    assert "Prioritize one known pet happy path before errors." in next_decision
    assert "Probe GET /pets with a known identifier." in next_decision
    assert "Confirm the pet milestone before exceptional testing." in next_decision
    assert "The identifier source was empty." in next_decision
    assert "A valid pet identifier is still required." in next_decision


def test_orchestrator_contract_rejects_an_oversized_future_plan() -> None:
    """A large rolling plan receives correction before it can reach the Ledger."""
    from restscope.agent import SystemAgentTask
    from restscope.orchestration.contracts import validate_orchestrator_output
    from restscope.orchestration.models import OrchestratorDecision

    output = OrchestratorDecision.model_validate(
        {
            "kind": "replan",
            "expected_plan_revision": 0,
            "reason": "Create an intentionally oversized future plan.",
            "milestones": [
                {
                    "title": f"Milestone {index}",
                    "purpose": "p" * 1_500,
                    "success_criteria": ["One bounded result is required."],
                }
                for index in range(3)
            ],
        }
    )
    task = SystemAgentTask(
        objective="Choose the first plan.",
        allowed_result_aliases=("revision_0", "goal_1", "goal_2", "goal_3"),
    )

    assert validate_orchestrator_output(output, task) == (
        "Replan future work text must not exceed 4000 characters.",
    )


def test_orchestration_contracts_reject_oversized_task_and_execution_text() -> None:
    """Task prompts and results stay small enough for the required context."""
    from restscope.agent import SystemAgentTask
    from restscope.orchestration.contracts import (
        validate_orchestrator_output,
        validate_task_execution_output,
    )
    from restscope.orchestration.models import OrchestratorDecision, TaskExecutionResult

    task_decision = OrchestratorDecision.model_validate(
        {
            "kind": "dispatch_task",
            "expected_plan_revision": 1,
            "task": {
                "milestone_id": "milestone_1",
                "objective": "o" * 3_000,
                "purpose": "p" * 1_500,
                "success_criteria": [
                    {
                        "criterion_id": "criterion_1",
                        "description": "One result is required.",
                    }
                ],
            },
        }
    )
    orchestrator_task = SystemAgentTask(
        objective="Choose one task.",
        allowed_result_aliases=(
            "revision_1",
            "goal_1",
            "goal_2",
            "goal_3",
            "milestone_1",
        ),
    )
    assert validate_orchestrator_output(task_decision, orchestrator_task) == (
        "Dispatched Task text must not exceed 4000 characters.",
    )

    result = TaskExecutionResult.model_validate(
        {
            "task_id": "task_1",
            "outcome": "partial",
            "criteria": [
                {
                    "criterion_id": f"criterion_{index}",
                    "status": "unknown",
                    "explanation": "e" * 3_000,
                }
                for index in range(1, 3)
            ],
        }
    )
    executor_task = SystemAgentTask(
        objective="Execute one task.",
        allowed_result_aliases=("task_1", "criterion_1", "criterion_2"),
    )
    assert validate_task_execution_output(result, executor_task) == (
        "Task execution result text must not exceed 6000 characters.",
    )


def test_hundred_execution_rounds_keep_each_prompt_projection_bounded() -> None:
    """Complete history stays in the Ledger while model inputs remain rolling."""
    from restscope.orchestration import OrchestrationRuntime

    outputs: list[dict[str, object]] = []
    for index in range(1, 101):
        outputs.extend(
            [
                {
                    "kind": "replan",
                    "expected_plan_revision": index - 1,
                    "reason": f"Open bounded milestone {index}.",
                    "milestones": [
                        {
                            "title": f"Milestone {index}",
                            "purpose": "Exercise one bounded operation.",
                            "success_criteria": ["One criterion is met."],
                        }
                    ],
                },
                {
                    "kind": "dispatch_task",
                    "expected_plan_revision": index,
                    "task": {
                        "milestone_id": f"milestone_{index}",
                        "objective": (
                            f"Execute bounded task {index}. " + "task-context-" * 55
                        ),
                        "purpose": "Exercise one bounded operation.",
                        "success_criteria": [
                            {
                                "criterion_id": "criterion_1",
                                "description": "One criterion is met.",
                            }
                        ],
                    },
                },
                {
                    "task_id": f"task_{index}",
                    "outcome": "completed",
                    "criteria": [
                        {
                            "criterion_id": "criterion_1",
                            "status": "met",
                            "explanation": (
                                f"Scripted evidence satisfied task {index}. "
                                + "result-context-" * 50
                            ),
                        }
                    ],
                },
            ]
        )
    outputs.append(
        {
            "kind": "complete",
            "expected_plan_revision": 100,
            "goal_criteria": [
                {
                    "criterion_id": f"goal_{index}",
                    "status": "met",
                    "explanation": "The scripted long run finished.",
                }
                for index in range(1, 4)
            ],
            "summary": "One hundred bounded rounds completed.",
        }
    )
    runner = _ScriptedSystemAgentRunner(outputs)

    result = OrchestrationRuntime(runner).run()

    assert len(result.ledger.attempts) == 100
    assert result.ledger.plan_revision == 100
    assert max(len(task.objective) for _profile, task in runner.calls) <= 18_000
    final_prompt = runner.calls[-1][1].objective
    assert "Return exactly one replan, dispatch_task, or complete" in final_prompt
    assert "Open bounded milestone 100." in final_prompt
    assert "Execute bounded task 100." in final_prompt
    assert "Execute bounded task 99." in final_prompt
    assert "Execute bounded task 81." not in final_prompt
    assert "optional history records omitted" in final_prompt
