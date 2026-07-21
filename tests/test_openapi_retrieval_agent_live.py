"""Opt-in live-model test for the OpenAPI Retrieval Agent."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from restscope.agent.openapi_retrieval import (
    OpenAPIRetrievalRequest,
    ParameterValueProducerQuery,
    build_openapi_retrieval_agent,
)
from restscope.openapi_parser import OpenAPIParser
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


def test_live_model_finds_order_id_producer_in_petstore_asset() -> None:
    """Investigate a real asset and require the model to find the documented producer."""

    config = RESTScopeConfig.from_environment(PROJECT_ROOT / ".env")
    thinking = config.llm.thinking
    if thinking.provider == "fake":
        pytest.fail("THINK_PROVIDER must select a real provider for this live test.")
    if not thinking.model:
        pytest.fail("THINK_MODEL must be configured for this live test.")
    if thinking.provider in {"openai_compatible", "deepseek"} and not thinking.api_key:
        pytest.fail(f"THINK_API_KEY must be configured for the {thinking.provider} provider.")

    result = build_openapi_retrieval_agent(config).retrieve(
        OpenAPIRetrievalRequest(
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
