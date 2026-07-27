"""Regression scenarios for generator revision history. Each test documents one observable contract or failure boundary."""

from __future__ import annotations

from pathlib import Path


def _catalog(tmp_path: Path):
    from restscope.db import (
        Base,
        SqlAlchemyGeneratorConfigUnitOfWork,
        create_engine_from_url,
        make_session_factory,
    )
    from restscope.openapi_parser import OpenAPIParser
    from restscope.testing import GeneratorConfigCatalog

    ir = OpenAPIParser.parse(
        {
            "openapi": "3.0.3",
            "info": {"title": "History", "version": "1"},
            "paths": {
                "/items/{itemId}": {
                    "get": {
                        "parameters": [
                            {
                                "name": "itemId",
                                "in": "path",
                                "required": True,
                                "schema": {"type": "string"},
                            },
                            {
                                "name": "region",
                                "in": "query",
                                "required": False,
                                "schema": {"type": "string"},
                            }
                        ],
                        "responses": {"200": {"description": "ok"}},
                    }
                }
            },
        }
    )
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'history.sqlite'}")
    Base.metadata.create_all(engine)
    catalog = GeneratorConfigCatalog(
        lambda: SqlAlchemyGeneratorConfigUnitOfWork(
            make_session_factory(engine)
        )
    )
    assert catalog.initialize_once(ir) is True
    return catalog, ir.operations["GET /items/{itemId}"]


def test_candidate_preview_validates_patch_without_writing_revision(
    tmp_path: Path,
) -> None:
    """Scenario: verify that candidate preview validates patch without writing revision."""
    catalog, operation = _catalog(tmp_path)
    baseline = catalog.inspect_operation(operation.operation_key)
    node_id = baseline.configs[0].input_node_id

    preview = catalog.preview_candidate(
        operation_key=operation.operation_key,
        updates=[
            {
                "input_node_id": node_id,
                "strategy": {"type": "constant", "value": "known-item"},
            }
        ],
    )

    assert preview.revision == baseline.revision
    assert preview.configs[0].strategy.model_dump(mode="json") == {
        "type": "constant",
        "value": "known-item",
    }
    assert catalog.inspect_operation(operation.operation_key) == baseline
    assert len(catalog.list_revisions(operation.operation_key)) == 1
    assert catalog.preview_candidate(
        operation_key=operation.operation_key,
        updates=[],
    ) == baseline


def test_candidate_revision_can_be_accepted_with_batch_evaluation(
    tmp_path: Path,
) -> None:
    """Scenario: verify that candidate revision can be accepted with batch evaluation."""
    catalog, operation = _catalog(tmp_path)
    current = catalog.inspect_operation(operation.operation_key)
    node_id = current.configs[0].input_node_id

    candidate = catalog.stage_candidate(
        operation_key=operation.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": node_id,
                "strategy": {"type": "constant", "value": "known-item"},
            }
        ],
        hypothesis={"failure_class": "invalid_parameter_value"},
    )

    assert candidate.revision == 2
    assert catalog.inspect_operation(operation.operation_key).revision == 2
    assert [item.lifecycle for item in catalog.list_revisions(operation.operation_key)] == [
        "accepted",
        "candidate",
    ]

    accepted = catalog.accept_candidate(
        operation_key=operation.operation_key,
        candidate_revision=2,
        evaluation={
            "case_count": 10,
            "success_2xx_count": 9,
            "success_rate": 0.9,
            "required_threshold": 0.8,
            "run_id": "run_2",
        },
    )

    assert accepted.lifecycle == "accepted"
    assert accepted.evaluation["success_rate"] == 0.9
    assert catalog.inspect_operation(operation.operation_key).revision == 2


