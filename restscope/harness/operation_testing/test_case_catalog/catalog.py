"""Store and query Test Cases for one Operation Smoke run.

The Catalog receives every Batch case and every HTTP request actually attempted
by Resolution. It assigns compact identities, keeps request values plus bounded
failure evidence, and answers exact typed queries. The whole Module is
in-memory: the Coordinator drops it when the operation's Smoke run ends.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from restscope.operation_references import RequestInputReference

from .schemas import CatalogTestCase, CatalogTestCaseDraft


class TestCaseCatalog:
    """Hide run-local Test Case identity, storage, and lookup behind one Interface."""

    def __init__(
        self,
        *,
        input_references: Iterable[RequestInputReference],
    ) -> None:
        """Create an empty Catalog for one operation.

        Args:
            input_references: Trusted OpenAPI request-input references. They
                distinguish valid-but-absent input from a forged handle and
                own handle-to-request-JSON traversal for every Catalog query.
        """
        self._input_references = {
            item.handle: item for item in input_references
        }
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

        The set is never added to the initial Prompt. Worklist writes use it
        only to reject invented semantic handles.
        """
        return frozenset(self._input_references)

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
            One result per Test Case. A used Parameter includes direct-name
            request JSON for the selected input. An unused Parameter has the
            explicit terminal status
            ``parameter_not_used_in_request`` and omits ``request``.

        Raises:
            KeyError: A Test Case or Parameter was not issued for this
                operation.
        """
        cases = [self._require_case(case_id) for case_id in case_ids]
        results: dict[str, Any] = {}
        for case in cases:
            reference = self._input_references.get(parameter)
            if reference is None:
                raise KeyError(f"Unknown Parameter: {parameter}")
            present, _ = reference.read(case.request)
            if not present:
                results[case.case_id] = {
                    "parameter": parameter,
                    "status": "parameter_not_used_in_request",
                }
                continue
            results[case.case_id] = {
                "parameter": parameter,
                "status": "parameter_used_in_request",
                "request": reference.fragment(case.request),
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
            only a present field includes a structured ``response`` fragment.

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
                "response": _response_fragment(case.response_body, field),
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
            Matching semantic handles and direct-name request JSON fragments.

        Raises:
            KeyError: A Test Case reference is unknown.
        """
        cases = [self._require_case(case_id) for case_id in case_ids]
        results: dict[str, Any] = {}
        for case in cases:
            matches = []
            for name, reference in sorted(self._input_references.items()):
                present, parameter_value = reference.read(case.request)
                if present and _typed_equal(parameter_value, value):
                    matches.append(
                        {
                            "parameter": name,
                            "request": reference.fragment(case.request),
                        }
                    )
            results[case.case_id] = {"value": value, "matches": matches}
        return {"cases": results}

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
            Concrete ``body.*`` paths and structured response fragments for
            every match. A case without a match has an empty ``matches`` list.

        Raises:
            KeyError: A Test Case reference is unknown.
        """
        cases = [self._require_case(case_id) for case_id in case_ids]
        return {
            "cases": {
                case.case_id: {
                    "value": value,
                    "matches": [
                        {
                            "field": path,
                            "response": _response_fragment(
                                case.response_body,
                                path,
                            ),
                        }
                        for path, field_value in sorted(
                            _response_fields(case.response_body),
                            key=lambda item: item[0],
                        )
                        if _typed_equal(field_value, value)
                    ],
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


def _response_fragment(body: Any, path: str) -> dict[str, Any]:
    """Project one present response field as trustworthy JSON ancestry.

    Object-only paths keep just the selected ancestry. When a path enters an
    array, the fragment stops narrowing at that array and retains the complete
    real container so it never fabricates values for omitted indices.
    """
    parts = _response_path_parts(path)
    first_array = next(
        (index for index, part in enumerate(parts) if isinstance(part, int)),
        None,
    )
    selected = parts if first_array is None else parts[:first_array]
    current = body
    for part in selected:
        assert isinstance(part, str)
        current = current[part]
    for part in reversed(selected):
        assert isinstance(part, str)
        current = {part: current}
    return {"body": current}


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
