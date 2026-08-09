"""Opt-in live DeepSeek FAST test for the Resource Identifier Tracker."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_RESOURCE_MONITOR_LIVE") != "1",
        reason=(
            "Set RUN_RESOURCE_MONITOR_LIVE=1 to call the configured DeepSeek "
            "FAST model."
        ),
    ),
]


def test_live_deepseek_fast_classifies_batched_synthetic_resources(
    tmp_path: Path,
) -> None:
    """Classify two resources and reject summary metadata in one observation."""

    try:
        from restscope.api_behavior_monitor.resource_identifiers.schemas import (
            DetectedResourceGroup,
            MonitoredOperation,
            ResourceObservation,
        )
        from restscope.api_behavior_monitor.resource_identifiers.catalog import ResourceCatalog
        from restscope.api_behavior_monitor import ResourceLookupRequest
        from restscope.api_behavior_monitor.resource_identifiers.tracker import ResourceIdentifierTracker
        from restscope.db import (
            Base,
            SqlAlchemyResourceCatalogUnitOfWork,
            create_engine_from_config,
            make_session_factory,
        )
        from restscope.llm import ModelSelector, build_llm_client
        from restscope.observability import TracingRuntime
        from restscope.observability import Redactor
        from restscope.config import RESTScopeConfig
    except Exception:
        pytest.fail(
            "Could not import Resource Identifier Tracker live runtime components.",
            pytrace=False,
        )

    redactor = Redactor()
    try:
        config = RESTScopeConfig.from_environment(PROJECT_ROOT / ".env")
    except Exception:
        pytest.fail(
            "Could not load Resource Identifier Tracker live runtime configuration.",
            pytrace=False,
        )

    engine = None
    runtime = None
    try:
        try:
            redactor.register_secrets(
                (
                    config.llm.thinking.api_key,
                    config.llm.fast.api_key,
                )
            )
            config = replace(
                config,
                db=replace(
                    config.db,
                    url=f"sqlite:///{tmp_path / 'resource-monitor-live.sqlite'}",
                    echo=False,
                ),
            )
            fast = config.llm.fast
            if fast.provider != "deepseek":
                pytest.fail(
                    "Runtime configuration must select DeepSeek for the FAST model.",
                    pytrace=False,
                )
            if not fast.model:
                pytest.fail(
                    "Runtime configuration must configure a DeepSeek FAST model.",
                    pytrace=False,
                )
            if not fast.api_key:
                pytest.fail(
                    "Runtime configuration must configure a DeepSeek FAST API key.",
                    pytrace=False,
                )
            if fast.reasoning_mode != "disabled":
                pytest.fail(
                    "Runtime configuration must disable reasoning for the FAST model.",
                    pytrace=False,
                )

            official_base_url = "https://api.deepseek.com"
            fast_base_url = (fast.base_url or official_base_url).rstrip("/")
            if fast_base_url != official_base_url:
                pytest.fail(
                    "Runtime FAST DeepSeek base URL must use the official endpoint.",
                    pytrace=False,
                )
            deepseek_slots = [
                slot
                for slot in (config.llm.thinking, fast)
                if slot.provider == "deepseek" and slot.api_key
            ]
            registry_selected_slot = deepseek_slots[0]
            if registry_selected_slot.api_key != fast.api_key:
                pytest.fail(
                    "Registry-selected DeepSeek credentials must match FAST.",
                    pytrace=False,
                )
            registry_base_url = (
                registry_selected_slot.base_url or official_base_url
            ).rstrip("/")
            if registry_base_url != fast_base_url:
                pytest.fail(
                    "Registry-selected DeepSeek base URL must match FAST.",
                    pytrace=False,
                )

            engine = create_engine_from_config(config.db)
            Base.metadata.create_all(engine)
            session_factory = make_session_factory(engine)
            catalog = ResourceCatalog(
                lambda: SqlAlchemyResourceCatalogUnitOfWork(session_factory)
            )
            runtime = TracingRuntime.disabled(redactor=redactor)
            llm_client = build_llm_client(
                config.llm,
                tracing_runtime=runtime,
            )
            deepseek_provider = llm_client.registry.get("deepseek")
            if deepseek_provider.api_key != fast.api_key:
                pytest.fail(
                    "Registered DeepSeek credentials must match FAST.",
                    pytrace=False,
                )
            provider_base_url = (
                deepseek_provider.base_url or official_base_url
            ).rstrip("/")
            if provider_base_url != fast_base_url:
                pytest.fail(
                    "Registered DeepSeek base URL must match FAST.",
                    pytrace=False,
                )
            model = ModelSelector.from_config(config.llm).select(
                "resource_monitor"
            )
            tracker = ResourceIdentifierTracker(
                catalog=catalog,
                client=llm_client,
                model=model,
                tracing_runtime=runtime,
            )
            if tracker.model.role != "api_behavior_monitor":
                pytest.fail(
                    "Resource Identifier Tracker must select the "
                    "resource_monitor model role.",
                    pytrace=False,
                )
            if tracker.model.provider != fast.provider:
                pytest.fail(
                    "Resource Identifier Tracker model provider must match FAST.",
                    pytrace=False,
                )
            if tracker.model.model != fast.model:
                pytest.fail(
                    "Resource Identifier Tracker model name must match FAST.",
                    pytrace=False,
                )

            tracker.catalog.record_groups(
                operation=MonitoredOperation(
                    operation_key="POST /accounts",
                    method="POST",
                    path="/accounts",
                ),
                groups=[
                    DetectedResourceGroup(
                        group_path="$",
                        resource_name="user",
                        resource_aliases=["account"],
                        id_field_name="id",
                        id_selector="$.id",
                        identifier_values=[1],
                        classification_source="exact_id",
                    )
                ],
            )

            result = tracker.observe(
                ResourceObservation(
                    operation=MonitoredOperation(
                        operation_key="POST /sync",
                        method="POST",
                        path="/sync",
                    ),
                    status_code=200,
                    media_type="application/json",
                    body={
                        "commit": {
                            "sha": "safe-commit-sha",
                            "message": "Synthetic synchronization commit",
                        },
                        "owner": {
                            "user_key": 42,
                            "display_name": "Synthetic User",
                        },
                        "summary": {
                            "status": "ok",
                            "count": 2,
                        },
                    },
                    response_schema_fields=[
                        {
                            "selector": "$.commit.sha",
                            "name": "sha",
                            "type": "string",
                            "description": "Unique commit hash",
                        },
                        {
                            "selector": "$.commit.message",
                            "name": "message",
                            "type": "string",
                            "description": "Synthetic commit message",
                        },
                        {
                            "selector": "$.owner.user_key",
                            "name": "user_key",
                            "type": "integer",
                            "description": "Unique user account key",
                        },
                        {
                            "selector": "$.owner.display_name",
                            "name": "display_name",
                            "type": "string",
                            "description": "Synthetic user display name",
                        },
                        {
                            "selector": "$.summary.status",
                            "name": "status",
                            "type": "string",
                            "description": "Synchronization summary status",
                        },
                        {
                            "selector": "$.summary.count",
                            "name": "count",
                            "type": "integer",
                            "description": (
                                "Number of items in the synchronization summary"
                            ),
                        },
                    ],
                )
            )

            if result.status != "updated":
                pytest.fail(
                    "Resource Identifier Tracker result must have status=updated.",
                    pytrace=False,
                )
            if result.groups_processed != 2:
                pytest.fail(
                    "Resource Identifier Tracker must process exactly two "
                    "resource groups.",
                    pytrace=False,
                )
            if result.identifiers_recorded != 2:
                pytest.fail(
                    "Resource Identifier Tracker must record exactly two identifiers.",
                    pytrace=False,
                )

            commit = tracker.lookup(ResourceLookupRequest(resource="commit"))
            if commit.status != "found":
                pytest.fail("Commit lookup must have status=found.", pytrace=False)
            if commit.canonical_resource != "commit":
                pytest.fail(
                    "Commit lookup must resolve canonical_resource=commit.",
                    pytrace=False,
                )
            if commit.recommended_id != "safe-commit-sha":
                pytest.fail(
                    "Commit lookup must recommend the synthetic safe SHA.",
                    pytrace=False,
                )

            user = tracker.lookup(ResourceLookupRequest(resource="user"))
            if user.status != "found":
                pytest.fail("User lookup must have status=found.", pytrace=False)
            if user.canonical_resource != "user":
                pytest.fail(
                    "User lookup must resolve canonical_resource=user.",
                    pytrace=False,
                )
            if 42 not in {identifier.value for identifier in user.identifiers}:
                pytest.fail(
                    "User lookup must include the synthetic identifier 42.",
                    pytrace=False,
                )

            summary = tracker.lookup(ResourceLookupRequest(resource="summary"))
            if summary.status != "not_found":
                pytest.fail(
                    "Summary lookup must have status=not_found.",
                    pytrace=False,
                )
        finally:
            if runtime is not None:
                runtime.close()
            if engine is not None:
                engine.dispose()
    except Exception as exc:
        safe_message = redactor.redact_text(str(exc))
        pytest.fail(
            "Configured DeepSeek FAST Resource Identifier Tracker call failed: "
            f"{type(exc).__name__}: {safe_message}",
            pytrace=False,
        )
