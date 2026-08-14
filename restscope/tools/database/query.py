"""Run bounded read-only SQL against the App-owned SQLite database.

This Tool Module receives the production SQLAlchemy Engine and exposes one
model-callable behavior: execute a parameterized read query and return a small,
position-preserving result. It sits after Profile authorization in the Harness
flow. The Module owns SQLite write denial, query timeout, response-header
redaction, binary encoding, and every Agent-visible output limit.
"""

from __future__ import annotations

import base64
import json
import re
import sqlite3
import time
from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError

from restscope.llm import ToolSpec
from restscope.target_api.request import is_sensitive_header
from restscope.tools.runtime import ToolBinding, ToolFailure

DATABASE_QUERY_TOOL_NAME = "database.query"

_DEFAULT_MAX_ROWS = 50
_MAX_ROWS = 200
_MAX_SQL_CHARACTERS = 8_000
_MAX_PARAMETER_COUNT = 100
_MAX_TEXT_CHARACTERS = 4_000
_MAX_BLOB_BYTES = 4 * 1024
_MAX_OUTPUT_CHARACTERS = 24_000
_QUERY_TIMEOUT_SECONDS = 1.0
_PROGRESS_CHECK_INSTRUCTIONS = 1_000
_REDACTED = "[REDACTED]"

_ALLOWED_SQLITE_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
        sqlite3.SQLITE_RECURSIVE,
    }
)
_TRUNCATION_ORDER = ("row_limit", "cell_limit", "output_limit")

