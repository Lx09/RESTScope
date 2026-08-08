"""Store the Agent-owned worklist while validating only issued references.

This Module deliberately does not interpret semantic grouping, progress,
root-cause text, item order, or completion quality. It gives the Resolution
Agent freedom to rewrite those judgments while ensuring that precise runtime
objects remain behind their registry references and that a rejected whole-list
write cannot partly change session state.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from restscope.tools import ToolFailure

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
        self._issued_item_ids: set[str] = set()
        self._next_item_number = 1

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
        """Atomically replace the whole list after mechanical validation.

        Args:
            expected_revision: Revision returned by the latest successful read
                or write. A stale value prevents lost updates.
            active_item_id: Stable ``WI-*`` identity to investigate next, or
                ``None`` when no item is active.
            items: Complete replacement list. Existing identities may move;
                new identities must consume the next contiguous numbers.

        Returns:
            A defensive copy of the newly stored revision.

        State changes and errors:
            A valid write advances the revision and permanently records every
            newly issued identity. A :class:`ToolFailure` rejects the entire
            write for stale revision, invalid references, an unknown active
            item, skipped numbers, or reuse of a retired identity; no state or
            identity counter changes on rejection.
        """
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
        new_item_ids = self._validate_item_id_sequence(item_ids)
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
        self._issued_item_ids.update(new_item_ids)
        self._next_item_number += len(new_item_ids)
        return replacement.model_copy(deep=True)

    def _validate_item_id_sequence(self, item_ids: list[str]) -> set[str]:
        """Require new WI identities to use the next contiguous session numbers.

        Existing items may be reordered freely. Once an item disappears from a
        successful replacement, its identity becomes retired and cannot later
        refer to a different diagnosis. The returned set is committed only
        after every other Worklist check succeeds, preserving atomic writes.
        """
        incoming = set(item_ids)
        current = {item.item_id for item in self._value.items}
        reused = sorted((incoming & self._issued_item_ids) - current)
        if reused:
            self._reject(
                code="retired_worklist_item_id",
                message="Retired Worklist item IDs cannot be reused: " + ", ".join(reused),
            )

        new_item_ids = incoming - self._issued_item_ids
        expected = {
            _format_worklist_item_id(number)
            for number in range(
                self._next_item_number,
                self._next_item_number + len(new_item_ids),
            )
        }
        if new_item_ids != expected:
            self._reject(
                code="worklist_item_id_out_of_sequence",
                message=(
                    "New Worklist item IDs must be assigned contiguously starting at "
                    + _format_worklist_item_id(self._next_item_number)
                    + "."
                ),
            )
        return new_item_ids

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


def _format_worklist_item_id(number: int) -> str:
    """Render one positive session sequence with at least three digits."""
    return f"WI-{number:03d}"