def test_rejected_candidate_creates_compensating_rollback_revision(
    tmp_path: Path,
) -> None:
    """Scenario: verify that rejected candidate creates compensating rollback revision."""
    catalog, operation = _catalog(tmp_path)
    baseline = catalog.inspect_operation(operation.operation_key)
    node_id = baseline.configs[0].input_node_id
    baseline_strategy = baseline.configs[0].strategy
    catalog.stage_candidate(
        operation_key=operation.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": node_id,
                "strategy": {"type": "constant", "value": "bad-item"},
            }
        ],
        hypothesis={"failure_class": "invalid_parameter_value"},
    )

    restored = catalog.reject_candidate_and_rollback(
        operation_key=operation.operation_key,
        candidate_revision=2,
        evaluation={
            "case_count": 10,
            "success_2xx_count": 1,
            "success_rate": 0.1,
            "required_threshold": 0.8,
            "run_id": "run_2",
        },
    )

    assert restored.revision == 3
    assert restored.configs[0].strategy == baseline_strategy
    history = catalog.list_revisions(operation.operation_key)
    assert [item.lifecycle for item in history] == [
        "accepted",
        "rejected",
        "rollback",
    ]
    assert history[1].evaluation["success_rate"] == 0.1
    assert history[2].rollback_of_revision == 2
    assert history[2].restored_from_revision == 1
    assert history[2].config.configs[0].strategy == baseline_strategy


def test_interrupted_candidate_is_automatically_rolled_back(
    tmp_path: Path,
) -> None:
    """Scenario: verify that interrupted candidate is automatically rolled back."""
    catalog, operation = _catalog(tmp_path)
    current = catalog.inspect_operation(operation.operation_key)
    node_id = current.configs[0].input_node_id
    catalog.stage_candidate(
        operation_key=operation.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": node_id,
                "strategy": {"type": "constant", "value": "unverified"},
            }
        ],
        hypothesis={"failure_class": "unknown"},
    )

    recovered = catalog.recover_interrupted_candidate(operation.operation_key)

    assert recovered.revision == 3
    assert [
        item.lifecycle for item in catalog.list_revisions(operation.operation_key)
    ] == ["accepted", "rejected", "rollback"]
    assert catalog.list_revisions(operation.operation_key)[1].evaluation == {
        "stop_reason": "interrupted"
    }


def test_direct_catalog_patch_is_recorded_as_an_accepted_revision(
    tmp_path: Path,
) -> None:
    """Scenario: verify that direct catalog patch is recorded as an accepted revision."""
    catalog, operation = _catalog(tmp_path)
    current = catalog.inspect_operation(operation.operation_key)

    catalog.patch_operation(
        operation_key=operation.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": current.configs[0].input_node_id,
                "strategy": {"type": "constant", "value": "manual"},
            }
        ],
    )

    history = catalog.list_revisions(operation.operation_key)
    assert [item.lifecycle for item in history] == ["accepted", "accepted"]


def test_candidate_finalization_accepts_only_validated_changes_atomically(
    tmp_path: Path,
) -> None:
    """Scenario: verify that candidate finalization accepts only validated changes atomically."""
    catalog, operation = _catalog(tmp_path)
    baseline = catalog.inspect_operation(operation.operation_key)
    path_node_id = baseline.configs[0].input_node_id
    query_node_id = baseline.configs[1].input_node_id
    path_strategy = {"type": "constant", "value": "known-item"}
    query_strategy = {"type": "constant", "value": "experimental-region"}
    catalog.stage_candidate(
        operation_key=operation.operation_key,
        expected_revision=1,
        updates=[
            {"input_node_id": path_node_id, "strategy": path_strategy},
            {"input_node_id": query_node_id, "strategy": query_strategy},
        ],
        hypothesis={"kind": "operation_smoke_joint_patch"},
    )

    accepted = catalog.finalize_candidate(
        operation_key=operation.operation_key,
        candidate_revision=2,
        accepted_input_node_ids={path_node_id},
        evaluation={
            "case_count": 10,
            "success_2xx_count": 0,
            "success_rate": 0.0,
            "required_threshold": 0.8,
            "run_id": "run_2",
            "validation_status": "partial",
            "accepted_change_count": 1,
            "rejected_change_count": 1,
        },
    )

    assert accepted.revision == 3
    assert accepted.configs[0].strategy.value == "known-item"
    assert accepted.configs[1].strategy == baseline.configs[1].strategy
    history = catalog.list_revisions(operation.operation_key)
    assert [item.lifecycle for item in history] == [
        "accepted",
        "rejected",
        "accepted",
    ]
    assert history[1].evaluation["validation_status"] == "partial"
    assert all(item.lifecycle != "rollback" for item in history)


