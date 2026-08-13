"""Protect generic Batch execution and its frozen generation revision."""

from __future__ import annotations


def _api_behavior_catalog():
    """Create a real in-memory Catalog required before every Batch send."""

    from restscope.api_behavior_monitor.catalog import APIBehaviorCatalog
    from restscope.api_behavior_monitor.coordinator import APIBehaviorMonitorCoordinator
    from restscope.api_behavior_monitor.contract_monitor import ResponseContractTracker
    from restscope.api_behavior_monitor.response_processor import APIBehaviorResponseProcessor
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )

    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    return APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(sessions)
    )


def test_batch_tool_returns_inline_cases_from_one_frozen_revision() -> None:
    """A Batch exposes one durable abstract state identity, never per-case rows."""
    import httpx

    from restscope.harness.operation_testing import OperationTestingService
    from restscope.api_behavior_monitor.catalog import APIBehaviorCatalog
    from restscope.api_behavior_monitor.coordinator import APIBehaviorMonitorCoordinator
    from restscope.api_behavior_monitor.contract_monitor import ResponseContractTracker
    from restscope.api_behavior_monitor.response_processor import APIBehaviorResponseProcessor
    from restscope.db import (
        Base,
        SqlAlchemyAPIBehaviorUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import RequestGenerationConfigStore
    from restscope.target_api import TargetAPIClient
    from restscope.tools.context import ToolContext
    from restscope.tools.test_case import TestCaseBatchToolBackend

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Batch", "version": "1"},
            "paths": {
                "/items": {
                    "get": {
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "query",
                                "required": True,
                                "schema": {"type": "integer", "enum": [1]},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    engine = create_engine_from_url("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    catalog = APIBehaviorCatalog(
        lambda: SqlAlchemyAPIBehaviorUnitOfWork(sessions)
    )
    sent: list[str] = []
    client = TargetAPIClient(
        response_processor=APIBehaviorResponseProcessor(
            APIBehaviorMonitorCoordinator(
                contract_tracker=ResponseContractTracker(catalog),
                catalog=catalog,
            )
        ),
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: sent.append(str(request.url)) or httpx.Response(200)
            ),
            **kwargs,
        )
    )
    context = ToolContext(
        ir=ir,
        baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
        base_url="https://api.example.test",
        headers={},
    )
    backend = TestCaseBatchToolBackend(
        service=OperationTestingService(
            config_store=store,
            target_api_client=client,
            api_behavior_catalog=catalog,
        ),
        context_provider=lambda: context,
    )

    result = backend.run_batch(
        operation_key="GET /items",
        test_mode="happy_path",
        case_count=2,
        seed=9,
    )["structured"]

    assert result["generation_revision"] == 0
    assert result["executed_case_count"] == 2
    assert result["batch_id"].startswith("batch_")
    assert result["batch_persistence_warnings"] == []
    assert result["success_count"] == 2
    assert [case["case_number"] for case in result["cases"]] == [1, 2]
    assert all("limit=1" in url for url in sent)
    assert "run_id" not in result
    assert result["abstract_test_case_id"].startswith("abstract_test_case_")
    assert "case_number" in str(result)
    stored_batch = catalog.get_batch(result["batch_id"])
    stored_cases, stored_total = catalog.list_batch_observations(
        batch_id=result["batch_id"],
        offset=0,
        limit=10,
    )
    assert stored_batch is not None
    assert stored_batch.summary["status"] == "completed"
    assert stored_batch.summary["http_status_counts"] == {"200": 2}
    assert stored_total == 2
    assert [case.batch_case_index for case in stored_cases] == [0, 1]

    repeated = backend.run_batch(
        operation_key="GET /items",
        test_mode="happy_path",
        case_count=1,
        seed=10,
    )["structured"]
    assert repeated["abstract_test_case_id"] == result["abstract_test_case_id"]


def test_batch_freezes_reference_values_with_generation_revision() -> None:
    """All cases use one value snapshot even if live evidence changes later."""
    import httpx
    from restscope.harness.operation_testing import OperationTestingService
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import (
        BehaviorMonitorReferences,
        RequestGenerationConfigStore,
        RequestGenerationPatchRuntime,
    )
    from restscope.request_generation.parameter_patch import SemanticParameterPatch
    from restscope.target_api import TargetAPIClient
    from restscope.tools.context import ToolContext

    class ChangingValues:
        """Return different live values each time so freezing is observable."""

        def __init__(self) -> None:
            self.pools = [[1]]
            self.calls = 0

        def values_for(self, _strategy):
            pool = self.pools[min(self.calls, len(self.pools) - 1)]
            self.calls += 1
            return pool

        def resource_key(self, _strategy):
            return "limits"

        def resource_records(self, _strategy):
            pool_index = max(0, min(self.calls - 1, len(self.pools) - 1))
            return tuple({"limit": value} for value in self.pools[pool_index])

        def resource_identity_fields(self, _strategy):
            return ("limit",)

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Frozen references", "version": "1"},
            "paths": {
                "/items/{limit}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "limit",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    catalog = _api_behavior_catalog()
    from restscope.api_behavior_monitor.catalog import (
        OperationDefinition,
        ResourceDerivation,
    )

    catalog.ensure_operation(
        OperationDefinition(
            operation_id="GET /limits",
            method="GET",
            path="/limits",
        )
    )
    catalog.record_resource_derivations(
        operation_id="GET /limits",
        derivations=[
            ResourceDerivation(
                resource_name="limits",
                identity_fields=["limit"],
                role="REFERENCED",
                instances=[{"limit": 1}],
            )
        ],
    )
    values = ChangingValues()
    runtime = RequestGenerationPatchRuntime(
        store=store,
        ir_provider=lambda: ir,
        references=BehaviorMonitorReferences(catalog),
    )
    patch = SemanticParameterPatch.model_validate(
        {
            "changes": [
                {
                    "input": "path.limit",
                    "inclusion_probability": 1,
                    "strategy": {
                        "type": "resource_identifier",
                        "source": {
                            "operation_key": "GET /limits",
                            "status_code": 200,
                            "media_type": "application/json",
                            "field": "body.items[].limit",
                        },
                    },
                }
            ]
        }
    )
    validated = runtime.validate(
        operation_key="GET /items/{limit}",
        expected_revision=0,
        affected_inputs=("path.limit",),
        patch=patch,
    )
    runtime.apply(
        operation_key="GET /items/{limit}",
        expected_revision=0,
        validation_digest=validated.validation_digest,
        affected_inputs=("path.limit",),
        patch=patch,
    )

    values.pools = [[7], [8]]
    values.calls = 0
    sent: list[str] = []
    client = TargetAPIClient(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: sent.append(str(request.url)) or httpx.Response(200)
            ),
            **kwargs,
        )
    )
    service = OperationTestingService(
        config_store=store,
        api_behavior_catalog=catalog,
        target_api_client=client,
        reference_values=values,
    )
    service.run_batch(
        ToolContext(
            ir=ir,
            baseline_schema_source={"kind": "inline", "format": "json", "content": "{}"},
            base_url="https://api.example.test",
            headers={},
        ),
        operation_key="GET /items/{limit}",
        test_mode="happy_path",
        case_count=2,
        seed=5,
    )

    assert values.calls == 1
    assert all(url.endswith("/items/7") for url in sent)


def test_abstract_case_persistence_failure_stops_batch_before_network() -> None:
    """No target request escapes when the mandatory audit snapshot cannot commit."""

    import httpx
    import pytest

    from restscope.harness.operation_testing import OperationTestingService
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import RequestGenerationConfigStore
    from restscope.target_api import TargetAPIClient
    from restscope.tools.context import ToolContext

    class FailingCatalog:
        """Accept operation metadata, then fail the abstract snapshot write."""

        def ensure_operation(self, operation):
            return operation

        def ensure_abstract_test_case(self, _test_case):
            raise RuntimeError("abstract case storage failed")

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Preflight", "version": "1"},
            "paths": {
                "/items": {
                    "get": {"responses": {"200": {"description": "ok"}}}
                }
            },
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    sent: list[str] = []
    client = TargetAPIClient(
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: sent.append(str(request.url)) or httpx.Response(200)
            ),
            **kwargs,
        )
    )
    service = OperationTestingService(
        config_store=store,
        api_behavior_catalog=FailingCatalog(),
        target_api_client=client,
    )

    with pytest.raises(RuntimeError, match="abstract case storage failed"):
        service.run_batch(
            ToolContext(
                ir=ir,
                baseline_schema_source={},
                base_url="https://api.example.test",
                headers={},
            ),
            operation_key="GET /items",
            test_mode="happy_path",
            case_count=1,
            seed=3,
        )

    assert sent == []


