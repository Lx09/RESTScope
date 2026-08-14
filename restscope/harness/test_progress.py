"""Render current Catalog test progress for a fresh Orchestrator root.

The API Behavior Catalog performs every database join and aggregate. This
Harness-owned Reader only prioritizes complete records, uses the shared safe
Markdown writer, and enforces the Context Source character budget.
"""

from __future__ import annotations

from restscope.api_behavior_monitor.catalog import (
    APIBehaviorCatalog,
    OperationTestProgress,
    TestProgressSnapshot,
)
from restscope.context import CompactTextWriter

TEST_PROGRESS_CONTEXT_SOURCE = "test-progress"
TEST_PROGRESS_MAX_CHARS = 12_000
_OMISSION_RESERVE = 96


class TestProgressContextReader:
    """Read a fresh Catalog aggregate and render one bounded Context Source."""

    def __init__(self, catalog: APIBehaviorCatalog) -> None:
        """Bind the sole deep read Interface without opening a transaction."""

        self._catalog = catalog

    def read(self) -> str:
        """Read and safely render current progress for one new Agent root.

        A Catalog exception deliberately propagates. The Prompt Session then
        fails before any model decision, so absent progress cannot be mistaken
        for completion.
        """

        return render_test_progress_context(
            self._catalog.read_test_progress(),
            max_chars=TEST_PROGRESS_MAX_CHARS,
        )


def render_test_progress_context(
    snapshot: TestProgressSnapshot,
    *,
    max_chars: int = TEST_PROGRESS_MAX_CHARS,
) -> str:
    """Prioritize incomplete operation records and fit whole records in Markdown.

    Args:
        snapshot: Complete Catalog-owned aggregate from one read transaction.
        max_chars: Hard Context Source allowance, normally 12,000 characters.

    Returns:
        Safe Markdown with totals, operation counts, resource state counts, and
        explicit category-specific omission counts. Event details are excluded.
    """

    if max_chars < 2_400:
        raise ValueError("test-progress context requires at least 2400 characters")

    summary_budget = min(1_200, max(400, max_chars // 8))
    operation_budget = max(800, int(max_chars * 0.65))
    resource_budget = max_chars - summary_budget - operation_budget - 4

    summary_writer = CompactTextWriter(max_value_chars=200)
    summary_writer.section("TEST PROGRESS SUMMARY")
    summary_writer.record(
        "totals",
        operation_count=len(snapshot.operations),
        untested_operation_count=sum(
            item.positive_case_count == 0 and item.negative_case_count == 0
            for item in snapshot.operations
        ),
        positive_batch_count=sum(
            item.positive_batch_count for item in snapshot.operations
        ),
        negative_batch_count=sum(
            item.negative_batch_count for item in snapshot.operations
        ),
        positive_executed_case_count=sum(
            item.positive_case_count for item in snapshot.operations
        ),
        negative_executed_case_count=sum(
            item.negative_case_count for item in snapshot.operations
        ),
        resource_state_instance_count=sum(
            item.instance_count for item in snapshot.resource_states
        ),
    )
    summary = summary_writer.render(max_chars=summary_budget).text

    operation_writer = CompactTextWriter(max_value_chars=400)
    operation_writer.section("OPERATION BATCH AND CASE COUNTS", untrusted=True)
    for index, item in enumerate(
        sorted(snapshot.operations, key=_operation_priority),
        start=1,
    ):
        operation_writer.record(
            f"O{index}",
            required=False,
            operation=item.operation_id,
            method=item.method,
            path=item.path,
            positive_batches=item.positive_batch_count,
            negative_batches=item.negative_batch_count,
            positive_cases=item.positive_case_count,
            negative_cases=item.negative_case_count,
        )
    operation_rendered = operation_writer.render(
        max_chars=operation_budget - _OMISSION_RESERVE
    )
    operations = (
        operation_rendered.text
        + "\n\n> operation records omitted: "
        + str(operation_rendered.metrics.omitted_history_count)
    )

    resource_writer = CompactTextWriter(max_value_chars=200)
    resource_writer.section("CURRENT RESOURCE STATE COUNTS", untrusted=True)
    for index, item in enumerate(snapshot.resource_states, start=1):
        resource_writer.record(
            f"R{index}",
            required=False,
            resource=item.resource_type,
            state=item.semantic_state,
            instances=item.instance_count,
        )
    resource_rendered = resource_writer.render(
        max_chars=resource_budget - _OMISSION_RESERVE
    )
    resources = (
        resource_rendered.text
        + "\n\n> resource-state records omitted: "
        + str(resource_rendered.metrics.omitted_history_count)
    )
    output = f"{summary}\n\n{operations}\n\n{resources}"
    if len(output) > max_chars:
        raise RuntimeError("test-progress renderer exceeded its fixed budget")
    return output


def _operation_priority(item: OperationTestProgress) -> tuple[int, str]:
    """Keep untested, then one-sided, then fully exercised operations first."""

    if item.positive_case_count == 0 and item.negative_case_count == 0:
        rank = 0
    elif item.positive_case_count == 0 or item.negative_case_count == 0:
        rank = 1
    else:
        rank = 2
    return rank, item.operation_id


__all__ = [
    "TEST_PROGRESS_CONTEXT_SOURCE",
    "TEST_PROGRESS_MAX_CHARS",
    "TestProgressContextReader",
    "render_test_progress_context",
]
