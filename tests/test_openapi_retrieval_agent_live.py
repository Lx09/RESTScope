"""Opt-in live-model test for the OpenAPI Retrieval Agent."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from restscope.agent.openapi_retrieval import (
    OpenAPIRetrievalResult,
    OpenAPIRetrievalRequest,
    ParameterValueProducerQuery,
    build_openapi_retrieval_agent,
)
from restscope.openapi_parser import OpenAPIParser, OpenAPISpecIR
from restscope.observability import build_tracing_runtime
from restscope.redaction import Redactor
from restscope.restscope_config import RESTScopeConfig


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PETSTORE_OPENAPI = PROJECT_ROOT / "assets" / "openapi" / "petstore-v3.json"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("RUN_OPENAPI_RETRIEVAL_LIVE") != "1",
        reason="Set RUN_OPENAPI_RETRIEVAL_LIVE=1 to call the configured thinking model.",
    ),
]


def _config_for_live_model_slot(
    config: RESTScopeConfig,
    slot: str,
) -> RESTScopeConfig:
    """Project one configured model slot into the Agent's thinking-model role."""

    if slot not in {"thinking", "fast"}:
        raise ValueError(
            "OPENAPI_RETRIEVAL_LIVE_MODEL_SLOT must be 'thinking' or 'fast'"
        )
    selected = getattr(config.llm, slot)
    return replace(config, llm=replace(config.llm, thinking=selected))


def _retrieve_with_tracing(
    *,
    base_config: RESTScopeConfig,
    config: RESTScopeConfig,
    request: OpenAPIRetrievalRequest,
    ir: OpenAPISpecIR,
) -> OpenAPIRetrievalResult:
    tracing_runtime = build_tracing_runtime(
        config.tracing,
        redactor=Redactor(
            (
                base_config.llm.thinking.api_key,
                base_config.llm.fast.api_key,
            )
        ),
    )
    try:
        return build_openapi_retrieval_agent(
            config,
            tracing_runtime=tracing_runtime,
        ).retrieve(request, ir=ir)
    finally:
        tracing_runtime.close()


def test_live_model_finds_order_id_producer_in_petstore_asset() -> None:
    """Investigate a real asset and require the model to find the documented producer."""

    base_config = RESTScopeConfig.from_environment(PROJECT_ROOT / ".env")
    model_slot = os.environ.get("OPENAPI_RETRIEVAL_LIVE_MODEL_SLOT", "thinking")
    config = _config_for_live_model_slot(base_config, model_slot)
    selected_model = config.llm.thinking
    if selected_model.provider == "fake":
        pytest.fail(f"The {model_slot} model slot must select a real provider.")
    if not selected_model.model:
        pytest.fail(f"The {model_slot} model slot must configure a model.")
    if (
        selected_model.provider in {"openai_compatible", "deepseek"}
        and not selected_model.api_key
    ):
        pytest.fail(
            f"The {model_slot} model slot must configure an API key for "
            f"the {selected_model.provider} provider."
        )

    result = _retrieve_with_tracing(
        base_config=base_config,
        config=config,
        request=OpenAPIRetrievalRequest(
            query=ParameterValueProducerQuery(
                objective="parameter_value_producer",
                consumer_method="GET",
                consumer_path="/store/order/{orderId}",
                parameter_name="orderId",
                limit=10,
            ),
        ),
        ir=OpenAPIParser.parse(PETSTORE_OPENAPI),
    )

    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))

    assert result.status == "found"
    assert result.consumer_operation.operation_id == "getOrderById"
    assert result.investigation_summary.evidence_sufficient is True
    assert 1 <= result.investigation_summary.tool_calls <= 20
    assert result.investigation_summary.actions

    candidates_by_id = {
        candidate.operation.operation_id: candidate
        for candidate in result.candidates
    }
    assert "placeOrder" in candidates_by_id

    evidence_ids = {evidence.id for evidence in result.evidence}
    place_order = candidates_by_id["placeOrder"]
    assert place_order.value_locations
    assert place_order.evidence_refs
    assert set(place_order.evidence_refs) <= evidence_ids
