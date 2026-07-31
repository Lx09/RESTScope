"""Developer CLI for Phoenix-backed Operation Smoke Agent evaluations.

``list`` is local and read-only. ``sync`` writes repository scenarios to
Phoenix. ``run`` first synchronizes the selected Dataset, then calls real
DeepSeek through RESTScope's configured LLM client and stores Experiment
outputs, code scores, and linked traces in Phoenix.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from evaluations.core import (
    current_git_revision,
    prompt_names,
    run_suite,
    sync_suite,
)
from evaluations.registry import SUITES


_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROLES = {
    "dedup": "operation_smoke_failure_dedup",
    "solve": "operation_smoke_failure_solve",
    "patch": "parameter_patch_agent",
}


def build_parser() -> argparse.ArgumentParser:
    """Describe the intentionally small evaluation command surface."""
    parser = argparse.ArgumentParser(
        prog="python -m evaluations",
        description="Run isolated Operation Smoke Agent Phoenix Evals.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List suites, scenarios, and prompt names.")

    sync = commands.add_parser("sync", help="Mirror scenarios to Phoenix.")
    sync.add_argument(
        "agent",
        nargs="?",
        choices=[*SUITES, "all"],
        default="all",
    )

    run = commands.add_parser("run", help="Run one Phoenix Experiment.")
    run.add_argument("agent", choices=SUITES)
    run.add_argument("--scenario")
    run.add_argument("--prompt", default="current")
    run.add_argument("--repetitions", type=int, default=1)
    run.add_argument("--seed", type=int, default=0)
    return parser


def _phoenix_base_url(endpoint: str) -> str:
    """Convert an OTLP HTTP endpoint into Phoenix's HTTP API base URL."""
    normalized = endpoint.rstrip("/")
    for suffix in ("/v1/traces", "/v1"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    return normalized


def _client(config):
    """Construct the official Phoenix client from existing tracing settings.

    A local Phoenix server must not pass through a developer's HTTP proxy.
    ``httpx`` otherwise honors proxy environment variables and can turn a
    healthy loopback service into a misleading 502 response. Remote Phoenix
    endpoints retain normal environment-proxy behavior.
    """
    import httpx
    from phoenix.client import Client

    base_url = _phoenix_base_url(config.tracing.collector_endpoint)
    hostname = urlparse(base_url).hostname
    http_client = (
        httpx.Client(base_url=base_url, trust_env=False)
        if hostname in {"localhost", "127.0.0.1", "::1"}
        else None
    )
    return Client(
        base_url=base_url,
        api_key=config.tracing.api_key or None,
        http_client=http_client,
    )


def _print_registry() -> None:
    """Print stable identifiers that can be copied into sync/run commands."""
    for name, suite in SUITES.items():
        print(f"{name}:")
        print(f"  dataset: {suite.dataset_name}")
        print(f"  prompts: {', '.join(prompt_names(suite))}")
        for scenario in suite.load_scenarios():
            print(f"  - {scenario.scenario_id}: {scenario.title}")


def _require_configured_model(llm_client, model) -> None:
    """Fail before Dataset/Experiment work when a model role is unusable.

    Agent tasks intentionally turn individual runtime exceptions into
    evaluation data. A missing model name or provider registration is different:
    it is a global configuration error that would invalidate every example, so
    the CLI must exit non-zero before creating an Experiment.
    """
    if not model.enabled:
        raise RuntimeError(
            f"The {model.role} evaluation model is not configured"
        )
    # ``get`` raises the existing provider-specific configuration exception.
    # Reusing that boundary keeps API keys out of this CLI and its error text.
    llm_client.registry.get(model.provider)


def main(argv: Sequence[str] | None = None) -> int:
    """Execute a local listing, Dataset sync, or real Agent Experiment."""
    args = build_parser().parse_args(argv)
    if args.command == "list":
        _print_registry()
        return 0

    from restscope.restscope_config import RESTScopeConfig

    config = RESTScopeConfig.from_environment()
    phoenix_client = _client(config)
    if args.command == "sync":
        selected = SUITES if args.agent == "all" else {args.agent: SUITES[args.agent]}
        for name, suite in selected.items():
            dataset = sync_suite(phoenix_client, suite)
            print(f"{name}: synchronized Dataset version {dataset.version_id}")
        return 0

    # Experiment runs always enable the existing bounded/redacted tracing
    # backend.  Phoenix's own task span becomes the parent of RESTScope's CHAIN,
    # AGENT, LLM, and TOOL spans, so every Experiment run links to one trace.
    from restscope.llm import ModelSelector, build_llm_client
    from restscope.observability import build_tracing_runtime

    tracing_config = replace(
        config.tracing,
        enabled=True,
        project_name=f"restscope-evals-{args.agent}",
    )
    tracing_runtime = build_tracing_runtime(tracing_config)
    try:
        llm_client = build_llm_client(
            config.llm,
            tracing_runtime=tracing_runtime,
        )
        model = ModelSelector.from_config(config.llm).select(
            _ROLES[args.agent]
        )
        _require_configured_model(llm_client, model)
        experiment = run_suite(
            phoenix_client=phoenix_client,
            suite=SUITES[args.agent],
            llm_client=llm_client,
            model=model,
            tracing_runtime=tracing_runtime,
            prompt_name=args.prompt,
            repetitions=args.repetitions,
            seed=args.seed,
            git_revision=current_git_revision(_REPO_ROOT),
            scenario_id=args.scenario,
        )
    finally:
        # Flush batched spans even when Phoenix or DeepSeek rejects a request.
        tracing_runtime.close()

    print(
        "Experiment stored:",
        experiment["experiment_id"],
        "Dataset version:",
        experiment["dataset_version_id"],
    )
    return 0