def test_observation_failure_does_not_stop_later_batch_cases() -> None:
    """A missing Observation becomes a warning while remaining requests still run."""

    import httpx

    from restscope.api_behavior_monitor.coordinator import APIBehaviorMonitorCoordinator
    from restscope.api_behavior_monitor.contract_monitor import ResponseContractTracker
    from restscope.api_behavior_monitor.response_processor import APIBehaviorResponseProcessor
    from restscope.harness.operation_testing import OperationTestingService
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import RequestGenerationConfigStore
    from restscope.target_api import TargetAPIClient
    from restscope.tools.context import ToolContext

    catalog = _api_behavior_catalog()

    class OneObservationFailure:
        """Delegate every Catalog behavior except the first Observation write."""

        def __init__(self) -> None:
            self.failed = False

        def __getattr__(self, name):
            return getattr(catalog, name)

        def record_observation(self, observation):
            if not self.failed:
                self.failed = True
                raise RuntimeError("simulated observation failure")
            return catalog.record_observation(observation)

    flaky = OneObservationFailure()
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Persistence", "version": "1"},
            "paths": {"/items": {"get": {"responses": {"200": {"description": "ok"}}}}},
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    sent: list[str] = []
    client = TargetAPIClient(
        response_processor=APIBehaviorResponseProcessor(
            APIBehaviorMonitorCoordinator(
                contract_tracker=ResponseContractTracker(flaky),
                catalog=flaky,
            )
        ),
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(
                lambda request: sent.append(str(request.url)) or httpx.Response(200)
            ),
            **kwargs,
        ),
    )
    result = OperationTestingService(
        config_store=store,
        api_behavior_catalog=flaky,
        target_api_client=client,
    ).run_batch(
        ToolContext(
            ir=ir,
            baseline_schema_source={},
            base_url="https://api.example.test",
            headers={},
        ),
        operation_key="GET /items",
        test_mode="happy_path",
        case_count=2,
        seed=4,
    )

    assert len(sent) == 2
    assert any(
        warning.startswith("observation_persistence_incomplete:")
        for warning in result.batch_persistence_warnings
    )
    stored = catalog.get_batch(result.batch_id)
    assert stored is not None
    assert stored.summary["status"] == "completed"
    assert stored.summary["persisted_observation_count"] == 1


