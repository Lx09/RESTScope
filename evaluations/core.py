"""Phoenix Dataset and Experiment plumbing shared by Agent suites.

This Module hides the repetitive Phoenix client calls.  It receives already
validated, Agent-owned scenarios and never interprets their domain input,
expected output, or scores.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess
from typing import Any

from evaluations.models import EvaluationSuite


def sync_suite(client: Any, suite: EvaluationSuite) -> Any:
    """Mirror one suite's repository scenarios into its Phoenix Dataset.

    Stable scenario IDs let Phoenix update, add, or remove examples while older
    Experiments remain attached to the Dataset version they actually used.

    Args:
        client: A configured ``phoenix.client.Client`` or compatible test fake.
        suite: The registered Agent suite to validate and upload.

    Returns:
        Phoenix's Dataset object for the newly synchronized version.

    Raises:
        Validation or Phoenix client errors unchanged, because either means the
        Experiment cannot reliably start.
    """
    examples = []
    for scenario in suite.load_scenarios():
        example = suite.to_example(scenario)
        examples.append(
            {
                "id": example.scenario_id,
                "input": example.input,
                "output": example.expected,
                "metadata": example.metadata,
                "splits": example.splits,
            }
        )
    return client.datasets.create_dataset(
        name=suite.dataset_name,
        examples=examples,
        dataset_description=(
            f"Version-controlled isolated evaluation scenarios for "
            f"RESTScope {suite.agent_name} Agent."
        ),
    )


@dataclass(frozen=True)
class PromptSelection:
    """Carry one named complete prompt and its production override behavior."""

    name: str
    text: str
    sha256: str
    system_prompt_override: str | None


def resolve_prompt(suite: EvaluationSuite, prompt_name: str) -> PromptSelection:
    """Resolve ``current`` or one suite-local complete prompt text file.

    Candidate names are plain file stems, not arbitrary paths.  This prevents a
    command typo from reading another repository file into a model request.
    """
    if suite.current_prompt is None:
        raise ValueError(f"{suite.agent_name} does not expose its current prompt")
    if prompt_name == "current":
        text = suite.current_prompt()
        override = None
    else:
        if (
            not prompt_name
            or not prompt_name.replace("-", "_").isalnum()
            or "/" in prompt_name
            or "\\" in prompt_name
        ):
            raise ValueError("prompt variant must be a simple file name")
        path = (
            suite.scenario_directory.parent
            / "prompts"
            / f"{prompt_name}.txt"
        )
        if not path.is_file():
            raise ValueError(
                f"Unknown {suite.agent_name} prompt variant: {prompt_name}"
            )
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(f"Prompt variant is empty: {path}")
        override = text
    return PromptSelection(
        name=prompt_name,
        text=text,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        system_prompt_override=override,
    )


def prompt_names(suite: EvaluationSuite) -> list[str]:
    """List the production prompt followed by repository candidate variants."""
    directory = suite.scenario_directory.parent / "prompts"
    candidates = (
        sorted(path.stem for path in directory.glob("*.txt"))
        if directory.is_dir()
        else []
    )
    return ["current", *candidates]


def current_git_revision(repo_root: Path) -> str:
    """Return the checked-out revision without changing repository state."""
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def run_suite(
    *,
    phoenix_client: Any,
    suite: EvaluationSuite,
    llm_client: Any,
    model: Any,
    tracing_runtime: Any,
    prompt_name: str,
    repetitions: int,
    seed: int,
    git_revision: str,
    scenario_id: str | None = None,
) -> Any:
    """Synchronize a Dataset, select examples, and run one Phoenix Experiment.

    Semantic low scores and a task ``runtime_error`` are persisted experiment
    results, so this function returns normally for both.  Configuration,
    Dataset, Phoenix, and Experiment transport failures remain exceptions and
    therefore make the CLI exit non-zero.
    """
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    if seed < 0:
        raise ValueError("seed must be non-negative")
    if suite.build_task is None:
        raise ValueError(f"{suite.agent_name} does not define an experiment task")

    scenarios = suite.load_scenarios()
    scenario_ids = {item.scenario_id for item in scenarios}
    if scenario_id is not None and scenario_id not in scenario_ids:
        raise ValueError(
            f"Unknown {suite.agent_name} scenario: {scenario_id}"
        )

    synchronized = sync_suite(phoenix_client, suite)
    dataset = synchronized
    if scenario_id is not None:
        # A split named after every stable scenario ID is uploaded during sync.
        # Filtering the just-created version preserves exact provenance while
        # allowing a cheap one-example exploratory run.
        dataset = phoenix_client.datasets.get_dataset(
            dataset=suite.dataset_name,
            version_id=synchronized.version_id,
            splits=[scenario_id],
        )

    prompt = resolve_prompt(suite, prompt_name)
    task = suite.build_task(
        client=llm_client,
        model=model,
        tracing_runtime=tracing_runtime,
        system_prompt=prompt.system_prompt_override,
        seed=seed,
    )
    metadata = {
        "agent": suite.agent_name,
        "model": model.model_dump(mode="json"),
        "prompt_name": prompt.name,
        "prompt_sha256": prompt.sha256,
        "git_revision": git_revision,
        "dataset_version": synchronized.version_id,
        "seed": seed,
        "repetitions": repetitions,
    }
    short_revision = git_revision[:8] or "unknown"
    return phoenix_client.experiments.run_experiment(
        dataset=dataset,
        task=task,
        evaluators=list(suite.evaluators),
        experiment_name=(
            f"restscope-{suite.agent_name}-{prompt.name}-{short_revision}"
        ),
        experiment_description=(
            f"Isolated {suite.agent_name} Agent evaluation from repository "
            "scenarios."
        ),
        experiment_metadata=metadata,
        repetitions=repetitions,
        timeout=300,
    )
