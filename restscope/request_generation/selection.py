"""Choose positive and negative Generator candidates for one Batch slot.

The Main Agent chooses happy-path or exceptional testing. This module then
performs only deterministic mechanics: separate epsilon-greedy selection for
positive and negative candidates, the exceptional 50/50 action split, and
whole-component Constraint removal. It receives frozen reward statistics and
returns one selected configuration; it performs no I/O or persistence.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Literal, TypeAlias

from .constraints import OperationConstraintRecord, associated_constraint_ids
from .models import (
    InputGeneratorConfig,
    NegativeInputGeneratorConfig,
    OperationGeneratorConfig,
)
from .randomness import SeededRandom


EPSILON = 0.1
CandidateKind: TypeAlias = Literal["positive", "negative"]
BanditKey: TypeAlias = tuple[CandidateKind, str, str]


class TestMode(StrEnum):
    """Name the Main Agent's semantic purpose for one Batch."""

    HAPPY_PATH = "happy_path"
    EXCEPTIONAL = "exceptional"


@dataclass(frozen=True, slots=True)
class RewardStatistics:
    """Keep attempts and accumulated binary rewards for one candidate arm."""

    attempts: int = 0
    rewards: int = 0

    @property
    def mean_reward(self) -> float:
        """Return zero for an untried arm and its empirical mean otherwise."""

        return self.rewards / self.attempts if self.attempts else 0.0


@dataclass(frozen=True, slots=True)
class GeneratorChoice:
    """Identify one selected arm without copying its potentially large value."""

    kind: CandidateKind
    input_node_id: str
    candidate_id: str
    rule: str | None = None


@dataclass(frozen=True, slots=True)
class CaseGeneratorSelection:
    """Return all deterministic choices needed to generate one request slot."""

    config: OperationGeneratorConfig
    constraints: tuple[OperationConstraintRecord, ...]
    action: Literal["happy_path", "negative_generator", "ignored_constraint"]
    positive_choices: tuple[GeneratorChoice, ...]
    negative_choice: GeneratorChoice | None = None
    ignored_constraint_ids: tuple[str, ...] = ()


def choose_case_generators(
    *,
    config: OperationGeneratorConfig,
    constraints: Sequence[OperationConstraintRecord],
    test_mode: TestMode,
    statistics: Mapping[BanditKey, RewardStatistics],
    run_seed: int,
    case_index: int,
) -> CaseGeneratorSelection | None:
    """Choose candidates for one requested slot from a frozen score snapshot.

    Exceptional slots first choose negative-Generator testing or one ignored
    Constraint with equal probability. If that chosen action has no eligible
    candidate, the slot is skipped rather than silently changing its purpose.
    """

    random = SeededRandom(run_seed)
    scope = f"selection:{config.operation_key}:{case_index}"
    action: Literal["happy_path", "negative_generator", "ignored_constraint"]
    negative: NegativeInputGeneratorConfig | None = None
    ignored_ids: tuple[str, ...] = ()

    if test_mode is TestMode.HAPPY_PATH:
        action = "happy_path"
    elif random.boolean(scope=f"{scope}:exceptional-action"):
        action = "negative_generator"
        inputs = sorted({item.input_node_id for item in config.negative_generators})
        if not inputs:
            return None
        negative_input = random.choice(inputs, scope=f"{scope}:negative-input")
        negative = _epsilon_greedy(
            [
                item
                for item in config.negative_generators
                if item.input_node_id == negative_input
            ],
            kind="negative",
            statistics=statistics,
            random=random,
            scope=f"{scope}:negative-candidate:{negative_input}",
        )
        ignored_ids = associated_constraint_ids(constraints, negative_input)
    else:
        action = "ignored_constraint"
        if not constraints:
            return None
        ignored = random.choice(
            list(constraints),
            scope=f"{scope}:ignored-constraint",
        )
        ignored_ids = (ignored.id,)

    positives_by_input: dict[str, list[InputGeneratorConfig]] = defaultdict(list)
    for candidate in config.positive_generators:
        positives_by_input[candidate.input_node_id].append(candidate)

    selected_positive: list[InputGeneratorConfig] = []
    positive_choices: list[GeneratorChoice] = []
    for input_node_id in sorted(positives_by_input):
        if negative is not None and input_node_id == negative.input_node_id:
            continue
        candidate = _epsilon_greedy(
            positives_by_input[input_node_id],
            kind="positive",
            statistics=statistics,
            random=random,
            scope=f"{scope}:positive-candidate:{input_node_id}",
        )
        selected_positive.append(candidate)
        positive_choices.append(_choice("positive", candidate))

    negative_choice = None
    if negative is not None:
        selected_positive.append(
            InputGeneratorConfig(
                input_node_id=negative.input_node_id,
                inclusion_probability=negative.inclusion_probability,
                strategy=negative.strategy,
            )
        )
        negative_choice = _choice("negative", negative)

    ignored = set(ignored_ids)
    return CaseGeneratorSelection(
        config=config.model_copy(
            update={"positive_generators": selected_positive},
            deep=True,
        ),
        constraints=tuple(item for item in constraints if item.id not in ignored),
        action=action,
        positive_choices=tuple(positive_choices),
        negative_choice=negative_choice,
        ignored_constraint_ids=ignored_ids,
    )


def candidate_id(candidate: InputGeneratorConfig) -> str:
    """Return a stable content identity independent of candidate list order."""

    payload = {
        "input_node_id": candidate.input_node_id,
        "inclusion_probability": candidate.inclusion_probability,
        "strategy": candidate.strategy.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def _epsilon_greedy(
    candidates: Sequence[InputGeneratorConfig],
    *,
    kind: CandidateKind,
    statistics: Mapping[BanditKey, RewardStatistics],
    random: SeededRandom,
    scope: str,
) -> InputGeneratorConfig:
    """Select one arm with fixed 0.1 exploration and seeded tie-breaking."""

    ordered = sorted(candidates, key=candidate_id)
    explore = random.number(0.0, 1.0, scope=f"{scope}:epsilon") < EPSILON
    if explore:
        return random.choice(ordered, scope=f"{scope}:explore")
    best_reward = max(
        statistics.get(
            (kind, item.input_node_id, candidate_id(item)),
            RewardStatistics(),
        ).mean_reward
        for item in ordered
    )
    greedy = [
        item
        for item in ordered
        if statistics.get(
            (kind, item.input_node_id, candidate_id(item)),
            RewardStatistics(),
        ).mean_reward
        == best_reward
    ]
    return random.choice(greedy, scope=f"{scope}:greedy-tie")


def _choice(
    kind: CandidateKind,
    candidate: InputGeneratorConfig,
) -> GeneratorChoice:
    """Project a selected candidate into bounded execution metadata."""

    return GeneratorChoice(
        kind=kind,
        input_node_id=candidate.input_node_id,
        candidate_id=candidate_id(candidate),
        rule=(
            candidate.rule
            if isinstance(candidate, NegativeInputGeneratorConfig)
            else None
        ),
    )