def test_final_batch_summary_failure_returns_inline_results_with_warning() -> None:
    """Completed target results survive when every summary replacement fails."""

    import httpx

    from restscope.api_behavior_monitor.coordinator import APIBehaviorMonitorCoordinator
    from restscope.api_behavior_monitor.contract_monitor import ResponseContractTracker
    from restscope.api_behavior_monitor.response_processor import APIBehaviorResponseProcessor
    from restscope.harness.operation_testing import OperationTestingService
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import RequestGenerationConfigStore
    from restscope.target_api import TargetAPIClient
    from restscope.tools.context import ToolContext

    catalog = _api_behavior_catalog()

    class SummaryFailure:
        """Keep the initial Batch but reject all progress/final summary writes."""

        def __getattr__(self, name):
            return getattr(catalog, name)

        def update_batch_summary(self, **_arguments):
            raise RuntimeError("simulated summary failure")

    flaky = SummaryFailure()
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Summary", "version": "1"},
            "paths": {"/items": {"get": {"responses": {"200": {"description": "ok"}}}}},
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    client = TargetAPIClient(
        response_processor=APIBehaviorResponseProcessor(
            APIBehaviorMonitorCoordinator(
                contract_tracker=ResponseContractTracker(flaky),
                catalog=flaky,
            )
        ),
        client_factory=lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(lambda _request: httpx.Response(200)),
            **kwargs,
        ),
    )

    result = OperationTestingService(
        config_store=store,
        api_behavior_catalog=flaky,
        target_api_client=client,
    ).run_batch(
        ToolContext(
            ir=ir,
            baseline_schema_source={},
            base_url="https://api.example.test",
            headers={},
        ),
        operation_key="GET /items",
        test_mode="happy_path",
        case_count=1,
        seed=5,
    )

    assert result.success_count == 1
    assert result.batch_persistence_warnings == (
        "batch_summary_persistence_failed:RuntimeError",
    )
    stored = catalog.get_batch(result.batch_id)
    assert stored is not None
    assert stored.summary["status"] == "running"


def test_unexpected_batch_interruption_marks_summary_failed() -> None:
    """An unexpected execution defect preserves a failed summary and re-raises."""

    import pytest

    from restscope.harness.operation_testing import OperationTestingService
    from restscope.openapi_parser import OpenAPIParser
    from restscope.request_generation import RequestGenerationConfigStore
    from restscope.tools.context import ToolContext

    class BrokenClient:
        """Raise outside the expected HTTP/transport failure vocabulary."""

        def send(self, *_arguments, **_keywords):
            raise ValueError("unexpected execution defect")

    class CapturingCatalog:
        """Remember the created Batch identity while delegating persistence."""

        batch_id = None

        def __getattr__(self, name):
            return getattr(catalog, name)

        def create_batch(self, batch):
            record = catalog.create_batch(batch)
            self.batch_id = record.batch_id
            return record

    catalog = _api_behavior_catalog()
    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "Interrupted", "version": "1"},
            "paths": {"/items": {"get": {"responses": {"200": {"description": "ok"}}}}},
        }
    )
    store = RequestGenerationConfigStore()
    store.initialize_once(ir)
    capturing_catalog = CapturingCatalog()
    service = OperationTestingService(
        config_store=store,
        api_behavior_catalog=capturing_catalog,
        target_api_client=BrokenClient(),
    )

    with pytest.raises(ValueError, match="unexpected execution defect"):
        service.run_batch(
            ToolContext(
                ir=ir,
                baseline_schema_source={},
                base_url="https://api.example.test",
                headers={},
            ),
            operation_key="GET /items",
            test_mode="happy_path",
            case_count=1,
            seed=6,
        )

    # The safe log records the exception type without persisting its message.
    assert capturing_catalog.batch_id is not None
    stored = catalog.get_batch(capturing_catalog.batch_id)
    assert stored is not None
    assert stored.summary["status"] == "failed"
    assert stored.summary["logs"] == ["batch_execution_failed:ValueError"]
