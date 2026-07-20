from __future__ import annotations


def test_memory_schema_defaults_and_source_refs() -> None:
    from restscope.memory import MemoryItem, MemoryPackage

    item = MemoryItem(
        id="mem_1",
        kind="operation",
        title="POST /pets",
        content="High risk operation",
        source_table="operations",
        source_id="op_high",
    )

    package = MemoryPackage.from_items(
        schema_id="schema_1",
        task_id="task_1",
        role="planner",
        items=[item],
    )

    assert item.importance == 0.5
    assert package.operation_memory == [item]
    assert package.source_refs == {"operations": ["op_high"]}


def test_ranker_and_compressor_prioritize_and_budget_items() -> None:
    from restscope.memory import MemoryCompressor, MemoryItem, MemoryQuery, MemoryRanker

    high = MemoryItem(
        id="high",
        kind="observation",
        title="Repeated server error",
        content="server_error " * 80,
        importance=0.95,
        confidence=0.9,
        recency_score=0.7,
        relevance_score=0.9,
        risk_score=0.8,
        source_table="test_observations",
        source_id="obs_1",
    )
    low = MemoryItem(
        id="low",
        kind="operation",
        title="Recent low risk",
        content="recent but low risk",
        importance=0.2,
        confidence=0.5,
        recency_score=1.0,
        relevance_score=0.2,
        risk_score=0.1,
        source_table="operations",
        source_id="op_low",
    )
    duplicate = high.model_copy(update={"id": "high_dup"})
    query = MemoryQuery(schema_id="schema_1", role="planner", token_budget=20)

    ranked = MemoryRanker().rank([low, high], query)
    compressed = MemoryCompressor().fit_budget([high, duplicate, low], token_budget=20)

    assert ranked[0].id == "high"
    assert [item.source_id for item in compressed].count("obs_1") == 1
    assert sum(item.estimated_tokens for item in compressed) <= 20
