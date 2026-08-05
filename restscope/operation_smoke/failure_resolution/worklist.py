"""Store the Agent-owned worklist while validating only issued references.

This Module deliberately does not interpret semantic grouping, progress,
root-cause text, item order, or completion quality. It gives the Resolution
Agent freedom to rewrite those judgments while ensuring that precise runtime
objects remain behind their registry references and that a rejected whole-list
write cannot partly change session state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from restscope.capabilities import ToolFailure

from .schemas import FailureSource, FailureWorklist, WorklistItem


class FailureWorklistStore:
    """Own one revisioned worklist and the trusted references it may contain."""

    def __init__(
        self,
        *,
        sources: Iterable[FailureSource],
        valid_parameters: Iterable[str],
        candidate_refs: Callable[[], frozenset[str]],
    ) -> None:
        """Retain source registries and freeze the initial coverage obligation."""
        source_list = list(sources)
        self._sources = {item.failure_ref: item for item in source_list}
        if len(self._sources) != len(source_list):
            raise ValueError("Failure source references must be unique")
        # Later HTTP probes may reproduce an exact source message and become
        # valid supporting evidence. They must not expand the Batch-completion
        # obligation, which is defined only by the original failed cases.
        self._required_pairs = frozenset(
            (source.failure_ref, case_ref)
            for source in source_list
            for case_ref in source.test_case_refs
        )
        self._valid_parameters = frozenset(valid_parameters)
        self._candidate_refs = candidate_refs
        self._value = FailureWorklist(revision=0, items=[])

    @property
    def sources(self) -> tuple[FailureSource, ...]:
        """Return trusted exact Failure sources in their deterministic order."""
        return tuple(self._sources.values())

    def read(self) -> FailureWorklist:
        """Return a deep copy so callers cannot mutate stored list members."""
        return self._value.model_copy(deep=True)

    def associate_probe_case(
        self,
        *,
        case_ref: str,
        failure_messages: Iterable[str],
    ) -> tuple[str, ...]:
        """Attach trusted Probe evidence only to exact matching E messages.

        Args:
            case_ref: A real run-local Test Case identity already issued by the
                Catalog after an HTTP attempt.
            failure_messages: Parsed exact messages from that trusted case.

        Returns:
            The deterministic E references whose exact message was reproduced.
            A novel Probe message returns an empty tuple and does not let the
            Agent invent a semantic source association.

        State changes:
            Matching source registries gain ``case_ref`` as optional evidence.
            The initial required coverage set and current worklist are unchanged.
        """
        number = case_ref.removeprefix("TC")
        if (
            not case_ref.startswith("TC")
            or not number.isdigit()
            or int(number) < 1
        ):
            raise ValueError("Probe Test Case reference must be a real TC identity")
        message_set = frozenset(failure_messages)
        matched: list[str] = []
        for failure_ref, source in self._sources.items():
            if source.message not in message_set:
                continue
            matched.append(failure_ref)
            if case_ref in source.test_case_refs:
                continue
            self._sources[failure_ref] = source.model_copy(
                update={"test_case_refs": [*source.test_case_refs, case_ref]}
            )
        return tuple(matched)

    def write(
        self,
        *,
        expected_revision: int,
        active_item_id: str | None,
        items: list[WorklistItem],
    ) -> FailureWorklist:
        """Atomically replace the whole list after mechanical validation."""
        if expected_revision != self._value.revision:
            self._reject(
                code="stale_worklist_revision",
                message=(
                    "The worklist revision is stale; read the current worklist "
                    "before replacing it."
                ),
            )
        item_ids = [item.item_id for item in items]
        if len(item_ids) != len(set(item_ids)):
            self._reject(
                code="duplicate_worklist_item",
                message="Worklist item IDs must be unique.",
            )
        if active_item_id is not None and active_item_id not in set(item_ids):
            self._reject(
                code="unknown_active_worklist_item",
                message="The active worklist item does not exist in the replacement.",
            )
        issued_candidates = self._candidate_refs()
        for item in items:
            self._validate_item(item, issued_candidates=issued_candidates)

        replacement = FailureWorklist(
            revision=self._value.revision + 1,
            active_item_id=active_item_id,
            items=items,
        )
        self._value = replacement.model_copy(deep=True)
        return replacement.model_copy(deep=True)

    def require_complete_coverage(self) -> None:
        """Require every original Failure/Test Case association at least once."""
        present = {
            (failure_ref, case_ref)
            for item in self._value.items
            for failure_ref in item.source_failure_refs
            for case_ref in item.test_case_refs
            if case_ref in self._sources[failure_ref].test_case_refs
        }
        missing = sorted(self._required_pairs - present)
        if missing:
            rendered = ", ".join(f"{failure_ref}/{case_ref}" for failure_ref, case_ref in missing)
            self._reject(
                code="incomplete_failure_coverage",
                message="The final worklist is missing source evidence: " + rendered,
            )

    def _validate_item(
        self,
        item: WorklistItem,
        *,
        issued_candidates: frozenset[str],
    ) -> None:
        """Validate references without deciding whether the item makes sense."""
        unknown_sources = sorted(set(item.source_failure_refs) - set(self._sources))
        if unknown_sources:
            self._reject(
                code="unknown_failure_source",
                message="Unknown Failure source: " + ", ".join(unknown_sources),
            )
        allowed_cases = {
            case_ref
            for failure_ref in item.source_failure_refs
            for case_ref in self._sources[failure_ref].test_case_refs
        }
        invalid_cases = sorted(set(item.test_case_refs) - allowed_cases)
        if invalid_cases:
            self._reject(
                code="unrelated_test_case",
                message=(
                    "Test Case does not belong to the referenced Failure sources: "
                    + ", ".join(invalid_cases)
                ),
            )
        unknown_parameters = sorted(
            set(item.suspected_parameters) - self._valid_parameters
        )
        if unknown_parameters:
            self._reject(
                code="unknown_parameter",
                message="Unknown Parameter: " + ", ".join(unknown_parameters),
            )
        unknown_candidates = sorted(set(item.candidate_refs) - issued_candidates)
        if unknown_candidates:
            self._reject(
                code="unknown_patch_candidate",
                message="Unknown Patch candidate: " + ", ".join(unknown_candidates),
            )

    @staticmethod
    def _reject(*, code: str, message: str) -> None:
        """Raise one model-safe rejection without changing stored state."""
        raise ToolFailure(code=code, message=message)
