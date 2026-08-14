"""Protect bounded read-only SQL and sensitive response-header projection."""

from __future__ import annotations

import json

import pytest
from jsonschema import validate


def _engine():
    """Create one real in-memory SQLite database for Tool Interface tests."""

    from restscope.db import create_engine_from_url

    engine = create_engine_from_url("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE examples ("
            "id INTEGER PRIMARY KEY, category TEXT, note TEXT, payload BLOB)"
        )
        connection.exec_driver_sql(
            "INSERT INTO examples (id, category, note, payload) VALUES "
            "(1, 'alpha', 'first', x'0001'), "
            "(2, 'alpha', 'second', x'0203'), "
            "(3, 'beta', 'third', x'0405')"
        )
        connection.exec_driver_sql(
            "CREATE TABLE observations ("
            "observation_id TEXT PRIMARY KEY, response_headers TEXT, response_body BLOB)"
        )
        headers = json.dumps(
            {
                "content-type": "application/json",
                "set-cookie": "session=secret",
                "x-api-token": "secret-token",
            }
        )
        connection.exec_driver_sql(
            "INSERT INTO observations VALUES (?, ?, ?)",
            ("OBS1", headers, bytes(range(256)) * 24),
        )
        connection.exec_driver_sql(
            "CREATE TABLE resource_instances ("
            "resource_instance_id TEXT PRIMARY KEY, current_state_json TEXT)"
        )
        connection.exec_driver_sql(
            "INSERT INTO resource_instances VALUES (?, ?)",
            ("RID1", json.dumps({"description": "x" * 5_000})),
        )
        connection.exec_driver_sql(
            "CREATE TABLE scalar_examples ("
            "nullable TEXT, whole_number INTEGER, decimal_number REAL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO scalar_examples VALUES (NULL, 7, 2.5)"
        )
    return engine


def _backend():
    """Return the public backend over the shared test database."""

    from restscope.tools.database import DatabaseQueryToolBackend

    return DatabaseQueryToolBackend(engine=_engine())


def test_query_reads_arbitrary_tables_with_parameters_and_duplicate_columns() -> None:
    """A caller can join, aggregate, and retain positional duplicate names."""

    backend = _backend()

    aggregate = backend.query(
        sql=(
            "SELECT category, COUNT(*) AS total FROM examples "
            "WHERE id >= :minimum GROUP BY category ORDER BY category"
        ),
        parameters={"minimum": 2},
    )["structured"]
    duplicates = backend.query(
        sql="SELECT id, id FROM examples WHERE id = :id",
        parameters={"id": 1},
    )["structured"]

    assert aggregate == {
        "columns": ["category", "total"],
        "rows": [["alpha", 1], ["beta", 1]],
        "returned_rows": 2,
        "truncated": False,
        "truncation_reasons": [],
    }
    assert duplicates["columns"] == ["id", "id"]
    assert duplicates["rows"] == [[1, 1]]

    scalars = backend.query(
        sql="SELECT nullable, whole_number, decimal_number FROM scalar_examples"
    )["structured"]
    assert scalars["rows"] == [[None, 7, 2.5]]


def test_query_bounds_rows_text_blobs_and_complete_output() -> None:
    """Large values remain useful prefixes and report every active limit."""

    backend = _backend()

    rows = backend.query(
        sql="SELECT note FROM examples ORDER BY id",
        max_rows=2,
    )["structured"]
    payload = backend.query(
        sql="SELECT response_body FROM observations",
    )["structured"]
    resource = backend.query(
        sql="SELECT current_state_json FROM resource_instances",
    )["structured"]

    assert rows["rows"] == [["first"], ["second"]]
    assert rows["truncation_reasons"] == ["row_limit"]
    blob = payload["rows"][0][0]
    assert blob["kind"] == "blob"
    assert blob["size_bytes"] == 256 * 24
    assert blob["truncated"] is True
    assert payload["truncation_reasons"] == ["cell_limit"]
    assert len(resource["rows"][0][0]) == 4_000
    assert resource["truncation_reasons"] == ["cell_limit"]


def test_query_stops_before_the_complete_output_limit() -> None:
    """Many individually valid cells stop at the 24,000-character envelope."""

    result = _backend().query(
        sql=(
            "WITH RECURSIVE rows(value) AS ("
            "VALUES(1) UNION ALL SELECT value + 1 FROM rows WHERE value < 20) "
            "SELECT printf('%04000d', value) AS large_text FROM rows ORDER BY value"
        ),
        max_rows=200,
    )["structured"]

    compact = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
    assert len(compact) <= 24_000
    assert result["returned_rows"] < 20
    assert result["truncation_reasons"] == ["output_limit"]


