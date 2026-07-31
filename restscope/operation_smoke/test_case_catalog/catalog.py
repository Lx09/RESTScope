"""Store and query Test Cases for one Operation Smoke run.

The Catalog receives every Batch case and every HTTP request actually attempted
by Solve. It assigns compact identities, keeps request values plus bounded
failure evidence, and answers exact typed queries. The whole Module is
in-memory: the Coordinator drops it when the operation's Smoke run ends.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .schemas import CatalogQuery, CatalogTestCase, CatalogTestCaseDraft


class TestCaseCatalog:
    """Hide run-local Test Case identity, storage, and lookup behind one Interface."""

    def __init__(self, *, valid_parameters: Iterable[str]) -> None:
        """Create an empty Catalog for one operation.

        Args:
            valid_parameters: Semantic OpenAPI input handles. They distinguish
                a valid-but-absent request input from a forged Parameter name.
        """
        self._valid_parameters = frozenset(valid_parameters)
        self._cases: dict[str, CatalogTestCase] = {}
        self._next_number = 1

    @property
    def case_range(self) -> str:
        """Return a compact prompt hint covering every assigned Test Case."""
        if not self._cases:
            return "empty"
        if len(self._cases) == 1:
            return "TC1"
        return f"TC1..TC{len(self._cases)}"

    @property
    def valid_parameters(self) -> frozenset[str]:
        """Expose the operation contract for deterministic Agent validation.

        The set is never added to an initial Prompt. Dedup uses it only after a
        final model response to reject invented semantic handles.
        """
        return self._valid_parameters

    def issue_case_id(self) -> str:
        """Reserve the next identity for Batch execution or an HTTP Probe."""
        case_id = f"TC{self._next_number}"
        self._next_number += 1
        return case_id

    def record(
        self,
        value: CatalogTestCase | CatalogTestCaseDraft,
    ) -> CatalogTestCase:
        """Retain a case, assigning an identity when the caller has no draft ID."""
        case = value if isinstance(value, CatalogTestCase) else (
            CatalogTestCase(
                case_id=self.issue_case_id(),
                **value.model_dump(mode="python"),
            )
        )
        if case.case_id in self._cases:
            raise ValueError(f"Test Case already exists: {case.case_id}")
        issued_number = int(case.case_id.removeprefix("TC"))
        if issued_number >= self._next_number:
            raise ValueError(
                f"Test Case ID was not issued by this Catalog: {case.case_id}"
            )
        self._cases[case.case_id] = case
        return case

    def query(self, query: CatalogQuery) -> dict[str, Any]:
        """Return only the exact facts requested for supplied Test Case IDs.

        Raises:
            KeyError: A case ID or Parameter name was not issued for this
                operation. Tool adapters convert this into a structured error.
        """
        cases = [self._require_case(case_id) for case_id in query.case_ids]
        output: dict[str, Any] = {
            "action": query.action,
            "cases": {},
        }
        for case in cases:
            output["cases"][case.case_id] = self._query_case(case, query)
        return output

    def get_case(self, case_id: str) -> CatalogTestCase:
        """Return one immutable case to trusted workflow code, not to tools."""
        return self._require_case(case_id)

    def _require_case(self, case_id: str) -> CatalogTestCase:
        """Reject forged or stale run-local Test Case references."""
        try:
            return self._cases[case_id]
        except KeyError as exc:
            raise KeyError(f"Unknown Test Case: {case_id}") from exc

    def _query_case(
        self,
        case: CatalogTestCase,
        query: CatalogQuery,
    ) -> dict[str, Any]:
        """Execute one validated query action against one Test Case."""
        if query.action == "parameter_value":
            assert query.name is not None
            if (
                query.name not in self._valid_parameters
                and query.name not in case.parameters
            ):
                raise KeyError(f"Unknown Parameter: {query.name}")
            if query.name not in case.parameters:
                return {
                    "parameter": query.name,
                    "present": False,
                }
            return {
                "parameter": query.name,
                "present": True,
                "value": case.parameters[query.name],
            }

        if query.action == "parameters_using_value":
            return {
                "value": query.value,
                "parameters": sorted(
                    name
                    for name, value in case.parameters.items()
                    if _typed_equal(value, query.value)
                ),
            }

        if query.action == "response_field_value":
            assert query.name is not None
            present, value = _response_value(
                case.response_body,
                query.name,
            )
            result: dict[str, Any] = {
                "field": query.name,
                "present": present,
            }
            if present:
                result["value"] = value
            return result

        if query.action == "response_fields_using_value":
            return {
                "value": query.value,
                "fields": sorted(
                    path
                    for path, value in _response_fields(case.response_body)
                    if _typed_equal(value, query.value)
                ),
            }

        return {
            "messages": (
                list(case.failure.messages)
                if case.failure is not None
                else []
            )
        }


def _typed_equal(left: Any, right: Any) -> bool:
    """Compare JSON-like values without Python's bool/int coercion."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return (
            left.keys() == right.keys()
            and all(_typed_equal(left[key], right[key]) for key in left)
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _typed_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return bool(left == right)


def _response_value(body: Any | None, path: str) -> tuple[bool, Any | None]:
    """Resolve one concrete ``body.*`` path without evaluating expressions."""
    if path == "body":
        return (body is not None), body
    if not path.startswith("body."):
        raise KeyError(f"Response Field must start with body: {path}")
    current = body
    for part in _path_parts(path.removeprefix("body.")):
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return False, None
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return False, None
            current = current[part]
    return True, current


def _path_parts(path: str) -> list[str | int]:
    """Parse dotted object names and concrete list indices such as ``a[0]``."""
    output: list[str | int] = []
    for segment in path.split("."):
        name = segment.split("[", 1)[0]
        if name:
            output.append(name)
        suffix = segment[len(name):]
        while suffix:
            if not suffix.startswith("[") or "]" not in suffix:
                raise KeyError(f"Invalid Response Field path: {path}")
            raw_index, suffix = suffix[1:].split("]", 1)
            if not raw_index.isdigit():
                raise KeyError(f"Invalid Response Field index: {path}")
            output.append(int(raw_index))
    return output


def _response_fields(
    body: Any | None,
    *,
    path: str = "body",
) -> list[tuple[str, Any]]:
    """Flatten a retained failed response into concrete searchable field paths."""
    if body is None:
        return []
    output = [(path, body)]
    if isinstance(body, dict):
        for name, value in body.items():
            output.extend(
                _response_fields(value, path=f"{path}.{name}")
            )
    elif isinstance(body, list):
        for index, value in enumerate(body):
            output.extend(
                _response_fields(value, path=f"{path}[{index}]")
            )
    return output