_SQL_IDENTIFIER = r'(?:[A-Za-z_][A-Za-z0-9_]*|"[^"]+"|`[^`]+`|\[[^\]]+\])'
_HEADER_COLUMN = r'(?:response_headers|"response_headers"|`response_headers`|\[response_headers\])'
_OBSERVATIONS_TABLE = r'(?:observations|"observations"|`observations`|\[observations\])'
_MAIN_SCHEMA = r'(?:main|"main"|`main`|\[main\])'
_DIRECT_HEADER_QUERY = re.compile(
    rf"^\s*SELECT\s+"
    rf"(?:(?:{_SQL_IDENTIFIER})\s*\.\s*)?{_HEADER_COLUMN}"
    rf"(?:\s+(?:AS\s+)?{_SQL_IDENTIFIER})?\s+"
    rf"FROM\s+(?:(?:{_MAIN_SCHEMA})\s*\.\s*)?{_OBSERVATIONS_TABLE}"
    rf"(?=\s|$)",
    re.IGNORECASE,
)
_SQL_NON_CODE = re.compile(
    r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|`(?:``|[^`])*`|\[[^\]]*\]"
    r"|--[^\r\n]*|/\*.*?\*/",
    re.DOTALL,
)
_SQL_SET_OPERATOR = re.compile(r"\b(?:UNION|INTERSECT|EXCEPT)\b", re.IGNORECASE)

SQLParameter = str | int | float | bool | None
DatabaseCell = SQLParameter | dict[str, object]


@dataclass
class _SQLiteQueryGuard:
    """Track authorization and timeout facts for one checked-out connection.

    The SQLite callback cannot raise a useful model-facing exception. It
    records the reason here and returns ``SQLITE_DENY``; the Tool translates the
    resulting database error after SQLite stops the statement.
    """

    deadline: float
    denial_code: str | None = None
    timed_out: bool = False
    read_response_headers: bool = False

    def authorize(
        self,
        action: int,
        first_argument: str | None,
        second_argument: str | None,
        _database_name: str | None,
        _trigger_name: str | None,
    ) -> int:
        """Allow only read-query bytecode and remember sensitive source reads."""

        if action == sqlite3.SQLITE_READ:
            if first_argument == "observations" and second_argument == "response_headers":
                self.read_response_headers = True
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION:
            function_name = (second_argument or first_argument or "").lower()
            if function_name == "load_extension":
                self.denial_code = "database_query_extension_denied"
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action in _ALLOWED_SQLITE_ACTIONS:
            return sqlite3.SQLITE_OK
        self.denial_code = "database_query_read_only"
        return sqlite3.SQLITE_DENY

    def check_progress(self) -> int:
        """Interrupt SQLite after the fixed wall-clock query allowance."""

        if time.monotonic() < self.deadline:
            return 0
        self.timed_out = True
        return 1


class DatabaseQueryToolBackend:
    """Execute safe database reads through one deep Tool Interface.

    Args:
        engine: The App-owned SQLAlchemy Engine. It must use SQLite; this Tool
            deliberately has no external-database or connection-string mode.

    The backend changes no database state. It temporarily installs SQLite
    callbacks only while its checked-out connection executes this call, then
    removes them before returning the connection to SQLAlchemy's pool.
    """

    def __init__(self, *, engine: Engine) -> None:
        """Retain the existing SQLite Engine without opening a connection."""

        if engine.dialect.name != "sqlite":
            raise ValueError("database.query requires the App-owned SQLite Engine")
        self._engine = engine

    def query(
        self,
        *,
        sql: str,
        parameters: dict[str, SQLParameter] | None = None,
        max_rows: int = _DEFAULT_MAX_ROWS,
    ) -> dict[str, object]:
        """Execute one bounded read query and return positional rows.

        Args:
            sql: One SQLite read statement. The Tool Schema limits its length;
                SQLite authorization, rather than string-prefix inspection,
                decides whether the compiled statement is read only.
            parameters: Optional named scalar values for ``:name`` placeholders.
            max_rows: Maximum rows to return. One extra row is fetched only to
                report that the caller should refine or paginate the query.

        Returns:
            A Tool-shaped structured result containing ordered column names,
            positional rows, and explicit truncation facts.

        Raises:
            ToolFailure: The statement writes, times out, is invalid, returns
                unsupported values, or handles response headers unsafely.
        """

        with self._engine.connect() as connection:
            driver_connection = connection.connection.driver_connection
            if not isinstance(driver_connection, sqlite3.Connection):
                raise ToolFailure(
                    code="database_query_backend_invalid",
                    message="The database query backend is not a SQLite connection",
                )
            guard = _SQLiteQueryGuard(
                deadline=time.monotonic() + _QUERY_TIMEOUT_SECONDS
            )
            driver_connection.set_authorizer(guard.authorize)
            driver_connection.set_progress_handler(
                guard.check_progress,
                _PROGRESS_CHECK_INSTRUCTIONS,
            )
            try:
                columns, raw_rows, row_limit_reached = self._execute(
                    connection=connection,
                    sql=sql,
                    parameters=parameters or {},
                    max_rows=max_rows,
                    guard=guard,
                )
                if guard.read_response_headers:
                    raw_rows = _verify_and_redact_response_headers(
                        connection=connection,
                        sql=sql,
                        columns=columns,
                        rows=raw_rows,
                        guard=guard,
                    )
                structured = _project_bounded_result(
                    columns=columns,
                    rows=raw_rows,
                    row_limit_reached=row_limit_reached,
                )
                return {"structured": structured}
            finally:
                # Pool reuse must not inherit this Agent call's security state or
                # deadline. Cleanup happens for success, rejection, and timeout.
                driver_connection.set_progress_handler(None, 0)
                driver_connection.set_authorizer(None)

    @staticmethod
    def _execute(
        *,
        connection: object,
        sql: str,
        parameters: dict[str, SQLParameter],
        max_rows: int,
        guard: _SQLiteQueryGuard,
    ) -> tuple[list[str], list[tuple[object, ...]], bool]:
        """Compile and execute one statement, translating SQLite failures safely."""

        # ``connection`` is supplied by SQLAlchemy above. Keeping this helper
        # private avoids publishing a second database seam merely for tests.
        from sqlalchemy.engine import Connection

        if not isinstance(connection, Connection):
            raise TypeError("database.query requires a SQLAlchemy Connection")
        try:
            result = connection.exec_driver_sql(sql, parameters)
            if not result.returns_rows:
                raise ToolFailure(
                    code="database_query_read_only",
                    message="database.query accepts only statements that return rows",
                )
            columns = list(map(str, result.keys()))
            fetched = [tuple(row) for row in result.fetchmany(max_rows + 1)]
            return columns, fetched[:max_rows], len(fetched) > max_rows
        except ToolFailure:
            raise
        except DBAPIError as exc:
            if guard.timed_out:
                raise ToolFailure(
                    code="database_query_timeout",
                    message="The database query exceeded 1 second",
                    status="timed_out",
                ) from exc
            if guard.denial_code == "database_query_extension_denied":
                raise ToolFailure(
                    code=guard.denial_code,
                    message="SQLite extension loading is not allowed",
                ) from exc
            if guard.denial_code == "database_query_read_only":
                raise ToolFailure(
                    code=guard.denial_code,
                    message="database.query accepts read-only SQLite queries",
                ) from exc
            raise ToolFailure(
                code="database_query_invalid",
                message="The SQLite query or its named parameters are invalid",
            ) from exc


def _verify_and_redact_response_headers(
    *,
    connection: object,
    sql: str,
    columns: list[str],
    rows: list[tuple[object, ...]],
    guard: _SQLiteQueryGuard,
) -> list[tuple[object, ...]]:
    """Require complete stored header maps, then mask sensitive header values.

    Arbitrary SQL can erase source-column provenance. A query that reads
    ``observations.response_headers`` must therefore return exactly one column,
    and every non-null value must equal a complete mapping stored in that
    column. This accepts filters, ordering, pagination, and aliases while
    rejecting scalar extraction or JSON relabeling that could bypass redaction.
    """

    from sqlalchemy.engine import Connection

    if not isinstance(connection, Connection):
        raise TypeError("response-header verification requires a SQLAlchemy Connection")
    if (
        _DIRECT_HEADER_QUERY.match(sql) is None
        or _SQL_SET_OPERATOR.search(_SQL_NON_CODE.sub(" ", sql)) is not None
        or len(columns) != 1
        or any(len(row) != 1 for row in rows)
    ):
        raise _unsafe_header_query()
    candidates = {row[0] for row in rows if row[0] is not None}
    if any(not isinstance(value, str) for value in candidates):
        raise _unsafe_header_query()
    stored: set[str] = set()
    candidate_strings = sorted(value for value in candidates if isinstance(value, str))
    if candidate_strings:
        placeholders = ",".join("?" for _value in candidate_strings)
        statement = (
            "SELECT response_headers FROM observations "
            f"WHERE response_headers IN ({placeholders})"
        )
        try:
            result = connection.exec_driver_sql(statement, tuple(candidate_strings))
            stored = {row[0] for row in result if isinstance(row[0], str)}
        except DBAPIError as exc:
            if guard.timed_out:
                raise ToolFailure(
                    code="database_query_timeout",
                    message="The database query exceeded 1 second",
                    status="timed_out",
                ) from exc
            raise ToolFailure(
                code="database_query_header_verification_failed",
                message="Stored response headers could not be verified safely",
            ) from exc
    if set(candidate_strings) != stored:
        raise _unsafe_header_query()
    return [
        (None,) if row[0] is None else (_redacted_header_json(row[0]),)
        for row in rows
    ]


def _unsafe_header_query() -> ToolFailure:
    """Return the correctable failure for a provenance-losing header query."""

    return ToolFailure(
        code="database_query_headers_require_complete_mapping",
        message=(
            "Read observations.response_headers as the only selected column; "
            "scalar extraction, derived JSON, and mixed projections are unsafe"
        ),
    )


def _redacted_header_json(source: object) -> str:
    """Parse one complete stored header mapping and serialize its safe view."""

    if not isinstance(source, str):
        raise _unsafe_header_query()
    try:
        parsed = json.loads(source)
    except json.JSONDecodeError as exc:
        raise _unsafe_header_query() from exc
    if not isinstance(parsed, dict) or any(
        not isinstance(name, str) or not isinstance(value, str)
        for name, value in parsed.items()
    ):
        raise _unsafe_header_query()
    redacted = {
        name: _REDACTED if is_sensitive_header(name) else value
        for name, value in parsed.items()
    }
    return json.dumps(redacted, ensure_ascii=False, separators=(",", ":"))


def _project_bounded_result(
    *,
    columns: list[str],
    rows: list[tuple[object, ...]],
    row_limit_reached: bool,
) -> dict[str, object]:
    """Normalize SQLite values while keeping the complete result under 24K."""

    projected_rows: list[list[DatabaseCell]] = []
    reasons: set[str] = {"row_limit"} if row_limit_reached else set()
    for row in rows:
        projected_row: list[DatabaseCell] = []
        row_cell_truncated = False
        for value in row:
            projected_value, cell_truncated = _project_cell(value)
            projected_row.append(projected_value)
            row_cell_truncated = row_cell_truncated or cell_truncated
        candidate_reasons = set(reasons)
        if row_cell_truncated:
            candidate_reasons.add("cell_limit")
        candidate = _result_payload(
            columns=columns,
            rows=[*projected_rows, projected_row],
            reasons=candidate_reasons,
        )
        if _json_size(candidate) > _MAX_OUTPUT_CHARACTERS:
            reasons.add("output_limit")
            break
        projected_rows.append(projected_row)
        reasons = candidate_reasons

    result = _result_payload(columns=columns, rows=projected_rows, reasons=reasons)
    if _json_size(result) > _MAX_OUTPUT_CHARACTERS:
        raise ToolFailure(
            code="database_query_output_too_large",
            message="Database query column metadata exceeds the output limit",
        )
    return result


def _project_cell(value: object) -> tuple[DatabaseCell, bool]:
    """Convert one SQLite value to the closed Tool cell contract."""

    if value is None or isinstance(value, bool | int | float):
        return value, False
    if isinstance(value, str):
        return value[:_MAX_TEXT_CHARACTERS], len(value) > _MAX_TEXT_CHARACTERS
    if isinstance(value, bytes):
        prefix = value[:_MAX_BLOB_BYTES]
        truncated = len(value) > len(prefix)
        return (
            {
                "kind": "blob",
                "base64": base64.b64encode(prefix).decode("ascii"),
                "size_bytes": len(value),
                "truncated": truncated,
            },
            truncated,
        )
    raise ToolFailure(
        code="database_query_value_unsupported",
        message="SQLite returned a value outside the supported scalar and BLOB types",
    )


def _result_payload(
    *,
    columns: list[str],
    rows: list[list[DatabaseCell]],
    reasons: set[str],
) -> dict[str, object]:
    """Build one stable result envelope from already projected values."""

    ordered_reasons = [reason for reason in _TRUNCATION_ORDER if reason in reasons]
    return {
        "columns": columns,
        "rows": rows,
        "returned_rows": len(rows),
        "truncated": bool(ordered_reasons),
        "truncation_reasons": ordered_reasons,
    }


def _json_size(value: dict[str, object]) -> int:
    """Measure the exact compact model-visible JSON character count."""

    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def database_query_tool_spec() -> ToolSpec:
    """Return the complete global contract for one SQLite read query."""

    scalar_schema: dict[str, object] = {
        "type": ["string", "number", "boolean", "null"]
    }
    blob_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "const": "blob"},
            "base64": {"type": "string"},
            "size_bytes": {"type": "integer", "minimum": 0},
            "truncated": {"type": "boolean"},
        },
        "required": ["kind", "base64", "size_bytes", "truncated"],
        "additionalProperties": False,
    }
    return ToolSpec(
        name=DATABASE_QUERY_TOOL_NAME,
        description=(
            "Run one bounded parameterized read-only SQL query against the current "
            "RESTScope SQLite evidence database."
        ),
        kind="local_function",
        input_schema={
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": _MAX_SQL_CHARACTERS,
                },
                "parameters": {
                    "type": "object",
                    "description": (
                        "Named scalar values used by :name placeholders in the SQL."
                    ),
                    "maxProperties": _MAX_PARAMETER_COUNT,
                    "propertyNames": {
                        "pattern": "^[A-Za-z_][A-Za-z0-9_]*$"
                    },
                    "additionalProperties": scalar_schema,
                },
                "max_rows": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": _MAX_ROWS,
                    "default": _DEFAULT_MAX_ROWS,
                },
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "rows": {
                    "type": "array",
                    "maxItems": _MAX_ROWS,
                    "items": {
                        "type": "array",
                        "items": {"anyOf": [scalar_schema, blob_schema]},
                    },
                },
                "returned_rows": {"type": "integer", "minimum": 0},
                "truncated": {"type": "boolean"},
                "truncation_reasons": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {
                        "type": "string",
                        "enum": ["row_limit", "cell_limit", "output_limit"],
                    },
                },
            },
            "required": [
                "columns",
                "rows",
                "returned_rows",
                "truncated",
                "truncation_reasons",
            ],
            "additionalProperties": False,
        },
        strict=True,
    )


def database_query_tool_binding(backend: DatabaseQueryToolBackend) -> ToolBinding:
    """Bind the global Tool name to one App-owned SQLite backend."""

    return ToolBinding(name=DATABASE_QUERY_TOOL_NAME, execute=backend.query)