def test_complete_response_headers_are_verified_and_redacted_after_aliasing() -> None:
    """A direct full-map read keeps ordinary headers and masks secret-bearing ones."""

    result = _backend().query(
        sql=(
            "SELECT response_headers AS headers FROM observations "
            "WHERE observation_id = :observation_id"
        ),
        parameters={"observation_id": "OBS1"},
    )["structured"]

    assert result["columns"] == ["headers"]
    assert json.loads(result["rows"][0][0]) == {
        "content-type": "application/json",
        "set-cookie": "[REDACTED]",
        "x-api-token": "[REDACTED]",
    }


@pytest.mark.parametrize(
    "sql",
    (
        "SELECT response_headers, observation_id FROM observations",
        "SELECT json_extract(response_headers, '$.set-cookie') FROM observations",
        (
            "SELECT json_object('content-type', "
            "json_extract(response_headers, '$.set-cookie')) FROM observations"
        ),
        "SELECT response_headers || '' FROM observations",
        (
            "SELECT response_headers FROM observations "
            "UNION SELECT response_headers || '' FROM observations"
        ),
    ),
)
def test_response_header_derivations_are_rejected(sql: str) -> None:
    """Expressions cannot discard header names and bypass name-based redaction."""

    from restscope.tools import ToolFailure

    with pytest.raises(ToolFailure) as exc_info:
        _backend().query(sql=sql)

    assert exc_info.value.code == "database_query_headers_require_complete_mapping"


@pytest.mark.parametrize(
    "sql",
    (
        "INSERT INTO examples VALUES (4, 'gamma', 'fourth', x'00')",
        "UPDATE examples SET note = 'changed' WHERE id = 1",
        "DELETE FROM examples WHERE id = 1",
        "CREATE TABLE added (id INTEGER)",
        "DROP TABLE examples",
        "ALTER TABLE examples ADD COLUMN extra TEXT",
        "PRAGMA table_info(examples)",
        "ATTACH DATABASE ':memory:' AS attached",
        "DETACH DATABASE attached",
        "BEGIN",
    ),
)
def test_sqlite_authorizer_rejects_every_non_read_statement(sql: str) -> None:
    """Compiled database actions, including disguised lifecycle work, cannot run."""

    from restscope.tools import ToolFailure

    backend = _backend()
    with pytest.raises(ToolFailure) as exc_info:
        backend.query(sql=sql)
    assert exc_info.value.code in {"database_query_read_only", "database_query_invalid"}

    unchanged = backend.query(sql="SELECT note FROM examples WHERE id = 1")[
        "structured"
    ]
    assert unchanged["rows"] == [["first"]]


def test_extension_loading_and_multiple_statements_fail_safely() -> None:
    """A read-looking extension call and statement chaining expose no side effect."""

    from restscope.tools import ToolFailure

    backend = _backend()
    with pytest.raises(ToolFailure) as extension_error:
        backend.query(sql="SELECT load_extension('unsafe')")
    assert extension_error.value.code == "database_query_extension_denied"

    with pytest.raises(ToolFailure) as multiple_error:
        backend.query(sql="SELECT 1; DELETE FROM examples")
    assert multiple_error.value.code == "database_query_invalid"


def test_runaway_recursive_query_times_out_and_callbacks_are_removed(monkeypatch) -> None:
    """Timeout cleanup leaves the pooled connection usable for later reads and writes."""

    from restscope.tools import ToolFailure
    from restscope.tools.database import query as query_module

    backend = _backend()
    monkeypatch.setattr(query_module, "_QUERY_TIMEOUT_SECONDS", 0.001)
    with pytest.raises(ToolFailure) as exc_info:
        backend.query(
            sql=(
                "WITH RECURSIVE counter(value) AS ("
                "VALUES(1) UNION ALL SELECT value + 1 FROM counter) "
                "SELECT max(value) FROM counter"
            )
        )
    assert exc_info.value.code == "database_query_timeout"

    assert backend.query(sql="SELECT COUNT(*) FROM examples")["structured"]["rows"] == [
        [3]
    ]


def test_database_tool_contract_validates_its_successful_result() -> None:
    """The global JSON Schemas close both model boundaries and accept real output."""

    from restscope.tools.database import database_query_tool_spec

    spec = database_query_tool_spec()
    result = _backend().query(sql="SELECT id, payload FROM examples ORDER BY id")

    assert spec.strict is True
    assert spec.input_schema["properties"]["sql"]["maxLength"] == 8_000
    assert spec.input_schema["properties"]["parameters"]["maxProperties"] == 100
    assert spec.input_schema["properties"]["max_rows"]["maximum"] == 200
    validate(result["structured"], spec.output_schema)
