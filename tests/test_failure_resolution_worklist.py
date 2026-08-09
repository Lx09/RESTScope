"""Behavior contracts for the reference-only Failure Resolution worklist."""

from __future__ import annotations

import pytest
from pydantic import ValidationError


def _candidate_registry():
    """Issue one precise candidate that must remain behind an opaque reference."""
    from restscope.operation_smoke.failure_resolution.candidates import (
        PatchCandidateRegistry,
    )
    from restscope.operation_smoke.memory import SolveAttemptParameterWrite
    from restscope.operation_smoke.parameter_patch import GeneratorPatchDraft
    from restscope.request_generation import InputGeneratorPatch
    from restscope.request_generation.models import ConstantGenerator

    registry = PatchCandidateRegistry()
    candidate = registry.issue(
        patch=GeneratorPatchDraft(
            updates=[
                InputGeneratorPatch(
                    input_node_id="path/projectId",
                    strategy=ConstantGenerator(type="constant", value="known-project"),
                )
            ]
        ),
        root_cause="Random project identifiers do not exist.",
        change_reason="Reuse one known project identifier.",
        affected_parameters=["path.projectId"],
        parameter_attributions=[
            SolveAttemptParameterWrite(
                input_node_id="path/projectId",
                cause_summary="Random project identifiers do not exist.",
            )
        ],
        before_generators={"path.projectId": {"type": "random_string"}},
        after_generators={
            "path.projectId": {"type": "constant", "value": "known-project"}
        },
        samples=[
            {
                "values": {"path.projectId": "known-project"},
                "present": {"path.projectId": True},
            }
        ],
        outputs_used=3,
    )
    return registry, candidate


def _source(ref: str, message: str, *case_refs: str):
    """Build one immutable exact Failure source issued by the harness."""
    from restscope.operation_smoke.failure_resolution import FailureSource

    return FailureSource(
        failure_ref=ref,
        message=message,
        test_case_refs=list(case_refs),
    )


def _item(**changes):
    """Build one valid Agent-owned item with only semantic text and references."""
    from restscope.operation_smoke.failure_resolution import WorklistItem

    value = {
        "item_id": "WI-001",
        "source_failure_refs": ["E1"],
        "test_case_refs": ["TC1"],
        "suspected_parameters": ["body.name"],
        "progress": "The response identifies the name field.",
        "root_cause": "The generated name already exists.",
        "candidate_refs": [],
        "decision": None,
    }
    value.update(changes)
    return WorklistItem.model_validate(value)


def _store(*, candidate_refs=()):
    """Create a store with trusted source, Parameter, and candidate registries."""
    from restscope.operation_smoke.failure_resolution import FailureWorklistStore

    return FailureWorklistStore(
        sources=[
            _source("E1", "HTTP 400: name already exists", "TC1", "TC2"),
            _source("E2", "HTTP 400: namespace is invalid", "TC3"),
        ],
        valid_parameters={"body.name", "body.namespace_id"},
        candidate_refs=lambda: frozenset(candidate_refs),
    )


