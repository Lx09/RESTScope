"""Store and query Test Cases for one Operation Smoke run.

The Catalog receives every Batch case and every HTTP request actually attempted
by Solve. It assigns compact identities, keeps request values plus bounded
failure evidence, and answers exact typed queries. The whole Module is
in-memory: the Coordinator drops it when the operation's Smoke run ends.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .schemas import CatalogTestCase, CatalogTestCaseDraft


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

    def get_case(self, case_id: str) -> CatalogTestCase:
        """Return one immutable case to trusted workflow code, not to tools."""
        return self._require_case(case_id)

    def get_parameter_value(
        self,
        *,
        case_ids: list[str],
        parameter: str,
    ) -> dict[str, Any]:
        """Report whether each request used one exact semantic Parameter.

        Args:
            case_ids: Run-local ``TC*`` references to compare.
            parameter: The semantic OpenAPI input handle to inspect.

        Returns:
            One result per Test Case. A used Parameter includes its exact value;
            an unused Parameter has the explicit terminal status
            ``parameter_not_used_in_request`` and omits ``value``.

        Raises:
            KeyError: A Test Case or Parameter was not issued for this
                operation.
        """
        cases = [self._require_case(case_id) for case_id in case_ids]
        results: dict[str, Any] = {}
        for case in cases:
            if (
                parameter not in self._valid_parameters
                and parameter not in case.parameters
            ):
                raise KeyError(f"Unknown Parameter: {parameter}")
            if parameter not in case.parameters:
                results[case.case_id] = {
                    "parameter": parameter,
                    "status": "parameter_not_used_in_request",
                }
                continue
            results[case.case_id] = {
                "parameter": parameter,
                "status": "parameter_used_in_request",
                "value": case.parameters[parameter],
            }
        return {"cases": results}

    def get_response_field_value(
        self,
        *,
        case_ids: list[str],
        field: str,
    ) -> dict[str, Any]:
        """Report why one response field is available or unavailable.

        Args:
            case_ids: Run-local ``TC*`` references to compare.
            field: A concrete response path beginning with ``body``.

        Returns:
            One explicit status per Test Case. The result distinguishes an
            unretained response body from a retained body that lacks the field;
            only a present field includes ``value``.

        Raises:
            KeyError: A Test Case is unknown or the field path is invalid.
        """
        # Validate the model-supplied path even when no response body was
        # retained. Otherwise an invalid path would look like valid absence and
        # could teach the model to repeat malformed queries.
        _response_path_parts(field)
        cases = [self._require_case(case_id) for case_id in case_ids]
        results: dict[str, Any] = {}
        for case in cases:
            if case.response_body is None:
                results[case.case_id] = {
                    "field": field,
                    "status": "response_body_not_retained",
                }
                continue
            present, value = _response_value(case.response_body, field)
            if not present:
                results[case.case_id] = {
                    "field": field,
                    "status": (
                        "response_field_not_present_in_retained_body"
                    ),
                }
                continue
            results[case.case_id] = {
                "field": field,
                "status": "response_field_present_in_retained_body",
                "value": value,
            }
        return {"cases": results}

    def find_parameters_by_value(
        self,
        *,
        case_ids: list[str],
        value: Any,
    ) -> dict[str, Any]:
        """Find request Parameters whose values exactly match one typed value.

        Args:
            case_ids: Run-local ``TC*`` references to search.
            value: A JSON-like value. Booleans, numbers, and containers retain
                their types during comparison.

        Returns:
            The matching semantic Parameter handles for each Test Case.

        Raises:
            KeyError: A Test Case reference is unknown.
        """
        cases = [self._require_case(case_id) for case_id in case_ids]
        return {
            "cases": {
                case.case_id: {
                    "value": value,
                    "parameters": sorted(
                        name
                        for name, parameter_value in case.parameters.items()
                        if _typed_equal(parameter_value, value)
                    ),
                }
                for case in cases
            }
        }

    def find_response_fields_by_value(
        self,
        *,
        case_ids: list[str],
        value: Any,
    ) -> dict[str, Any]:
        """Find retained response fields matching one exact typed value.

        Args:
            case_ids: Run-local ``TC*`` references to search.
            value: A JSON-like value compared without bool/integer coercion.

        Returns:
            Concrete ``body.*`` paths for every match in each retained body.
            A case without a retained body has an empty field list.

        Raises:
            KeyError: A Test Case reference is unknown.
        """
        cases = [self._require_case(case_id) for case_id in case_ids]
        return {
            "cases": {
                case.case_id: {
                    "value": value,
                    "fields": sorted(
                        path
                        for path, field_value in _response_fields(
                            case.response_body
                        )
                        if _typed_equal(field_value, value)
                    ),
                }
                for case in cases
            }
        }

    def get_failure_messages(
        self,
        *,
        case_ids: list[str],
    ) -> dict[str, Any]:
        """Return parsed Failure messages for exact Test Case references.

        Args:
            case_ids: Run-local ``TC*`` references to inspect.

        Returns:
            Each case's parsed messages. A successful case or another attempt
            without Failure evidence has an empty list.

        Raises:
            KeyError: A Test Case reference is unknown.
        """
        cases = [self._require_case(case_id) for case_id in case_ids]
        return {
            "cases": {
                case.case_id: {
                    "messages": (
                        list(case.failure.messages)
                        if case.failure is not None
                        else []
                    )
                }
                for case in cases
            }
        }

    def _require_case(self, case_id: str) -> CatalogTestCase:
        """Reject forged or stale run-local Test Case references."""
        try:
            return self._cases[case_id]
        except KeyError as exc:
            raise KeyError(f"Unknown Test Case: {case_id}") from exc


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
    current = body
    for part in _response_path_parts(path):
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return False, None
            current = current[part]
        else:
            if not isinstance(current, dict) or part not in current:
                return False, None
            current = current[part]
    return True, current


def _response_path_parts(path: str) -> list[str | int]:
    """Validate one concrete response path and return its traversal steps."""
    if path == "body":
        return []
    if not path.startswith("body."):
        raise KeyError(f"Response Field must start with body: {path}")
    return _path_parts(path.removeprefix("body."))


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
