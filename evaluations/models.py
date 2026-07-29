"""Small shared data shapes used by the Phoenix evaluation runner.

The shared Module knows how to identify and upload examples, but it deliberately
does not understand Failure, Investigation, Generator, or Constraint concepts.
Those terms remain inside the Plan, Solve, and Patch suites.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ScenarioProvenance(BaseModel):
    """Explain why a curated scenario exists.

    ``trace`` scenarios name the ignored local export and original trace/span
    identifiers.  ``manual`` scenarios instead give a rationale so future
    maintainers can distinguish real-run evidence from a designed edge case.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["trace", "manual"]
    source: str = Field(min_length=1)
    trace_id: str | None = Field(default=None, min_length=1)
    span_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)


class DatasetExample(BaseModel):
    """Represent one Phoenix Dataset example without importing Phoenix."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scenario_id: str = Field(min_length=1)
    input: dict[str, Any]
    expected: dict[str, Any]
    metadata: dict[str, Any]
    splits: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class EvaluationSuite:
    """Describe the small Interface the shared runner needs from one suite.

    The shared runner deliberately receives ready-made callbacks instead of
    understanding the three Agents.  A suite owns its temporary collaborators,
    prompt text, and semantic scores; this object only gives Phoenix a uniform
    way to start them.
    """

    agent_name: str
    dataset_name: str
    scenario_directory: Path
    scenario_model: type[BaseModel]
    to_example: Callable[[BaseModel], DatasetExample]
    build_task: Callable[..., Callable[..., dict[str, Any]]] | None = None
    evaluators: tuple[Any, ...] = ()
    current_prompt: Callable[[], str] | None = None

    def load_scenarios(self) -> list[BaseModel]:
        """Load and validate every YAML scenario in stable ID order."""
        scenarios: list[BaseModel] = []
        for path in sorted(self.scenario_directory.glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            scenarios.append(self.scenario_model.model_validate(raw))
        return sorted(scenarios, key=lambda item: getattr(item, "scenario_id"))