def test_candidate_finalization_with_no_accepted_changes_restores_parent_without_rollback(
    tmp_path: Path,
) -> None:
    """Scenario: verify that candidate finalization with no accepted changes restores parent without rollback."""
    catalog, operation = _catalog(tmp_path)
    baseline = catalog.inspect_operation(operation.operation_key)
    node_id = baseline.configs[0].input_node_id
    catalog.stage_candidate(
        operation_key=operation.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": node_id,
                "strategy": {"type": "constant", "value": "bad-item"},
            }
        ],
        hypothesis={"kind": "operation_smoke_joint_patch"},
    )

    accepted = catalog.finalize_candidate(
        operation_key=operation.operation_key,
        candidate_revision=2,
        accepted_input_node_ids=set(),
        evaluation={
            "case_count": 10,
            "success_2xx_count": 0,
            "success_rate": 0.0,
            "required_threshold": 0.8,
            "run_id": "run_2",
            "validation_status": "rejected",
            "accepted_change_count": 0,
            "rejected_change_count": 1,
        },
    )

    assert accepted.revision == 3
    assert accepted.configs == baseline.configs
    assert [
        item.lifecycle for item in catalog.list_revisions(operation.operation_key)
    ] == ["accepted", "rejected", "accepted"]


def test_candidate_finalization_rejects_nodes_not_changed_by_candidate(
    tmp_path: Path,
) -> None:
    """Scenario: verify that candidate finalization rejects nodes not changed by candidate."""
    from restscope.testing import GeneratorConfigError

    catalog, operation = _catalog(tmp_path)
    baseline = catalog.inspect_operation(operation.operation_key)
    node_id = baseline.configs[0].input_node_id
    catalog.stage_candidate(
        operation_key=operation.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": node_id,
                "strategy": {"type": "constant", "value": "candidate"},
            }
        ],
        hypothesis={"kind": "operation_smoke_joint_patch"},
    )

    try:
        catalog.finalize_candidate(
            operation_key=operation.operation_key,
            candidate_revision=2,
            accepted_input_node_ids={"not-a-candidate-change"},
            evaluation={"run_id": "run_2"},
        )
    except GeneratorConfigError as exc:
        assert exc.code == "generator_candidate_invalid_accepted_nodes"
    else:
        raise AssertionError("unchanged node was accepted")

    assert catalog.inspect_operation(operation.operation_key).revision == 2
    assert [
        item.lifecycle for item in catalog.list_revisions(operation.operation_key)
    ] == ["accepted", "candidate"]


def test_candidate_partial_finalization_rolls_back_the_whole_transaction_on_write_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Scenario: verify that candidate partial finalization rolls back the whole transaction on write error."""
    from restscope.db.repositories import SqlAlchemyGeneratorConfigRepository

    catalog, operation = _catalog(tmp_path)
    baseline = catalog.inspect_operation(operation.operation_key)
    path_node_id = baseline.configs[0].input_node_id
    query_node_id = baseline.configs[1].input_node_id
    catalog.stage_candidate(
        operation_key=operation.operation_key,
        expected_revision=1,
        updates=[
            {
                "input_node_id": path_node_id,
                "strategy": {"type": "constant", "value": "known-item"},
            },
            {
                "input_node_id": query_node_id,
                "strategy": {"type": "constant", "value": "region"},
            },
        ],
        hypothesis={"kind": "operation_smoke_joint_patch"},
    )

    def fail_replace(self, **kwargs):
        del self, kwargs
        raise RuntimeError("simulated accepted revision write failure")

    monkeypatch.setattr(
        SqlAlchemyGeneratorConfigRepository,
        "replace",
        fail_replace,
    )

    try:
        catalog.finalize_candidate(
            operation_key=operation.operation_key,
            candidate_revision=2,
            accepted_input_node_ids={path_node_id},
            evaluation={"run_id": "run_2"},
        )
    except RuntimeError as exc:
        assert str(exc) == "simulated accepted revision write failure"
    else:
        raise AssertionError("simulated write failure did not escape")

    assert catalog.inspect_operation(operation.operation_key).revision == 2
    history = catalog.list_revisions(operation.operation_key)
    assert [item.lifecycle for item in history] == ["accepted", "candidate"]
    assert history[1].evaluation is None
