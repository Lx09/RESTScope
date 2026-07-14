"""Replaceable stage strategies for operation-level testing."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OperationTestStage:
    """Schemathesis run shape for one logical test stage."""

    name: str
    phases: list[str]
    generation_modes: list[str]
    checks: list[str] = field(default_factory=list)
    max_examples_override: int | None = None


def default_operation_test_stages() -> list[OperationTestStage]:
    """Return the MVP operation test stage order."""

    return [
        OperationTestStage(
            name="smoke",
            phases=["fuzzing"],
            generation_modes=["positive"],
            max_examples_override=1,
        ),
        OperationTestStage(
            name="conformance",
            phases=["coverage", "fuzzing"],
            generation_modes=["positive"],
            checks=["status_code_conformance", "content_type_conformance", "response_schema_conformance"],
        ),
        OperationTestStage(
            name="positive",
            phases=["fuzzing"],
            generation_modes=["positive"],
        ),
        OperationTestStage(
            name="negative",
            phases=["fuzzing"],
            generation_modes=["negative"],
        ),
        OperationTestStage(
            name="boundary",
            phases=["fuzzing"],
            generation_modes=["positive", "negative"],
        ),
    ]
