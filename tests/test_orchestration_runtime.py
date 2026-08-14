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


def test_runtime_replans_dispatches_one_fresh_worker_and_completes() -> None:
    """One bounded Worker result returns to the outer Ledger before completion."""
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

    result = OrchestrationRuntime(runner).run(focus="Prefer read-only operations.")

    assert [profile for profile, _task in runner.calls] == [
        "orchestrator",
        "orchestrator",
        "main-worker",
        "orchestrator",
    ]
    assert result.summary == "The focused happy path was confirmed."
    assert result.goal.focus == "Prefer read-only operations."
    assert result.ledger.plan_revision == 1
    assert result.ledger.run_status == "completed"
    assert len(result.ledger.attempts) == 1
    assert result.ledger.attempts[0].task_id == "task_1"
    assert result.ledger.attempts[0].outcome == "completed"

    # Every call owns a fresh registered root; the outer runtime never feeds a
    # previous Agent transcript into a later task.
    assert len({call[1].objective for call in runner.calls}) == 4


def test_worker_lifecycle_failure_is_recorded_before_replan() -> None:
    """A failed Worker root returns control through an immutable Attempt."""
    from restscope.agent import AgentError, AgentUsage, SystemAgentResult
    from restscope.orchestration import OrchestrationRuntime

    class Runner(_ScriptedSystemAgentRunner):
        """Return one lifecycle failure between valid outer decisions."""

        def run_system_agent(self, profile_name: str, task: object):
            """Inject the failure without bypassing normal call recording."""
            if profile_name == "main-worker":
                self.calls.append((profile_name, task))
                return SystemAgentResult(
                    session_id="failed_worker",
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
                        "explanation": "Worker lifecycle failed.",
                    }
                    for index in range(1, 4)
                ],
                "summary": "The run stopped with explicit unresolved work.",
                "unresolved": ["Retry with a recovered provider."],
            },
        ]
    )

    result = OrchestrationRuntime(runner).run()

    assert result.ledger.attempts[0].outcome == "failed"
    assert result.ledger.attempts[0].failure_code == "provider_failed"
    assert [name for name, _task in runner.calls] == [
        "orchestrator",
        "orchestrator",
        "main-worker",
        "orchestrator",
    ]


def test_hundred_worker_rounds_keep_each_prompt_projection_bounded() -> None:
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
                        "objective": f"Execute bounded task {index}.",
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
                            "explanation": "Scripted evidence satisfied it.",
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
