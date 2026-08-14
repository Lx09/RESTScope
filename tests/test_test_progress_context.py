"""Protect the Harness-owned bounded projection of Catalog test progress."""

from __future__ import annotations


def test_context_prioritizes_incomplete_operations_and_reports_omissions() -> None:
    """A tight projection keeps gaps visible and omits only complete records first."""

    from restscope.api_behavior_monitor.catalog import (
        OperationTestProgress,
        ResourceStateCount,
        TestProgressSnapshot,
    )
    from restscope.harness.test_progress import render_test_progress_context

    snapshot = TestProgressSnapshot(
        operations=(
            OperationTestProgress(
                operation_id="GET /untested",
                method="GET",
                path="/untested",
                positive_case_count=0,
                negative_case_count=0,
            ),
            OperationTestProgress(
                operation_id="POST /partial",
                method="POST",
                path="/partial",
                positive_case_count=4,
                negative_case_count=0,
            ),
            *(
                OperationTestProgress(
                    operation_id=f"GET /complete/{index}",
                    method="GET",
                    path=f"/complete/{index}/" + "x" * 200,
                    positive_case_count=3,
                    negative_case_count=2,
                )
                for index in range(100)
            ),
        ),
        resource_states=(
            ResourceStateCount(
                resource_type="users",
                semantic_state="active",
                instance_count=7,
            ),
        ),
    )

    rendered = render_test_progress_context(snapshot, max_chars=2_400)

    assert len(rendered) <= 2_400
    assert "GET /untested" in rendered
    assert "POST /partial" in rendered
    assert "users" in rendered
    assert "active" in rendered
    assert "operation records omitted" in rendered
    assert "resource-state records omitted: 0" in rendered


def test_context_reader_calls_only_the_deep_catalog_interface() -> None:
    """Harness receives a finished aggregate and never navigates persistence details."""

    from restscope.api_behavior_monitor.catalog import TestProgressSnapshot
    from restscope.harness.test_progress import TestProgressContextReader

    class Catalog:
        """Expose only the approved deep read seam."""

        calls = 0

        def read_test_progress(self) -> TestProgressSnapshot:
            """Return an empty but valid current snapshot."""

            self.calls += 1
            return TestProgressSnapshot()

    catalog = Catalog()
    reader = TestProgressContextReader(catalog)

    first = reader.read()
    second = reader.read()

    assert catalog.calls == 2
    assert "positive" in first
    assert second == first


def test_context_reader_propagates_catalog_failure_instead_of_hiding_progress() -> None:
    """A fresh root cannot receive an empty substitute after a failed read."""

    import pytest

    from restscope.harness.test_progress import TestProgressContextReader

    class BrokenCatalog:
        """Represent a database read failure before Orchestrator startup."""

        def read_test_progress(self):
            """Fail without manufacturing an empty snapshot."""

            raise RuntimeError("progress unavailable")

    with pytest.raises(RuntimeError, match="progress unavailable"):
        TestProgressContextReader(BrokenCatalog()).read()