def test_worklist_schema_rejects_embedded_precise_objects() -> None:
    """Patch and Test Case objects cannot become a second Agent-written truth."""
    from restscope.operation_smoke.failure_resolution import WorklistItem

    value = _item().model_dump(mode="json")
    value["patch"] = {
        "updates": [{"input_node_id": "request/body/name"}],
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorklistItem.model_validate(value)


def test_worklist_schema_rejects_agent_authored_failure_summary() -> None:
    """Stable Failure summaries come from E messages, not duplicated Agent text."""
    from restscope.operation_smoke.failure_resolution import WorklistItem

    value = _item().model_dump(mode="json")
    value["failure_summary"] = "Agent-authored duplicate of the root cause."

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorklistItem.model_validate(value)


def test_worklist_write_is_revision_checked_and_atomic() -> None:
    """A stale or invalid replacement leaves the previous complete list intact."""
    from restscope.tools import ToolFailure

    store = _store()
    first = store.write(
        expected_revision=0,
        active_item_id="WI-001",
        items=[_item()],
    )
    assert first.revision == 1

    with pytest.raises(ToolFailure, match="revision"):
        store.write(
            expected_revision=0,
            active_item_id=None,
            items=[],
        )
    assert store.read() == first

    forged = _item(test_case_refs=["TC3"])
    with pytest.raises(ToolFailure, match="does not belong"):
        store.write(
            expected_revision=1,
            active_item_id="WI-001",
            items=[forged],
        )
    assert store.read() == first


def test_worklist_item_id_uses_the_fixed_wi_number_format() -> None:
    """A Worklist item uses identity like WI-001 instead of a semantic slug."""
    from restscope.operation_smoke.failure_resolution import WorklistItem

    invalid = _item().model_dump(mode="json")
    invalid["item_id"] = "name-conflict"
    with pytest.raises(ValidationError, match="item_id"):
        WorklistItem.model_validate(invalid)

    assert _item(item_id="WI-001").item_id == "WI-001"
    assert _item(item_id="WI-1000").item_id == "WI-1000"


def test_worklist_item_ids_start_at_wi_001_and_add_contiguous_numbers() -> None:
    """A session cannot skip the next never-before-issued WI number."""
    from restscope.tools import ToolFailure

    store = _store()

    first = store.write(
        expected_revision=0,
        active_item_id="WI-001",
        items=[
            _item(item_id="WI-001"),
            _item(
                item_id="WI-002",
                source_failure_refs=["E2"],
                test_case_refs=["TC3"],
                suspected_parameters=["body.namespace_id"],
            ),
        ],
    )
    assert [item.item_id for item in first.items] == ["WI-001", "WI-002"]

    with pytest.raises(ToolFailure, match="WI-003"):
        store.write(
            expected_revision=1,
            active_item_id="WI-001",
            items=[
                first.items[0],
                first.items[1],
                _item(item_id="WI-004", test_case_refs=["TC2"]),
            ],
        )
    assert store.read() == first

    accepted = store.write(
        expected_revision=first.revision,
        active_item_id="WI-003",
        items=[
            *first.items,
            _item(item_id="WI-003", test_case_refs=["TC2"]),
        ],
    )
    assert accepted.revision == 2
    assert accepted.active_item_id == "WI-003"


def test_deleted_worklist_item_id_cannot_be_reused() -> None:
    """A removed WI identity never points at a later unrelated diagnosis."""
    from restscope.tools import ToolFailure

    store = _store()
    first = store.write(
        expected_revision=0,
        active_item_id="WI-001",
        items=[_item(item_id="WI-001")],
    )
    without_item = store.write(
        expected_revision=first.revision,
        active_item_id=None,
        items=[],
    )

    with pytest.raises(ToolFailure, match="Retired.*WI-001"):
        store.write(
            expected_revision=without_item.revision,
            active_item_id="WI-001",
            items=[_item(item_id="WI-001", test_case_refs=["TC2"])],
        )
    assert store.read() == without_item

    replacement = store.write(
        expected_revision=without_item.revision,
        active_item_id="WI-002",
        items=[_item(item_id="WI-002", test_case_refs=["TC2"])],
    )
    assert replacement.active_item_id == "WI-002"


def test_worklist_accepts_agent_owned_overlap_split_and_reordering() -> None:
    """The harness validates references without imposing semantic grouping rules."""
    store = _store()
    snapshot = store.write(
        expected_revision=0,
        active_item_id="WI-002",
        items=[
            _item(
                item_id="WI-001",
                test_case_refs=["TC1"],
            ),
            _item(
                item_id="WI-002",
                test_case_refs=["TC2"],
            ),
        ],
    )

    assert snapshot.active_item_id == "WI-002"
    assert [item.item_id for item in snapshot.items] == [
        "WI-001",
        "WI-002",
    ]


def test_worklist_rejects_forged_source_parameter_and_candidate_refs() -> None:
    """Opaque references remain authoritative even though semantics are Agent-owned."""
    from restscope.tools import ToolFailure

    cases = [
        (_item(source_failure_refs=["E9"]), "Unknown Failure source"),
        (_item(suspected_parameters=["body.forged"]), "Unknown Parameter"),
        (_item(candidate_refs=["P9"]), "Unknown Patch candidate"),
    ]

    for item, message in cases:
        with pytest.raises(ToolFailure, match=message):
            _store().write(
                expected_revision=0,
                active_item_id=item.item_id,
                items=[item],
            )


def test_apply_patch_decision_can_select_only_a_listed_real_candidate() -> None:
    """The Agent selects an opaque reference, never an embedded executable Patch."""
    from restscope.operation_smoke.failure_resolution import WorklistDecision

    decision = WorklistDecision(
        outcome="apply_patch",
        selected_candidate_ref="P1",
        reason="P1 addresses the observed name conflict.",
    )
    store = _store(candidate_refs={"P1"})
    snapshot = store.write(
        expected_revision=0,
        active_item_id=None,
        items=[_item(candidate_refs=["P1"], decision=decision)],
    )
    assert snapshot.items[0].decision == decision

    with pytest.raises(ValidationError, match="listed on the worklist item"):
        _item(candidate_refs=[], decision=decision)


def test_final_coverage_requires_every_source_case_association_once_or_more() -> None:
    """Agent grouping may overlap, but it cannot silently lose initial evidence."""
    from restscope.tools import ToolFailure

    store = _store()
    store.write(
        expected_revision=0,
        active_item_id=None,
        items=[
            _item(test_case_refs=["TC1", "TC2"]),
            _item(
                item_id="WI-002",
                source_failure_refs=["E2"],
                test_case_refs=["TC3"],
                suspected_parameters=["body.namespace_id"],
            ),
            _item(
                item_id="WI-003",
                test_case_refs=["TC1"],
            ),
        ],
    )
    store.require_complete_coverage()

    incomplete = _store()
    incomplete.write(
        expected_revision=0,
        active_item_id=None,
        items=[_item(test_case_refs=["TC1"])],
    )
    with pytest.raises(ToolFailure, match=r"E1/TC2.*E2/TC3"):
        incomplete.require_complete_coverage()


def test_exact_probe_evidence_is_valid_but_not_new_required_coverage() -> None:
    """A reproduced message may cite its new TC without changing Batch coverage."""
    store = _store()

    matched = store.associate_probe_case(
        case_ref="TC4",
        failure_messages=["HTTP 400: name already exists"],
    )

    assert matched == ("E1",)
    assert store.sources[0].test_case_refs == ["TC1", "TC2", "TC4"]
    store.write(
        expected_revision=0,
        active_item_id=None,
        items=[
            _item(test_case_refs=["TC1", "TC2", "TC4"]),
            _item(
                item_id="WI-002",
                source_failure_refs=["E2"],
                test_case_refs=["TC3"],
                suspected_parameters=["body.namespace_id"],
            ),
        ],
    )
    store.require_complete_coverage()

    # A novel Probe message cannot be semantically attached by the harness.
    assert store.associate_probe_case(
        case_ref="TC5",
        failure_messages=["HTTP 503: new unrelated failure"],
    ) == ()


def test_worklist_tools_return_structured_references_and_safe_failures() -> None:
    """The toolbox exposes the store without leaking its trusted registries."""
    from restscope.tools import AgentToolbox
    from restscope.llm import ToolCall
    from restscope.tools.worklist import (
        READ_WORKLIST_TOOL_NAME,
        WRITE_WORKLIST_TOOL_NAME,
        register_worklist_tools,
    )

    toolbox = AgentToolbox()
    store = _store()
    register_worklist_tools(toolbox=toolbox, store=store)

    read = toolbox.execute(
        ToolCall(id="read-1", name=READ_WORKLIST_TOOL_NAME, arguments={})
    )
    assert read.status == "succeeded"
    assert read.structured == {
        "revision": 0,
        "active_item_id": None,
        "items": [],
    }

    invalid = _item(source_failure_refs=["E9"]).model_dump(mode="json")
    rejected = toolbox.execute(
        ToolCall(
            id="write-1",
            name=WRITE_WORKLIST_TOOL_NAME,
            arguments={
                "expected_revision": 0,
                "active_item_id": invalid["item_id"],
                "items": [invalid],
            },
        )
    )
    assert rejected.status == "failed"
    assert rejected.error == {
        "code": "unknown_failure_source",
        "message": "Unknown Failure source: E9",
    }
    assert store.read().revision == 0


def test_worklist_tool_schema_denies_embedded_patch_before_store_mutation() -> None:
    """Provider strictness is repeated locally before a precise object can enter."""
    from restscope.tools import AgentToolbox
    from restscope.llm import ToolCall
    from restscope.tools.worklist import (
        WRITE_WORKLIST_TOOL_NAME,
        register_worklist_tools,
    )

    toolbox = AgentToolbox()
    store = _store()
    register_worklist_tools(toolbox=toolbox, store=store)
    item = _item().model_dump(mode="json")
    item["patch"] = {"updates": []}

    result = toolbox.execute(
        ToolCall(
            id="write-embedded",
            name=WRITE_WORKLIST_TOOL_NAME,
            arguments={
                "expected_revision": 0,
                "active_item_id": item["item_id"],
                "items": [item],
            },
        )
    )

    assert result.status == "denied"
    assert result.error["code"] == "invalid_tool_arguments"
    assert store.read().revision == 0


@pytest.mark.parametrize(
    "decision",
    [
        {
            "outcome": "apply_patch",
            "reason": "The candidate passed validation.",
        },
        {
            "outcome": "apply_patch",
            "selected_candidate_ref": None,
            "reason": "The candidate passed validation.",
        },
        {
            "outcome": "no_patch",
            "selected_candidate_ref": "P1",
            "reason": "No safe candidate should be applied.",
        },
    ],
)
def test_worklist_tool_schema_denies_inconsistent_patch_decisions(
    decision: dict,
) -> None:
    """The tool contract rejects candidate choices that contradict the outcome."""
    from restscope.tools import AgentToolbox
    from restscope.llm import ToolCall
    from restscope.tools.worklist import (
        WRITE_WORKLIST_TOOL_NAME,
        register_worklist_tools,
    )

    toolbox = AgentToolbox()
    store = _store(candidate_refs={"P1"})
    register_worklist_tools(toolbox=toolbox, store=store)
    item = _item(candidate_refs=["P1"]).model_dump(mode="json")
    item["decision"] = decision

    result = toolbox.execute(
        ToolCall(
            id="write-inconsistent-decision",
            name=WRITE_WORKLIST_TOOL_NAME,
            arguments={
                "expected_revision": 0,
                "active_item_id": None,
                "items": [item],
            },
        )
    )

    assert result.status == "denied"
    assert result.error == {
        "code": "invalid_tool_arguments",
        "message": "Tool arguments do not match the declared input schema.",
    }
    assert store.read().revision == 0


def test_worklist_tool_returns_safe_feedback_for_unlisted_selected_candidate() -> None:
    """A dynamic item-reference error remains correctable model feedback."""
    from restscope.tools import AgentToolbox
    from restscope.llm import ToolCall
    from restscope.tools.worklist import (
        WRITE_WORKLIST_TOOL_NAME,
        register_worklist_tools,
    )

    toolbox = AgentToolbox()
    store = _store(candidate_refs={"P1"})
    register_worklist_tools(toolbox=toolbox, store=store)
    item = _item().model_dump(mode="json")
    item["decision"] = {
        "outcome": "apply_patch",
        "selected_candidate_ref": "P1",
        "reason": "P1 is the reviewed repair.",
    }

    result = toolbox.execute(
        ToolCall(
            id="write-unlisted-candidate",
            name=WRITE_WORKLIST_TOOL_NAME,
            arguments={
                "expected_revision": 0,
                "active_item_id": None,
                "items": [item],
            },
        )
    )

    assert result.status == "failed"
    assert result.error == {
        "code": "invalid_worklist_item",
        "message": (
            "One or more Worklist items violate the decision or reference rules. "
            "For apply_patch, selected_candidate_ref must also appear in "
            "candidate_refs; use unique E*, TC*, and P* references with their "
            "issued formats."
        ),
    }
    assert store.read().revision == 0


def test_candidate_registry_issues_real_refs_and_returns_defensive_copies() -> None:
    """Only the harness can create P refs or retrieve precise candidate objects."""
    registry, candidate = _candidate_registry()

    assert candidate.candidate_ref == "P1"
    assert registry.refs() == frozenset({"P1"})
    assert registry.get("P1") == candidate
    assert registry.get("P1") is not candidate


def test_candidate_read_tool_returns_summary_without_executable_patch_dto() -> None:
    """Context recovery reveals meaning and validation, never resubmittable objects."""
    from restscope.tools import AgentToolbox
    from restscope.llm import ToolCall
    from restscope.tools.parameter import (
        READ_CANDIDATE_TOOL_NAME,
        register_candidate_read_tool,
    )

    registry, _candidate = _candidate_registry()
    toolbox = AgentToolbox()
    register_candidate_read_tool(toolbox=toolbox, registry=registry)

    result = toolbox.execute(
        ToolCall(
            id="candidate-1",
            name=READ_CANDIDATE_TOOL_NAME,
            arguments={"candidate_ref": "P1"},
        )
    )

    assert result.status == "succeeded"
    assert result.structured == {
        "candidate_ref": "P1",
        "validation_status": "validated",
        "root_cause": "Random project identifiers do not exist.",
        "affected_parameters": ["path.projectId"],
        "generator_change_overview": ["path.projectId: generator changed"],
        "constraint_change_overview": [],
        "sample_overview": {
            "sample_count": 1,
            "covered_parameters": ["path.projectId"],
        },
        "model_outputs_used": 3,
    }
    assert "patch" not in result.structured
    assert "samples" not in result.structured
    assert "before_generators" not in result.structured

    forged = toolbox.execute(
        ToolCall(
            id="candidate-2",
            name=READ_CANDIDATE_TOOL_NAME,
            arguments={"candidate_ref": "P99"},
        )
    )
    assert forged.status == "failed"
    assert forged.error == {
        "code": "unknown_patch_candidate",
        "message": "Unknown or expired Patch candidate: P99",
    }
